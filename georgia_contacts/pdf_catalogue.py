"""Text PDF catalogues with reviewed page pairs and explicit historical evidence."""
import hashlib
import io
import re
from html import escape

from .directories import city_facts
from .extract import extract, RELEVANT
from .network import FetchError
from .urls import canonical_url, domain, SOCIAL_HOSTS


def catalogue_candidates(pages, source, timestamp, document_date='', events=None):
    results = []
    events = events if events is not None else []
    for entry in source.get('entries', []):
        description_page, contact_page = entry['description_page'], entry['contact_page']
        evidence = source['url'] + '#page=' + str(contact_page)
        if not (1 <= description_page <= len(pages) and 1 <= contact_page <= len(pages)):
            events.append({'url': evidence, 'status': 'error', 'reason': 'pdf_page_mapping_changed'})
            continue
        description, contact = pages[description_page - 1], pages[contact_page - 1]
        # This heading appears on EVERY page, including unrelated engineering firms.
        description = re.sub(r'(?im)^\s*Architecture and Interior Design\s*$', '', description)
        heading = ' '.join(entry['expected_heading'].casefold().split())
        if heading not in ' '.join(description.casefold().split()) or 'CONTACT INFORMATION' not in contact:
            events.append({'url': evidence, 'status': 'error', 'reason': 'pdf_page_mapping_changed'})
            continue
        websites = []
        for value in re.findall(r'\bwww\.\s*([A-Z0-9][A-Z0-9.-]+\.[A-Z]{2,}(?:/[^\s,;]*)?)', contact, re.I):
            target = canonical_url('https://www.' + value.rstrip('.'))
            if target and domain(target) not in SOCIAL_HOSTS:
                websites.append(target)
        websites = list(dict.fromkeys(websites))
        relevant = bool(RELEVANT.search(description)) and not entry.get('exclude_contacts')
        if not relevant:
            # No architecture-only firm's contacts are collected from this document.
            results.extend({'url': u, 'name': entry['name'], 'discovered_from': evidence} for u in websites)
            events.append({'url': evidence, 'status': 'skipped', 'reason': 'pdf_interior_service_not_confirmed'})
            continue
        parsed = extract(''.join('<p>' + escape(line) + '</p>' for line in contact.splitlines()),
                         evidence, timestamp, entry['name'])
        facts = [f for f in parsed['facts'] if f['kind'] != 'website']
        facts.extend(city_facts(contact, evidence, timestamp))
        facts.extend({'kind': 'website', 'value': u, 'source_url': evidence,
                      'first_seen': timestamp, 'last_seen': timestamp} for u in websites)
        for fact in facts:
            fact['historical_source'] = True
            fact['source_document_created_at'] = document_date
        results.append({'url': websites[0] if websites else source['url'], 'name': entry['name'],
                        'kind': 'website' if websites else 'listing',
                        'business_key': 'pdf:' + hashlib.sha256(source['url'].encode()).hexdigest()[:16] + ':' + str(contact_page),
                        'discovered_from': evidence, 'directory_facts': facts,
                        'directory_service_claim': True,
                        'review_reasons': ['historical_pdf_contacts_require_verification']})
    return results


def read_catalogue(body, source, timestamp, check, events):
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError
    try:
        reader = PdfReader(io.BytesIO(body), strict=True)
        if reader.is_encrypted:
            raise FetchError('blocked', 'encrypted_pdf_not_read')
        if len(reader.pages) > 100:
            raise FetchError('unreachable', 'pdf_page_limit')
        pages = []
        for page in reader.pages:
            check()
            text = page.extract_text() or ''
            if len(text) > 100_000:
                raise FetchError('unreachable', 'pdf_text_too_large')
            pages.append(text)
        if not any(p.strip() for p in pages):
            raise FetchError('unreachable', 'pdf_requires_ocr')
        created = reader.metadata.creation_date if reader.metadata else None
        return catalogue_candidates(pages, source, timestamp, created.isoformat() if created else '', events)
    except FetchError:
        raise
    except (PyPdfError, ValueError, TypeError, KeyError, AttributeError, OSError) as exc:
        raise FetchError('unreachable', 'pdf_parse_' + type(exc).__name__) from None
