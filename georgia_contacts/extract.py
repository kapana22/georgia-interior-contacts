"""Conservative HTML / JSON-LD contact extraction with value-level provenance."""
import json
import re
from urllib.parse import parse_qs, unquote, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup
import phonenumbers

from .urls import canonical_url, domain

EMAIL = re.compile(r"[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9](?:[A-Z0-9.-]*[A-Z0-9])?\.[A-Z]{2,63}", re.I)
PLACEHOLDERS = {"example.com", "example.org", "example.net", "mysite.com", "domain.com", "email.com", "yourdomain.com"}
CITIES = {"თბილისი": ("თბილისი", "თბილისში", "tbilisi", "тбилиси"),
          "ბათუმი": ("ბათუმი", "ბათუმში", "batumi", "батуми"),
          "ქუთაისი": ("ქუთაისი", "ქუთაისში", "kutaisi", "кутаиси"),
          "რუსთავი": ("რუსთავი", "rustavi", "рустави"),
          "გორი": ("გორი", "gori", "гори"),
          "თელავი": ("თელავი", "telavi", "телави"),
          "ზუგდიდი": ("ზუგდიდი", "zugdidi", "зугдиди"),
          "ფოთი": ("ფოთი", "poti", "поти"),
          "ქობულეთი": ("ქობულეთი", "kobuleti", "кобулети"),
          "მცხეთა": ("მცხეთა", "mtskheta", "мцхета"),
          "ოზურგეთი": ("ოზურგეთი", "ozurgeti", "озургети")}
RELEVANT = re.compile(r"ინტერიერ\w*\s+დიზაინ|interior\s+(?:design|architecture)|дизайн\w*\s+интерьер", re.I)
LINK_WORDS = re.compile(r"contact|about|service|studio|team|people|kontakti|chven|კონტაქტ|შესახებ|სერვის|მომსახურ|გუნდი|თანამშრომლ|контакт|о-нас|услуг|команда", re.I)


def normalize_phone(raw):
    raw = unquote(raw).removeprefix("tel:").split(";")[0].strip()
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("00995"):
        raw = "+" + digits[2:]
    elif digits.startswith("995") and len(digits) == 12:
        raw = "+" + digits
    elif digits.startswith("0") and len(digits) == 10:
        raw = "+995" + digits[1:]
    elif len(digits) == 9 and digits[0] in "3457":
        raw = "+995" + digits
    elif not raw.startswith("+"):
        return ""  # No area code guessing for seven-digit local numbers.
    try:
        number = phonenumbers.parse(raw, None)
        if phonenumbers.is_valid_number(number):
            return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
    except phonenumbers.NumberParseException:
        pass
    return ""


def social_url(value):
    url = canonical_url(value)
    if not url:
        return None
    p, host = urlsplit(url), domain(url)
    path = p.path.rstrip("/").lower()
    if not path or path.lower().split("/")[1] in {"sharer", "sharer.php", "share", "dialog", "login", "accounts", "plugins", "p", "reel", "reels", "stories"}:
        return None
    if host in {"instagram.com", "m.instagram.com"}:
        return "instagram", "https://www.instagram.com" + path
    if host in {"facebook.com", "m.facebook.com", "web.facebook.com", "fb.com", "fb.me"}:
        query = ""
        if path == "/profile.php":
            ids = parse_qs(p.query).get("id")
            if not ids or not ids[0].isdigit():
                return None
            query = "id=" + ids[0]
        return "facebook", urlunsplit(("https", "www.facebook.com" if host not in {"fb.me"} else host, path, query, ""))
    return None


def organizations(value):
    if isinstance(value, list):
        for item in value:
            yield from organizations(item)
    elif isinstance(value, dict):
        types = value.get("@type", [])
        types = [types] if isinstance(types, str) else types
        if ("Person" in types and RELEVANT.search(str(value.get("jobTitle", "")))) or any(t in {"Organization", "LocalBusiness", "ProfessionalService", "HomeAndConstructionBusiness", "InteriorDesignBusiness", "Architect", "Corporation"} for t in types):
            yield value
        # Do not scrape arbitrary Person, author, reviewer or publisher contacts.
        for key in ("@graph", "mainEntity"):
            yield from organizations(value.get(key))


