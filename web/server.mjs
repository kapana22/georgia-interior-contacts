import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
const allowed = new Set(['index.html', 'styles.css', 'app.js', 'core.js', 'favicon.svg']);
const types = { html: 'text/html', css: 'text/css', js: 'text/javascript', svg: 'image/svg+xml' };
const port = Number(process.env.PORT || 4173);
createServer(async (req, res) => {
  const name = new URL(req.url, 'http://localhost').pathname.slice(1) || 'index.html';
  if (!allowed.has(name)) { res.writeHead(404); res.end('Not found'); return; }
  try {
    const body = await readFile(new URL(name, import.meta.url));
    res.writeHead(200, { 'Content-Type': `${types[name.split('.').pop()]}; charset=utf-8`, 'Cache-Control': 'no-store' });
    res.end(body);
  } catch { res.writeHead(500); res.end('Unable to read file'); }
}).listen(port, '127.0.0.1', () => console.log(`Local: http://127.0.0.1:${port}`));
