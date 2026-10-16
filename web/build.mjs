import { mkdir, copyFile, rm } from 'node:fs/promises';
const files = ['index.html', 'styles.css', 'app.js', 'core.js', 'favicon.svg', 'leads/index.html', 'leads/styles.css', 'leads/app.js', 'leads/core.js', 'companies/index.html', 'companies/app.js', 'companies/core.js', 'companies/styles.css', 'companies/data.json'];
await rm(new URL('./dist/', import.meta.url), { recursive: true, force: true });
await mkdir(new URL('./dist/', import.meta.url));
for (const name of files) {
  await mkdir(new URL(`./dist/${name.split('/').slice(0, -1).join('/')}/`, import.meta.url), { recursive: true });
  await copyFile(new URL(`./${name}`, import.meta.url), new URL(`./dist/${name}`, import.meta.url));
}
console.log('Static website built in web/dist (separate contacts and leads pages; no dependencies).');
