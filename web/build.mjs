import { mkdir, copyFile, rm, readFile, writeFile } from 'node:fs/promises';
import {companyParts} from './company-parts.mjs';
const files = ['index.html', 'styles.css', 'app.js', 'core.js', 'favicon.svg', 'leads/index.html', 'leads/styles.css', 'leads/app.js', 'leads/core.js', 'companies/index.html', 'companies/app.js', 'companies/core.js', 'companies/sync.js', 'companies/styles.css', 'companies/data.json'];
await rm(new URL('./dist/', import.meta.url), { recursive: true, force: true });
await mkdir(new URL('./dist/', import.meta.url));
for (const name of files) {
  await mkdir(new URL(`./dist/${name.split('/').slice(0, -1).join('/')}/`, import.meta.url), { recursive: true });
  await copyFile(new URL(`./${name === 'index.html' ? 'companies/index.html' : name}`, import.meta.url), new URL(`./dist/${name}`, import.meta.url));
}
await mkdir(new URL('./dist/companies/parts/',import.meta.url),{recursive:true});
const snapshot=JSON.parse(await readFile(new URL('./companies/data.json',import.meta.url),'utf8'));
for(const [name,body] of companyParts(snapshot))await writeFile(new URL(`./dist/companies/parts/${name}`,import.meta.url),body);
console.log('Static website built in web/dist with progressive company data.');
