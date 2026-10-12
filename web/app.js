import { GitHubClient, REPOSITORY, REPO_URL, WORKFLOW_URL, rawURL, normalizeRecords, values, filterRecords, validateInputs, validateBusiness, safeURL, reasonText, runLabel, directContactCounts, RememberedAuth } from './core.js';

const $ = id => document.getElementById(id);
const client = new GitHubClient();
const rememberedAuth = new RememberedAuth();
const state = { records: [], report: null, sha: null, page: 1, pageSize: 50, activeId: null, timer: null, polling: false, sources: null, sourcesSHA: null, editingIndex: null };

function el(tag, className = '', text = '') {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== '') node.textContent = text;
  return node;
}
function date(value, short = false) {
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? 'თარიღი უცნობია' : new Intl.DateTimeFormat('ka-GE', { dateStyle: short ? 'short' : 'medium', timeStyle: 'short' }).format(parsed);
}
function link(text, value, className = '') {
  const href = safeURL(value);
  const node = el(href ? 'a' : 'span', className, text);
  if (href) { node.href = href; node.target = '_blank'; node.rel = 'noopener noreferrer'; }
  return node;
}
function message(id, text = '', error = false) {
  const node = $(id); node.textContent = text;
  node.classList.toggle('error', error); node.classList.toggle('success', !error && Boolean(text));
}
function notice(id, text = '') { $(id).textContent = text; $(id).hidden = !text; }
function sourcePath() { return $('sources').value === 'custom' ? $('custom-sources').value : $('sources').value; }
function busy(button, active, label) { button.disabled = active; button.textContent = label; }

