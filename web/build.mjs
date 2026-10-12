import { mkdir, copyFile, rm } from 'node:fs/promises';
const files = ['index.html', 'styles.css', 'app.js', 'core.js', 'favicon.svg'];
await rm(new URL('./dist/', import.meta.url), { recursive: true, force: true });
await mkdir(new URL('./dist/', import.meta.url));
for (const name of files) {
  await copyFile(new URL(`./${name}`, import.meta.url), new URL(`./dist/${name}`, import.meta.url));
}
console.log('Static website built in web/dist (5 public files; no dependencies).');