def extract(html, url, timestamp, seed_name=""):
    soup = BeautifulSoup(html, "html.parser")
    facts, reasons, orgs = [], [], []

    def add(kind, value):
        if value and not any(f["kind"] == kind and f["value"] == value for f in facts):
            facts.append({"kind": kind, "value": value, "source_url": url,
                          "first_seen": timestamp, "last_seen": timestamp})

    def email(raw):
        raw = unquote(str(raw))
        if raw.lower().startswith("mailto:"):
            raw = raw[7:].split("?")[0]
        for item in EMAIL.findall(raw):
            item = item.lower().strip(".")
            if item.split("@")[1] not in PLACEHOLDERS and not item.endswith((".png", ".jpg", ".webp", ".svg")):
                add("email", item)

    def phone(raw, explicit=True):
        value = normalize_phone(str(raw))
        if value:
            add("phone", value)
        elif explicit and str(raw).strip():
            add("phone_unverified", str(raw).strip())
            reasons.append("unverified_phone_structure")

    def social(raw):
        result = social_url(str(raw))
        if result:
            add(*result)

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            orgs.extend(organizations(json.loads(script.string or script.get_text())))
        except (ValueError, TypeError):
            reasons.append("malformed_json_ld")
    # Narrow JSON-LD organizations to this website when an explicit URL is supplied.
    orgs = [o for o in orgs if not isinstance(o.get("url"), str) or domain(o["url"]) == domain(url)]
    for org in orgs:
        for key, consumer in (("email", email), ("telephone", phone), ("sameAs", social)):
            vals = org.get(key, [])
            for val in vals if isinstance(vals, list) else [vals]:
                if isinstance(val, str):
                    consumer(val)
        points = org.get("contactPoint", [])
        for point in points if isinstance(points, list) else [points]:
            if isinstance(point, dict):
                if point.get("email"):
                    email(point["email"])
                if point.get("telephone"):
                    phone(point["telephone"])
        addresses = org.get("address", [])
        for address in addresses if isinstance(addresses, list) else [addresses]:
            if isinstance(address, dict) and isinstance(address.get("addressLocality"), str):
                locality = address["addressLocality"].strip()
                add("city", next((name for name, variants in CITIES.items() if locality.casefold() in variants), locality))
    meta = soup.find("meta", attrs={"property": "og:site_name"})
    structured_name = next((o["name"] for o in orgs if isinstance(o.get("name"), str)), "")
    name = structured_name or (meta.get("content", "") if meta else "") or seed_name
    if not name:
        name = soup.title.get_text(" ", strip=True) if soup.title else ""
    if name:
        add("name", name[:250])
    add("website", url)
    robots = soup.find("meta", attrs={"name": re.compile(r"^(robots|GeorgiaInteriorContacts)$", re.I)})
    if robots and any(x in robots.get("content", "").lower() for x in ("none", "noindex", "nofollow")):
        return {"facts": [], "links": [], "text": "", "name": "", "reasons": ["meta_robots_opt_out"], "blocked": True}
    for node in soup.select("script, style, template, noscript, [hidden], [aria-hidden='true']"):
        node.decompose()
    # Avoid obvious site-builder / designer credits in footers.
    for a in list(soup.find_all("a", href=True)):
        if re.search(r"powered by|website by|site by|developed by|ვებგვერდი შექმნა", a.get_text(" ", strip=True), re.I):
            a.decompose()
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.lower().startswith("mailto:"):
            email(href)
        elif href.lower().startswith("tel:"):
            phone(href)
        else:
            target = canonical_url(urljoin(url, href))
            social(target)
            if target and domain(target) == domain(url) and LINK_WORDS.search(unquote(href) + " " + a.get_text(" ", strip=True)):
                if "nofollow" not in a.get("rel", []):
                    links.append(target)
    for node in soup.select('[itemprop="telephone"]'):
        phone(node.get("content") or node.get_text(" ", strip=True))
    for node in soup.select('[itemprop="email"]'):
        email(node.get("content") or node.get_text(" ", strip=True))
    for node in soup.select('[itemprop="addressLocality"]'):
        add("city", node.get("content") or node.get_text(" ", strip=True))
    text = soup.get_text("\n", strip=True)
    if re.search(r"(?:scraping|automated access|automated collection) (?:is |are )?(?:prohibited|forbidden|not permitted)", text, re.I):
        return {"facts": [], "links": [], "text": "", "name": "", "reasons": ["access_policy_opt_out"], "blocked": True}
    email(text)
    for line in text.splitlines():
        for match in phonenumbers.PhoneNumberMatcher(line, "GE"):
            # Georgian nine-digit national numbers or explicit international form only.
            phone(match.raw_string, explicit=False)
    # City from address/contact context, not project titles or a guessed phone area code.
    address_texts = [x.get_text(" ", strip=True) for x in soup.select("address, footer, [itemprop='address']")]
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if re.search(r"მისამართ|address|based in|located in|адрес", line, re.I):
            address_texts.append(" ".join(lines[i:i + 3]))
    for name_city, variants in CITIES.items():
        if any(re.search(r"(?<!\w)" + re.escape(v) + r"(?!\w)", block.casefold()) for block in address_texts for v in variants):
            add("city", name_city)
    if len({f["value"] for f in facts if f["kind"] == "city"}) > 1:
        reasons.append("multiple_cities")
    if re.search(r"არ არის რეალური კომპანია|demo (?:company|project)|fictional (?:company|business)", text, re.I):
        reasons.append("demo_or_fictional_business")
    return {"facts": facts, "name": name[:250], "links": list(dict.fromkeys(links)),
            "text": text, "reasons": sorted(set(reasons)), "blocked": False}


def assess(text, facts, reasons=()):
    reasons = set(reasons)
    if not RELEVANT.search(text):
        reasons.add("interior_service_not_confirmed")
    if not (re.search(r"საქართველ|\bgeorgia\b|грузи", text, re.I) or any(f["kind"] == "city" and f["value"] in CITIES for f in facts) or any(f["kind"] == "phone" and f["value"].startswith("+995") for f in facts)):
        reasons.add("georgia_operation_not_confirmed")
    if not any(f["kind"] in {"email", "phone", "instagram", "facebook"} for f in facts):
        reasons.add("no_contacts_found")
    return sorted(reasons)
