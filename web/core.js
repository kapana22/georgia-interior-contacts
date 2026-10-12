export const REPOSITORY = 'kapana22/georgia-interior-contacts';
export const REPO_URL = `https://github.com/${REPOSITORY}`;
export const API = 'https://api.github.com';
export const API_VERSION = '2026-03-10';
export const WORKFLOW = 'scrape.yml';
export const WORKFLOW_URL = `${REPO_URL}/actions/workflows/${WORKFLOW}`;
export const EXPORT_FILES = new Set(['contacts.csv', 'contacts.json', 'changes.csv', 'report.txt', 'report.json']);

export function validateInputs(limit, sources) {
  const numeric = Number(limit);
  if (!String(limit).trim() || !Number.isInteger(numeric) || numeric < 1 || numeric > 1000) {
    throw new Error('ბიზნესების ლიმიტი უნდა იყოს მთელი რიცხვი 1-დან 1000-მდე.');
  }
  const path = String(sources).trim();
  if (!/^config\/[a-zA-Z0-9_/-]+\.json$/.test(path) || path.includes('..') || path.includes('//')) {
    throw new Error('წყაროს ფაილი მიუთითე config/ საქაღალდიდან, მაგალითად config/sources.json.');
  }
  return { limit: String(numeric), sources: path };
}

