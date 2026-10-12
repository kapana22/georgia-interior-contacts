import collector from './api/collector.mjs';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
const allowed = new Set(['index.html', 'styles.css', 'app.js', 'core.js', 'favicon.svg', 'leads/index.html', 'leads/styles.css', 'leads/app.js', 'leads/core.js']);
const types = { html: 'text/html', css: 'text/css', js: 'text/javascript', svg: 'image/svg+xml' };
const port = Number(process.env.PORT || 4173);
createServer(async (req, res) => {
  const path = new URL(req.url, 'http://localhost').pathname;
  const name = path === '/leads' || path === '/leads/' ? 'leads/index.html' : path.slice(1) || 'index.html';
  if (name === 'api/collector') { await collector(req, res); return; }
  if (!allowed.has(name)) { res.writeHead(404); res.end('Not found'); return; }
  try {
    const body = await readFile(new URL(name, import.meta.url));
    res.writeHead(200, { 'Content-Type': `${types[name.split('.').pop()]}; charset=utf-8`, 'Cache-Control': 'no-store' });
    res.end(body);
  } catch { res.writeHead(500); res.end('Unable to read file'); }
}).listen(port, '127.0.0.1', () => console.log(`Local: http://127.0.0.1:${port}`));