async function collector(method = 'GET', body) {
  const response = await fetch('/api/collector', {
    method, credentials: 'omit', cache: 'no-store', redirect: 'error',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined, signal: AbortSignal.timeout(20000),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'გაშვების სერვისი დროებით მიუწვდომელია.');
  return result;
}

async function rawFile(ref, file, type = 'json') {
  let response;
  try { response = await fetch(rawURL(ref, file), { credentials: 'omit', redirect: 'error', cache: 'no-store', signal: AbortSignal.timeout(20000) }); }
  catch { throw new Error('მონაცემების ჩატვირთვა ვერ მოხერხდა. შეამოწმე ინტერნეტი.'); }
  if (!response.ok) throw new Error(response.status === 404 ? 'ფაილი ჯერ არ არსებობს. გაუშვი შეგროვება ან შეამოწმე წყაროს გზა.' : `მონაცემები დროებით მიუწვდომელია (HTTP ${response.status}).`);
  return type === 'blob' ? response.blob() : response.json();
}

function renderStats() {
  $('stat-businesses').textContent = state.records.length;
  const direct = directContactCounts(state.records);
  $('stat-contacts').textContent = direct.total;
  $('stat-target').textContent = `სამიზნე: ${direct.relevant} / ${state.report?.target_contacts || 1000}`;
  $('stat-review').textContent = state.records.filter(r => r.status === 'review').length;
  $('stat-new').textContent = state.report?.new_records ?? '—';
  const savedAt = state.report?.checkpoint_at || state.report?.finished_at;
  $('stat-date').textContent = savedAt ? date(savedAt, true) : '';
  const status = state.report?.collection_status;
  const label = status === 'exhausted' ? 'წყაროები ამოწურულია' : status === 'waiting' ? 'წყაროს პაუზის დასრულებას ელოდება' : status === 'paused' ? 'შეჩერებულია · გადასამოწმებელია' : state.report?.continuous ? (state.report.status === 'running' ? 'გროვდება' : 'ავტომატურად გაგრძელდება') : 'ავტომატური შეგროვება გამორთულია';
  $('collection-state').textContent = label; $('collection-status').textContent = label;
  $('data-updated').textContent = savedAt ? `ბოლო შენახვა: ${date(savedAt)} · data ბრენჩი` : 'წყარო: რეპოზიტორიის data ბრენჩი';
  const selected = $('city-filter').value;
  const cities = [...new Set(state.records.flatMap(r => values(r, 'city')))].sort((a, b) => a.localeCompare(b, 'ka'));
  $('city-filter').replaceChildren(el('option', '', 'ყველა ქალაქი'));
  $('city-filter').firstElementChild.value = '';
  for (const city of cities) { const option = el('option', '', city); option.value = city; $('city-filter').append(option); }
  $('city-filter').value = cities.includes(selected) ? selected : '';
}

function contactLine(record, kind, label) {
  const list = values(record, kind);
  if (!list.length) return null;
  const row = el('div', 'contact-line'); row.append(el('span', 'line-label', label));
  const items = el('div', 'line-values');
  for (const value of list) {
    if (kind === 'phone' && /^\+\d{7,15}$/.test(value)) {
      const a = el('a', '', value); a.href = `tel:${value}`; items.append(a);
    } else if (kind === 'email' && /^[^\s<>@]+@[^\s<>@]+\.[^\s<>@]+$/.test(value)) {
      const a = el('a', '', value); a.href = `mailto:${encodeURIComponent(value).replace(/%40/g, '@')}`; items.append(a);
    } else if (kind === 'website') {
      items.append(link(safeURL(value) ? new URL(value).hostname.replace(/^www\./, '') : value, value));
    } else items.append(el('span', '', value));
    if (['phone', 'email'].includes(kind)) {
      const copy = el('button', 'copy-contact', '⧉'); copy.type = 'button';
      copy.setAttribute('aria-label', `${label} ${value} კოპირება`);
      copy.addEventListener('click', async () => {
        try { await navigator.clipboard.writeText(value); message('download-message', 'კოპირებულია.'); }
        catch { message('download-message', 'კოპირება ვერ მოხერხდა. კონტაქტი მონიშნე და დააკოპირე.', true); }
      }); items.append(copy);
    }
  }
  row.append(items); return row;
}

function contactCard(record) {
  const card = el('article', 'contact-card');
  const heading = el('div', 'contact-card-heading');
  const title = el('div', 'contact-title');
  title.append(el('div', 'contact-avatar', (record.name || record.domains[0] || '?').slice(0, 1).toLocaleUpperCase('ka-GE')));
  const text = el('div'); text.append(el('h3', '', record.name || record.domains[0] || 'უსახელო ბიზნესი'));
  text.append(el('p', 'contact-city', values(record, 'city').join(' · ') || 'ქალაქი მითითებული არ არის'));
  title.append(text); heading.append(title, el('span', `contact-status ${record.status === 'review' ? 'review' : ''}`, record.status === 'review' ? 'გადასამოწმებელი' : 'კანდიდატი'));
  card.append(heading);
  for (const [kind, label] of [['phone', 'ტელეფონი'], ['email', 'ელფოსტა'], ['website', 'ვებსაიტი']]) { const row = contactLine(record, kind, label); if (row) card.append(row); }
  const social = el('div', 'contact-social');
  for (const [kind, label] of [['instagram', 'Instagram'], ['facebook', 'Facebook']]) for (const value of values(record, kind)) social.append(link(label + ' ↗', value));
  if (social.childElementCount) card.append(social);
  const detail = el('details', 'contact-detail'); detail.append(el('summary', '', 'წყაროები, თარიღები და შენიშვნები'));
  if (record.review_reasons.length) {
    const reasons = el('ul', 'reason-list'); for (const reason of record.review_reasons) reasons.append(el('li', '', reasonText(reason))); detail.append(reasons);
  }
  for (const fact of record.facts.filter(f => ['phone', 'phone_unverified', 'email', 'instagram', 'facebook', 'city'].includes(f.kind))) {
    const evidence = el('div', 'fact'); evidence.append(el('strong', '', fact.value));
    evidence.append(link(fact.source_url, fact.source_url));
    evidence.append(el('small', '', `პირველად: ${date(fact.first_seen, true)} · ბოლოს: ${date(fact.last_seen, true)}`)); detail.append(evidence);
  }
  card.append(detail); return card;
}

function renderContacts() {
  const filtered = filterRecords(state.records, { query: $('search').value, city: $('city-filter').value, status: $('status-filter').value, channel: $('contact-filter').value }).sort((a, b) => a.name.localeCompare(b.name, 'ka'));
  $('clear-filters').hidden = !['search', 'city-filter', 'status-filter', 'contact-filter'].some(id => $(id).value);
  const pages = Math.max(1, Math.ceil(filtered.length / state.pageSize));
  state.page = Math.min(state.page, pages);
  const grid = $('contact-grid'); grid.replaceChildren(); grid.setAttribute('aria-busy', 'false');
  $('results-count').textContent = filtered.length;
  for (const record of filtered.slice((state.page - 1) * state.pageSize, state.page * state.pageSize)) grid.append(contactCard(record));
  if (!filtered.length) {
    const empty = el('div', 'empty-state'); empty.append(el('span', 'empty-icon', '⌕'), el('h3', '', state.records.length ? 'შესაბამისი ჩანაწერი ვერ მოიძებნა' : 'კონტაქტები ჯერ არ არის'), el('p', '', state.records.length ? 'შეცვალე ძიება ან ფილტრები.' : 'შეგროვების დასრულების შემდეგ შედეგები აქ გამოჩნდება.')); grid.append(empty);
  }
  $('pagination').hidden = pages <= 1; $('page-label').textContent = `${filtered.length ? (state.page - 1) * state.pageSize + 1 : 0}–${Math.min(state.page * state.pageSize, filtered.length)} / ${filtered.length}`;
  $('previous-page').disabled = state.page <= 1; $('next-page').disabled = state.page >= pages;
}

async function refreshData(background = false) {
  if (state.refreshing || (background && document.hidden)) return;
  state.refreshing = true;
  busy($('refresh-data'), true, 'იტვირთება…'); $('contact-grid').setAttribute('aria-busy', 'true');
  try {
    // One atomic public file: no anonymous GitHub API request or token needed.
    const snapshot = await rawFile('data', 'snapshot.json');
    if (snapshot.version !== 1 || !snapshot.report || !Array.isArray(snapshot.records)) throw new Error('მონაცემების ფორმატი არასწორია.');
    const records = normalizeRecords(snapshot.records);
    state.records = records; state.sha = 'data'; state.report = snapshot.report; state.snapshotRuns = snapshot.runs || [];
    if (!state.publicCollector && !client.connected) renderRuns(state.snapshotRuns);
    notice('data-error');
    renderStats(); renderContacts(); document.querySelectorAll('[data-download]').forEach(b => { b.disabled = false; });

  } catch (error) {
    notice('data-error', error.message + (state.sha ? ' ეკრანზე წინა ჩატვირთული შედეგები დარჩა.' : ''));
    if (!state.sha) { const empty = el('div', 'empty-state'); empty.append(el('span', 'empty-icon', '↻'), el('h3', '', 'ბაზა ვერ ჩაიტვირთა'), el('p', '', 'დააჭირე „განახლებას“ ან ნახე შედეგები GitHub-ზე.')); $('contact-grid').replaceChildren(empty); }
  } finally { state.refreshing = false; state.lastDataCheck = Date.now(); busy($('refresh-data'), false, '↻ განახლება'); $('contact-grid').setAttribute('aria-busy', 'false'); }
}

function renderRuns(runs) {
  const list = $('runs-list'); list.replaceChildren();
  for (const run of runs) {
    const pending = run.status !== 'completed'; const failed = !pending && run.conclusion !== 'success';
    const row = el('div', 'run-row'); row.append(el('span', `run-symbol ${failed ? 'failure' : ''}`, pending ? '◷' : failed ? '!' : '✓'));
    const info = el('div', 'run-info'); info.append(el('strong', '', `შეგროვება #${run.run_number || run.id}`), el('span', '', date(run.created_at, true))); row.append(info);
    row.append(el('span', `run-badge ${pending ? 'pending' : failed ? 'failure' : ''}`, runLabel(run)), link('↗', run.html_url, 'run-arrow')); list.append(row);
  }
  if (!runs.length) list.append(link('გაშვებები GitHub-ზე ↗', WORKFLOW_URL, 'loading-row'));
}
async function refreshRuns() {
  $('refresh-runs').disabled = true;
  try {
    const result = await collector(); state.publicCollector = result.configured;
    const runs = !result.configured ? (client.connected ? await client.runs() : state.snapshotRuns || []) : result.runs;
    $('run-form').hidden = !result.configured && !client.connected;
    renderRuns(runs); notice('runs-error');
    const active = runs.find(run => run.status !== 'completed');
    if (active && !state.activeId) {
      state.activeId = active.id; $('active-run').hidden = false;
      $('active-run-label').textContent = runLabel(active);
      $('active-run-link').href = `${REPO_URL}/actions/runs/${active.id}`;
      if (!state.polling) { stopPolling(); state.timer = setTimeout(pollActiveRun, 15000); }
    }
  }
  catch (error) { notice('runs-error', error.message); if ($('runs-list').querySelector('.loading-row')) $('runs-list').replaceChildren(); }
  finally { $('refresh-runs').disabled = false; }
}

function stopPolling() { if (state.timer) clearTimeout(state.timer); state.timer = null; }
async function pollActiveRun() {
  if (!state.activeId || document.hidden || state.polling) return;
  state.polling = true;
  try {
    const result = !state.publicCollector && !client.connected ? { configured: false, runs: state.snapshotRuns || [] } : await collector(); const run = !result.configured && client.connected ? await client.run(state.activeId) : (result.configured ? result.runs : state.snapshotRuns || []).find(r => String(r.id) === String(state.activeId));
    if (!run) { state.timer = setTimeout(pollActiveRun, 15000); return; }
    $('active-run-label').textContent = runLabel(run);
    $('active-run-description').textContent = run.status === 'completed' ? (run.conclusion === 'success' ? 'შეგროვება დასრულდა. ბოლო შენახული შედეგები განახლდება.' : 'ნახე GitHub-ის ანგარიში. შესაძლოა ნაწილობრივი შედეგებიც შენახული იყოს.') : 'გვერდის დახურვა შეგროვებას არ აჩერებს. შედეგები დასრულების შემდეგ განახლდება.';
    if (run.status === 'completed') { state.activeId = null; stopPolling(); await Promise.allSettled([refreshData(), refreshRuns()]); }
    else state.timer = setTimeout(pollActiveRun, 15000);
  } catch (error) { notice('runs-error', error.message + ' მონიტორინგი გაჩერდა; მიმდინარე გაშვება GitHub-ზე გრძელდება.'); stopPolling(); }
  finally { state.polling = false; }
}

$('run-form').addEventListener('submit', async event => {
  event.preventDefault(); message('run-message');
  try { validateInputs($('limit').value, sourcePath()); }
  catch (error) { message('run-message', error.message, true); return; }
  busy($('run-submit'), true, 'მოთხოვნა იგზავნება…');
  try {
    const input = validateInputs($('limit').value, sourcePath());
    await rawFile('main', input.sources); // Confirm the editable path exists before dispatch.
    const result = !state.publicCollector && client.connected ? await client.dispatch(input.limit, input.sources) : await collector('POST', input);
    message('run-message', 'მოთხოვნა მიღებულია. შეგროვება GitHub Actions-ზე დაიწყება.');
    $('active-run').hidden = false; $('active-run-label').textContent = 'მოთხოვნა მიღებულია';
    const id = result?.workflow_run_id; const href = id && /^\d+$/.test(String(id)) ? `${REPO_URL}/actions/runs/${id}` : WORKFLOW_URL;
    $('active-run-link').href = href;
    $('active-run-description').textContent = 'შეგროვება GitHub Actions-ზე სრულდება. გვერდის დახურვა პროცესს არ აჩერებს.';
    stopPolling(); state.activeId = id && /^\d+$/.test(String(id)) ? id : null;
    if (state.activeId) await pollActiveRun();
    await refreshRuns();
  } catch (error) { message('run-message', error.message + ' თუ მოთხოვნისას კავშირი გაწყდა, გამეორებამდე GitHub-ზე გადაამოწმე, ხომ არ დაიწყო გაშვება.', true); }
  finally { busy($('run-submit'), false, 'შეგროვების დაწყება →'); }
});

function updateAuth(user = null) {
  $('run-form').hidden = !state.publicCollector && !user;
  $('auth-label').textContent = user ? user.login : 'მფლობელი'; $('auth-dot').classList.toggle('connected', Boolean(user));
  $('auth-connected').hidden = !user; $('auth-form').hidden = Boolean(user); $('auth-user').textContent = user?.login || '';
}
$('auth-form').addEventListener('submit', async event => {
  event.preventDefault(); const token = $('github-token').value; $('github-token').value = '';
  busy($('connect-submit'), true, 'მოწმდება…'); message('auth-message');
  try { const user = await client.connect(token);
    const saved = $('remember-auth').checked ? rememberedAuth.save(token.trim()) : (rememberedAuth.clear(), false);
    updateAuth(user); $('auth-dialog').close(); message('run-message', saved ? 'GitHub დაკავშირებულია და ამ ბრაუზერში დამახსოვრებულია.' : 'GitHub დაკავშირებულია მხოლოდ ამ სესიაში.'); await refreshRuns(); if (state.activeId) { stopPolling(); pollActiveRun(); } }
  catch (error) { updateAuth(); message('auth-message', error.message, true); }
  finally { busy($('connect-submit'), false, 'დაკავშირება →'); }
});
$('disconnect').addEventListener('click', () => { rememberedAuth.clear(); client.disconnect(); updateAuth(); stopPolling(); $('github-token').value = ''; message('auth-message', 'ავტორიზაცია გაითიშა და შენახული ტოკენი წაიშალა.'); });
$('auth-open').addEventListener('click', () => { message('auth-message'); $('auth-dialog').showModal(); });
$('auth-dialog').addEventListener('close', () => { $('github-token').value = ''; });
document.querySelectorAll('[data-close]').forEach(button => button.addEventListener('click', () => $(button.dataset.close).close()));

$('sources').addEventListener('change', () => { const custom = $('sources').value === 'custom'; $('custom-sources-wrap').hidden = !custom; $('custom-sources').required = custom; if (custom) $('custom-sources').focus(); });
document.querySelectorAll('[data-limit]').forEach(button => button.addEventListener('click', () => { $('limit').value = button.dataset.limit; if (button.dataset.limit === '3') { $('sources').value = 'config/smoke.json'; $('sources').dispatchEvent(new Event('change')); } syncPresets(); }));
function syncPresets() { document.querySelectorAll('[data-limit]').forEach(b => b.classList.toggle('selected', b.dataset.limit === $('limit').value)); }
$('limit').addEventListener('input', syncPresets);
for (const id of ['search', 'city-filter', 'status-filter', 'contact-filter']) $(id).addEventListener(id === 'search' ? 'input' : 'change', () => { state.page = 1; renderContacts(); });
$('clear-filters').addEventListener('click', () => { for (const id of ['search', 'city-filter', 'status-filter', 'contact-filter']) $(id).value = ''; state.page = 1; renderContacts(); $('search').focus(); });
$('page-size').addEventListener('change', () => { state.pageSize = Number($('page-size').value); state.page = 1; renderContacts(); });
document.addEventListener('keydown', event => { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k' && !document.querySelector('dialog[open]')) { event.preventDefault(); $('search').focus(); $('search').select(); } });
$('previous-page').addEventListener('click', () => { state.page--; renderContacts(); });
$('next-page').addEventListener('click', () => { state.page++; renderContacts(); });
$('refresh-data').addEventListener('click', () => refreshData());
$('refresh-runs').addEventListener('click', async () => { await refreshRuns(); if (state.activeId) { stopPolling(); pollActiveRun(); } });

document.querySelectorAll('[data-download]').forEach(button => button.addEventListener('click', async () => {
  if (!state.sha) return; button.disabled = true; message('download-message');
  try {
    const blob = await rawFile(state.sha, button.dataset.download, 'blob');
    const url = URL.createObjectURL(blob); const anchor = el('a'); anchor.href = url; anchor.download = button.dataset.download;
    document.body.append(anchor); anchor.click(); anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 30000);
    message('download-message', `${button.dataset.download} ჩამოტვირთვა დაიწყო.`);
  } catch (error) { message('download-message', error.message, true); }
  finally { button.disabled = false; }
}));

function renderSources() {
  $('sources-list').replaceChildren();
  const businesses = state.sources.businesses;
  $('source-count').textContent = `${businesses.length} წყარო · ${businesses.filter(b => b.enabled !== false).length} ჩართული`;
  businesses.forEach((business, index) => {
    const row = el('div', `source-row ${business.enabled === false ? 'source-disabled' : ''}`); row.append(el('i', 'status-dot'));
    const info = el('div'); info.append(el('strong', '', business.name || business.url), link(business.url, business.url)); row.append(info);
    const edit = el('button', 'button button-small button-outline', 'რედაქტირება'); edit.addEventListener('click', () => editSource(index)); row.append(edit); $('sources-list').append(row);
  });
}
async function loadSources() {
  $('source-add').disabled = true; message('sources-message');
  try { const result = client.connected ? await client.readSources() : { config: await rawFile('main', 'config/sources.json'), sha: null }; state.sources = result.config; state.sourcesSHA = result.sha; renderSources(); }
  catch (error) { message('sources-message', error.message, true); }
  finally { $('source-add').disabled = !state.sources; }
}
$('sources-open').addEventListener('click', () => { $('sources-dialog').showModal(); loadSources(); });
function editSource(index = null) {
  state.editingIndex = index;
  const source = index === null ? {} : state.sources.businesses[index];
  $('source-edit-title').textContent = index === null ? 'ბიზნესის დამატება' : 'ბიზნესის რედაქტირება';
  $('source-name').value = source.name || ''; $('source-url').value = source.url || ''; $('source-pages').value = (source.pages || []).join('\n'); $('source-enabled').checked = source.enabled !== false;
  message('source-edit-message'); $('source-edit-dialog').showModal();
}
$('source-add').addEventListener('click', () => editSource());
$('source-edit-form').addEventListener('submit', async event => {
  event.preventDefault(); message('source-edit-message');
  let business;
  try { business = validateBusiness({ name: $('source-name').value, url: $('source-url').value, pages: $('source-pages').value, enabled: $('source-enabled').checked }, state.sources.businesses, state.editingIndex ?? -1); }
  catch (error) { message('source-edit-message', error.message, true); return; }
  if (!client.connected) { message('source-edit-message', 'შენახვისთვის გაიარე GitHub ავტორიზაცია და ხელახლა დააჭირე შენახვას.'); $('auth-dialog').showModal(); return; }
  busy($('source-save'), true, 'ინახება…');
  try {
    const config = structuredClone(state.sources);
    if (state.editingIndex === null) config.businesses.push({ ...business, verified_at: '', evidence_url: business.url, evidence: 'Added through the web dashboard; requires source verification.', review_reasons: ['manually_added_source_not_verified'] });
    else {
      const old = config.businesses[state.editingIndex];
      config.businesses[state.editingIndex] = { ...old, ...business };
      if (old.url !== business.url) {
        config.businesses[state.editingIndex].verified_at = '';
        config.businesses[state.editingIndex].evidence_url = business.url;
        config.businesses[state.editingIndex].evidence = 'Website changed through the web dashboard; requires source verification.';
        config.businesses[state.editingIndex].review_reasons = [...new Set([...(old.review_reasons || []), 'manually_added_source_not_verified'])];
      }
    }
    const result = await client.saveSources(config, state.sourcesSHA);
    state.sources = config; state.sourcesSHA = result.content?.sha || state.sourcesSHA;
    renderSources(); $('source-edit-dialog').close();
    message('sources-message', 'ცვლილება GitHub-ზე შენახულია. შემდეგი შეგროვება განახლებულ სიას გამოიყენებს.');
  } catch (error) { message('source-edit-message', error.message, true); }
  finally { busy($('source-save'), false, 'GitHub-ზე შენახვა →'); }
});

window.addEventListener('pagehide', () => { client.disconnect(); $('github-token').value = ''; stopPolling(); });
window.addEventListener('pageshow', event => { if (event.persisted) { updateAuth(); restoreAuthentication(); } });
document.addEventListener('visibilitychange', () => { if (document.hidden) stopPolling(); else if (state.activeId) pollActiveRun(); });
Promise.allSettled([refreshData(), restoreAuthentication()]);

// Public data may change through a CLI run or GitHub Actions outside this tab.
setInterval(() => refreshData(true), 90000);
window.addEventListener('focus', () => { if (Date.now() - (state.lastDataCheck || 0) > 90000) refreshData(true); });

async function restoreAuthentication() {
  if (state.restoringAuth) return;
  state.restoringAuth = true;
  busy($('connect-submit'), true, 'მოწმდება…');
  try {
    const user = await rememberedAuth.restore(client);
    updateAuth(user);
    if (user) message('run-message', 'GitHub ავტომატურად დაკავშირებულია.');
  } catch (error) { updateAuth(); message('auth-message', error.message, true); }
  finally { state.restoringAuth = false; busy($('connect-submit'), false, 'დაკავშირება →'); }
  await refreshRuns();
  if (state.activeId) { stopPolling(); pollActiveRun(); }
}