export function safeURL(value, protocols = ['https:', 'http:']) {
  try {
    const url = new URL(value);
    return protocols.includes(url.protocol) && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}

export function values(record, kind) {
  return [...new Set((record.facts || []).filter(f => f.kind === kind && typeof f.value === 'string').map(f => f.value))];
}

export function filterRecords(records, { query = '', city = '', status = '' } = {}) {
  const needle = query.trim().toLocaleLowerCase('ka-GE');
  return records.filter(record => {
    if (city && !values(record, 'city').includes(city)) return false;
    if (status && record.status !== status) return false;
    const text = [record.name, ...(record.domains || []), ...(record.facts || []).map(f => f.value)].join(' ').toLocaleLowerCase('ka-GE');
    return !needle || text.includes(needle);
  });
}

export function normalizeRecords(payload) {
  if (!Array.isArray(payload)) throw new Error('კონტაქტების ფაილს არასწორი ფორმატი აქვს.');
  return payload.filter(r => r && typeof r.id === 'string' && Array.isArray(r.facts)).map(r => ({
    id: r.id,
    name: typeof r.name === 'string' ? r.name : '',
    domains: Array.isArray(r.domains) ? r.domains.filter(v => typeof v === 'string') : [],
    facts: r.facts.filter(f => f && typeof f.kind === 'string' && typeof f.value === 'string' && typeof f.source_url === 'string'),
    status: r.status === 'review' ? 'review' : 'candidate',
    review_reasons: Array.isArray(r.review_reasons) ? r.review_reasons.filter(v => typeof v === 'string') : [],
    last_seen: typeof r.last_seen === 'string' ? r.last_seen : '',
  }));
}

export function rawURL(ref, file) {
  if (!/^(main|[a-f0-9]{40})$/.test(ref)) throw new Error('Invalid repository reference');
  if (!EXPORT_FILES.has(file) && !/^config\/[a-zA-Z0-9_/-]+\.json$/.test(file)) throw new Error('Invalid repository file');
  return `https://raw.githubusercontent.com/${REPOSITORY}/${ref}/${file}`;
}

export class GitHubClient {
  #token = '';
  constructor(fetcher = fetch) { this.fetcher = (url, options) => fetcher(url, options); }
  disconnect() { this.#token = ''; }
  get connected() { return Boolean(this.#token); }
  async request(path, { method = 'GET', body, authenticated = false, publicOnly = false } = {}) {
    // Only this fixed GitHub origin receives the token, including on redirects.
    if (!path.startsWith('/repos/' + REPOSITORY + '/') && path !== '/user') throw new Error('Unsupported GitHub endpoint');
    if (authenticated && !this.#token) throw new Error('პირდაპირი გაშვებისთვის ჯერ GitHub ავტორიზაცია გაიარე.');
    const headers = { Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': API_VERSION };
    if (this.#token && !publicOnly) headers.Authorization = `Bearer ${this.#token}`;
    if (body) headers['Content-Type'] = 'application/json';
    let response;
    try {
      response = await this.fetcher(API + path, {
        method, headers, body: body ? JSON.stringify(body) : undefined,
        redirect: 'error', credentials: 'omit', cache: 'no-store', signal: AbortSignal.timeout(20000),
      });
    } catch { throw new Error('GitHub-თან კავშირი ვერ მოხერხდა. შეამოწმე ინტერნეტი და სცადე ხელახლა.'); }
    if (!response.ok) {
      if (response.status === 401) throw new Error('GitHub ტოკენი არასწორია ან ვადა გაუვიდა.');
      if (response.status === 403 || response.status === 429) {
        if (response.headers.get('x-ratelimit-remaining') === '0' || response.status === 429) {
          throw new Error('GitHub-ის მოთხოვნების ლიმიტი ამოიწურა. განახლება მოგვიანებით სცადე.');
        }
        throw new Error('წვდომა არ არის. გაშვებისთვის საჭიროა Actions: Read and write, წყაროების შეცვლისთვის — Contents: Read and write.');
      }
      if (response.status === 404) throw new Error('GitHub-ზე ფაილი ან გაშვება ვერ მოიძებნა.');
      if (response.status === 409) throw new Error('წყაროები სხვამ შეცვალა. განაახლე სია და ხელახლა შეიტანე ცვლილება; ძველი ფაილი არ გადაიწერება.');
      if (response.status === 422) throw new Error('GitHub-მა პარამეტრები ვერ მიიღო. შეამოწმე წყაროს ფაილი და workflow.');
      throw new Error(`GitHub დროებით მიუწვდომელია (HTTP ${response.status}).`);
    }
    return response.status === 204 ? null : response.json();
  }
  async connect(token) {
    const candidate = String(token).trim();
    if (!/^[A-Za-z0-9_]+$/.test(candidate) || candidate.length < 20) throw new Error('ჩასვი მოქმედი GitHub personal access token.');
    this.#token = candidate;
    try { return await this.request('/user', { authenticated: true }); }
    catch (error) { this.disconnect(); throw error; }
  }
  async snapshot() {
    const ref = await this.request(`/repos/${REPOSITORY}/git/ref/heads/data`, { publicOnly: true });
    const sha = ref?.object?.sha;
    if (!/^[a-f0-9]{40}$/.test(sha || '')) throw new Error('data ბრენჩის ვერსია ვერ განისაზღვრა.');
    return sha;
  }
  async runs() {
    const result = await this.request(`/repos/${REPOSITORY}/actions/workflows/${WORKFLOW}/runs?per_page=5&branch=main`);
    return result.workflow_runs || [];
  }
  async run(id) {
    if (!/^\d+$/.test(String(id))) throw new Error('Invalid run ID');
    return this.request(`/repos/${REPOSITORY}/actions/runs/${id}`);
  }
  async dispatch(limit, sources) {
    const inputs = validateInputs(limit, sources);
    // POST is sent once. Never retry a dispatch after a network timeout.
    return this.request(`/repos/${REPOSITORY}/actions/workflows/${WORKFLOW}/dispatches`, {
      method: 'POST', body: { ref: 'main', inputs }, authenticated: true,
    });
  }
}

export const REASONS = {
  historical_pdf_contacts_require_verification: 'ძველი PDF კატალოგის კონტაქტები — მიმდინარე მოქმედება გადასამოწმებელია',
  directory_only_service_claim: 'სერვისის შესაბამისობა მხოლოდ კატალოგშია მითითებული',
  page_source_policy_prohibits_collection: 'წყაროს წესები შეგროვებას კრძალავს',
  manually_added_source_not_verified: 'ხელით დამატებული წყარო ჯერ გადასამოწმებელია',
  interior_service_not_confirmed: 'ინტერიერის დიზაინის სერვისი ვერ დადასტურდა',
  georgia_operation_not_confirmed: 'საქართველოში საქმიანობა ვერ დადასტურდა',
  no_contacts_found: 'კონტაქტები ვერ მოიძებნა',
  unverified_phone_structure: 'ნომრის ფორმატი გადასამოწმებელია',
  multiple_cities: 'მითითებულია რამდენიმე ქალაქი',
  demo_or_fictional_business: 'შესაძლოა დემო ბიზნესი იყოს',
  shared_contact_across_domains: 'სხვადასხვა დომენს საერთო კონტაქტი აქვს',
  mixed_studio_and_academy_contacts: 'სტუდიისა და აკადემიის შერეული კონტაქტები',
  page_robots_disallowed: 'ერთ-ერთი გვერდი robots.txt-ით აკრძალულია',
  page_page_limit: 'გვერდების ლიმიტი მიღწეულია',
};

export function reasonText(reason) { return REASONS[reason] || reason; }
export function runLabel(run) {
  if (run.status === 'completed') {
    return ({ success: 'დასრულებულია', failure: 'შეცდომით დასრულდა', cancelled: 'გაუქმებულია', skipped: 'გამოტოვებულია', timed_out: 'დრო ამოიწურა' })[run.conclusion] || 'დასრულებულია';
  }
  return run.status === 'in_progress' ? 'მიმდინარეობს' : 'რიგშია';
}

export function decodeUTF8Base64(value) {
  return new TextDecoder().decode(Uint8Array.from(atob(value.replace(/\s/g, '')), c => c.charCodeAt(0)));
}
export function encodeUTF8Base64(value) {
  let binary = '';
  for (const byte of new TextEncoder().encode(value)) binary += String.fromCharCode(byte);
  return btoa(binary);
}

export function validateBusiness({ name, url, pages = '', enabled = true }, businesses = [], exclude = -1) {
  const title = String(name).trim();
  if (title.length < 2 || title.length > 250) throw new Error('მიუთითე ბიზნესის სახელი (2–250 სიმბოლო).');
  const canonical = safeURL(String(url).trim());
  if (!canonical) throw new Error('მიუთითე სრული http:// ან https:// ვებსაიტი.');
  const parsed = new URL(canonical);
  if (!parsed.hostname.includes('.') || parsed.hostname.endsWith('.local') || parsed.hostname.endsWith('.internal') || /^\d+\.\d+\.\d+\.\d+$/.test(parsed.hostname) || parsed.hostname.includes(':') || !['', '80', '443'].includes(parsed.port)) {
    throw new Error('გამოიყენე საჯარო ბიზნესის დომენი, ადგილობრივი მისამართების გარეშე.');
  }
  parsed.hash = '';
  const host = parsed.hostname.toLowerCase().replace(/^www\./, '');
  if (businesses.some((b, i) => i !== exclude && safeURL(b.url) && new URL(b.url).hostname.toLowerCase().replace(/^www\./, '') === host)) {
    throw new Error('ეს დომენი წყაროების სიაში უკვე არსებობს. არსებული ჩანაწერი დაარედაქტირე.');
  }
  const list = [...new Set(String(pages).split(/\r?\n/).map(v => v.trim()).filter(Boolean))];
  if (list.length > 4) throw new Error('მთავარი გვერდის გარდა მაქსიმუმ 4 გვერდი დაამატე.');
  const normalized = list.map(value => {
    const page = safeURL(value);
    if (!page || new URL(page).hostname.toLowerCase().replace(/^www\./, '') !== host) throw new Error('ყველა გვერდი იმავე ბიზნესის დომენზე უნდა იყოს.');
    const p = new URL(page);
    if (!['', '80', '443'].includes(p.port)) throw new Error('გვერდის პორტი არ არის მხარდაჭერილი.');
    p.hash = '';
    return p.href;
  });
  return { name: title, url: parsed.href, pages: [...new Set(normalized)], enabled: Boolean(enabled) };
}

GitHubClient.prototype.readSources = async function () {
  const file = await this.request(`/repos/${REPOSITORY}/contents/config/sources.json?ref=main`, { publicOnly: true });
  if (file.encoding !== 'base64' || !/^[a-f0-9]{40}$/.test(file.sha || '')) throw new Error('წყაროების ფაილის ვერსია ვერ წაიკითხა.');
  const config = JSON.parse(decodeUTF8Base64(file.content));
  if (!Array.isArray(config.businesses)) throw new Error('წყაროების ფაილის ფორმატი არასწორია.');
  return { config, sha: file.sha };
};

GitHubClient.prototype.saveSources = async function (config, sha) {
  if (!/^[a-f0-9]{40}$/.test(sha) || !Array.isArray(config.businesses)) throw new Error('Invalid source file state');
  return this.request(`/repos/${REPOSITORY}/contents/config/sources.json`, {
    method: 'PUT', authenticated: true,
    body: { message: 'Update business sources from the web dashboard', content: encodeUTF8Base64(JSON.stringify(config, null, 2) + '\n'), sha, branch: 'main' },
  });
};

export function directContactCounts(records) {
  const all = new Set(), relevant = new Set();
  const excluded = new Set(['interior_service_not_confirmed', 'georgia_operation_not_confirmed', 'demo_or_fictional_business']);
  for (const record of records) {
    const eligible = !(record.review_reasons || []).some(reason => excluded.has(reason));
    for (const fact of record.facts || []) if (['phone', 'email'].includes(fact.kind)) {
      const key = fact.kind + ':' + fact.value;
      all.add(key);
      if (eligible && !fact.historical_source) relevant.add(key);
    }
  }
  return { total: all.size, relevant: relevant.size };
}
