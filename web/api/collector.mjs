// Server-only token. Never copied into web/dist or returned to visitors.
const REPO = 'kapana22/georgia-interior-contacts';
const WORKFLOW = 'scrape.yml';
const SOURCES = new Set(['config/sources.json', 'config/smoke.json']);
export function createCollector({ token = () => process.env.GITHUB_ACTIONS_TOKEN, fetcher = fetch, now = Date.now } = {}) {
  let cached, cachedAt = 0, pending = false, lastDispatch = 0;
  async function github(path, method = 'GET', body) {
    const response = await fetcher(`https://api.github.com/repos/${REPO}/${path}`, {
      method, redirect: 'error', signal: AbortSignal.timeout(15000),
      headers: { Authorization: `Bearer ${token()}`, Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2026-03-10', 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!response.ok) throw new Error('GitHub-თან კავშირი დროებით ვერ მოხერხდა. გაშვებები GitHub-ზე გადაამოწმე.');
    return response.status === 204 ? {} : response.json();
  }
  async function runs(force = false) {
    if (!force && cached && now() - cachedAt < 15000) return cached;
    const result = await github(`actions/workflows/${WORKFLOW}/runs?per_page=5&branch=main`);
    // Whitelist fields rather than passing GitHub's entire response through.
    cached = (result.workflow_runs || []).map(r => Object.fromEntries(
      ['id', 'run_number', 'status', 'conclusion', 'created_at', 'html_url'].map(k => [k, r[k]])));
    cachedAt = now(); return cached;
  }
  return async function handler(req, res) {
    const send = (status, value) => { res.statusCode = status; res.setHeader('Content-Type', 'application/json; charset=utf-8'); res.setHeader('Cache-Control', 'no-store'); res.end(JSON.stringify(value)); };
    if (!['GET', 'POST'].includes(req.method)) { res.setHeader('Allow', 'GET, POST'); send(405, { error: 'Method not allowed' }); return; }
    if (!token()) { send(req.method === 'GET' ? 200 : 503, { configured: false, runs: [], error: 'სერვერული კავშირი ჯერ არ არის გამართული. საჭიროა Vercel-ზე GITHUB_ACTIONS_TOKEN.' }); return; }
    if (req.method === 'GET') { try { send(200, { configured: true, runs: await runs() }); } catch (e) { send(503, { error: e.message }); } return; }
    // No cookies/authentication; public dispatch is deliberate. Disallow cross-origin form submission.
    const origin = req.headers.origin;
    if (origin && (!/^https?:\/\//.test(origin) || (() => { try { return new URL(origin).host !== req.headers.host; } catch { return true; } })())) { send(403, { error: 'Cross-origin request rejected' }); return; }
    if (!(req.headers['content-type'] || '').startsWith('application/json')) { send(415, { error: 'JSON required' }); return; }
    let input;
    try {
      if (req.body !== undefined) input = typeof req.body === 'string' ? JSON.parse(req.body) : req.body;
      else { let text = ''; for await (const chunk of req) { text += chunk; if (text.length > 2048) throw new Error('Body too large'); } input = JSON.parse(text); }
      if (!input || JSON.stringify(input).length > 2048 || !Number.isInteger(Number(input.limit)) || Number(input.limit) < 1 || Number(input.limit) > 1000 || !SOURCES.has(input.sources)) throw new Error('Invalid parameters');
    } catch { send(400, { error: 'აირჩიე ლიმიტი 1–1000 და სტანდარტული ან მცირე გამოცდის წყაროები.' }); return; }
    if (pending || (lastDispatch && now() - lastDispatch < 60000)) { send(429, { error: 'მოთხოვნა უკვე მიღებულია. ერთი წუთის შემდეგ განაახლე გაშვებები.' }); return; }
    pending = true;
    try {
      // Cross-instance guard: reuse the repository's running/queued job.
      // GitHub workflow concurrency is the final global serialization mechanism.
      const active = (await runs(true)).find(r => r.status !== 'completed');
      if (active) { send(200, { workflow_run_id: active.id, existing: true }); return; }
      lastDispatch = now();
      const result = await github(`actions/workflows/${WORKFLOW}/dispatches`, 'POST', { ref: 'main', inputs: { limit: String(Number(input.limit)), sources: input.sources } });
      cached = null; send(200, { workflow_run_id: result.workflow_run_id || null, existing: false });
    } catch (e) { send(503, { error: e.message + ' მოთხოვნა ავტომატურად არ განმეორებულა.' }); }
    finally { pending = false; }
  };
}
export default createCollector();
