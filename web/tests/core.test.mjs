import test from 'node:test';
import assert from 'node:assert/strict';
import { GitHubClient, validateInputs, validateBusiness, safeURL, filterRecords, normalizeRecords, rawURL, encodeUTF8Base64, decodeUTF8Base64, REPOSITORY } from '../core.js';

const sha = 'a'.repeat(40);
const fakeToken = 'testtoken'.repeat(4);
const record = (name, status = 'candidate', city = 'თბილისი') => ({ id: name, name, domains: ['studio.test'], status, facts: [{ kind: 'city', value: city, source_url: 'https://studio.test/' }, { kind: 'email', value: 'info@studio.test', source_url: 'https://studio.test/' }], review_reasons: [] });

test('limits and source paths reject invalid input', () => {
  assert.deepEqual(validateInputs('50', 'config/sources.json'), { limit: '50', sources: 'config/sources.json' });
  for (const v of ['', '0', '-1', '1.5', '1001', 'abc']) assert.throws(() => validateInputs(v, 'config/sources.json'));
  for (const v of ['../secret.json', '/config/sources.json', 'config/../../secrets.json', 'https://evil.test', 'config//bad.json']) assert.throws(() => validateInputs(3, v));
});
test('search supports Georgian, contacts and filters', () => {
  const rows = [record('სტუდია'), record('Other', 'review', 'ბათუმი')];
  assert.equal(filterRecords(rows, { query: 'სტუდ' }).length, 1);
  assert.equal(filterRecords(rows, { query: 'INFO@STUDIO.TEST' }).length, 2);
  assert.equal(filterRecords(rows, { city: 'ბათუმი', status: 'review' })[0].name, 'Other');
  assert.equal(filterRecords(rows, { city: 'თბილისი', status: 'review' }).length, 0);
});
test('links reject credentials and executable protocols', () => {
  for (const u of ['javascript:alert(1)', 'data:text/html,hi', 'https://user:password@evil.test', 'file:///tmp/file']) assert.equal(safeURL(u), null);
  assert.equal(safeURL('https://studio.test'), 'https://studio.test/');
});
test('records reject malformed data but preserve text without treating it as markup', () => {
  assert.throws(() => normalizeRecords({ records: [] }));
  const rows = normalizeRecords([null, {}, { ...record('<img src=x onerror=alert(1)>'), facts: [null, ...record('x').facts] }]);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].name, '<img src=x onerror=alert(1)>');
  assert.equal(rows[0].facts.length, 2);
});
test('downloads pin a commit and whitelist files', () => {
  assert.equal(rawURL(sha, 'contacts.csv'), `https://raw.githubusercontent.com/${REPOSITORY}/${sha}/contacts.csv`);
  assert.throws(() => rawURL('data', 'contacts.csv'));
  assert.throws(() => rawURL(sha, 'state.json'));
});
test('source validation preserves Georgian and rejects duplicates and unrelated pages', () => {
  const entry = validateBusiness({ name: 'სტუდია', url: 'https://www.studio.test/#contact', pages: 'https://studio.test/contact\nhttps://studio.test/about' });
  assert.equal(entry.name, 'სტუდია'); assert.equal(entry.url, 'https://www.studio.test/'); assert.equal(entry.pages.length, 2);
  assert.throws(() => validateBusiness({ name: 'Studio', url: 'https://studio.test' }, [entry]));
  assert.doesNotThrow(() => validateBusiness({ name: 'Studio', url: 'https://studio.test' }, [entry], 0));
  assert.throws(() => validateBusiness({ name: 'Studio', url: 'https://studio.test', pages: 'https://other.test/contact' }));
  assert.throws(() => validateBusiness({ name: 'Studio', url: 'http://127.0.0.1' }));
  assert.throws(() => validateBusiness({ name: 'Studio', url: 'https://studio.test', pages: Array.from({ length: 5 }, (_, i) => `https://studio.test/${i}`).join('\n') }));
});
test('UTF-8 source commits round trip Georgian', () => {
  const text = JSON.stringify({ name: 'ქართული სტუდია' }); assert.equal(decodeUTF8Base64(encodeUTF8Base64(text)), text);
});
test('token is sent only to fixed GitHub API and removed on disconnect', async () => {
  const calls = [];
  const client = new GitHubClient(async (url, options) => { calls.push([url, options]); return Response.json({ login: 'tester' }); });
  await client.connect(fakeToken); assert.equal(client.connected, true);
  assert.equal(calls[0][0], 'https://api.github.com/user'); assert.equal(calls[0][1].headers.Authorization, `Bearer ${fakeToken}`);
  assert.equal(calls[0][1].redirect, 'error'); assert.equal(calls[0][1].credentials, 'omit');
  await assert.rejects(() => client.request('https://evil.test'), /Unsupported/);
  client.disconnect(); assert.equal(client.connected, false);
  await assert.rejects(() => client.dispatch(3, 'config/smoke.json'));
  assert.equal(calls.length, 1);
});
test('failed authentication forgets the token', async () => {
  const client = new GitHubClient(async () => new Response('', { status: 401 }));
  await assert.rejects(() => client.connect(fakeToken)); assert.equal(client.connected, false);
});
test('dispatch uses main and validated form inputs', async () => {
  const calls = [];
  const client = new GitHubClient(async (url, options) => { calls.push([url, options]); return Response.json(url.endsWith('/user') ? { login: 'tester' } : { workflow_run_id: 123 }); });
  await client.connect(fakeToken); const result = await client.dispatch('3', 'config/smoke.json');
  assert.equal(result.workflow_run_id, 123); assert.equal(calls[1][1].method, 'POST');
  assert.deepEqual(JSON.parse(calls[1][1].body), { ref: 'main', inputs: { limit: '3', sources: 'config/smoke.json' } });
});
test('dispatch is never retried on network failure', async () => {
  let posts = 0;
  const client = new GitHubClient(async (_, options) => { if (options.method === 'POST') { posts++; throw new Error('timeout'); } return Response.json({ login: 'tester' }); });
  await client.connect(fakeToken); await assert.rejects(() => client.dispatch(3, 'config/smoke.json')); assert.equal(posts, 1);
});
test('source saves use the read version and preserve directories; conflicts fail safely', async () => {
  const config = { businesses: [{ name: 'სტუდია', url: 'https://studio.test' }], directories: [{ enabled: true, max_pages: 2 }] };
  const calls = [];
  const client = new GitHubClient(async (url, options) => {
    calls.push([url, options]);
    if (url.endsWith('/user')) return Response.json({ login: 'tester' });
    if (options.method === 'PUT') return new Response('', { status: 409 });
    return Response.json({ encoding: 'base64', content: encodeUTF8Base64(JSON.stringify(config)), sha });
  });
  await client.connect(fakeToken); const file = await client.readSources();
  await assert.rejects(() => client.saveSources(file.config, file.sha), /სხვამ შეცვალა/);
  const body = JSON.parse(calls.at(-1)[1].body); assert.equal(body.sha, sha); assert.equal(body.branch, 'main');
  assert.deepEqual(JSON.parse(decodeUTF8Base64(body.content)).directories, config.directories);
});
test('rate limits yield an actionable message', async () => {
  const client = new GitHubClient(async () => new Response('', { status: 403, headers: { 'x-ratelimit-remaining': '0' } }));
  await assert.rejects(() => client.runs(), /ლიმიტი ამოიწურა/);
});

test('fetch is invoked without binding it to the client object (browser compatibility)', async () => {
  const client = new GitHubClient(async function () { assert.equal(this, undefined); return Response.json({ workflow_runs: [] }); });
  assert.deepEqual(await client.runs(), []);
});

test('public snapshots do not require contents permissions or transmit the token', async () => {
  const calls = [];
  const client = new GitHubClient(async (url, options) => { calls.push([url, options]); return Response.json(url.endsWith('/user') ? { login: 'tester' } : { object: { sha } }); });
  await client.connect(fakeToken);
  assert.equal(await client.snapshot(), sha);
  assert.equal(calls[1][1].headers.Authorization, undefined);
});

test('direct-contact target excludes social links, duplicates and unrelated providers', async () => {
  const {directContactCounts} = await import('../core.js');
  const a = record('Designer');
  const b = record('Supplier', 'review');
  b.review_reasons = ['interior_service_not_confirmed'];
  b.facts.push({kind:'phone',value:'+995599554188',source_url:'https://studio.test'});
  a.facts.push({kind:'instagram',value:'https://instagram.com/studio',source_url:'https://studio.test'});
  assert.deepEqual(directContactCounts([a,b]),{total:2,relevant:1});
});


test('historical PDF contact remains visible but only independent current evidence counts toward target', async () => {
  const {directContactCounts} = await import('../core.js');
  const records = [{ review_reasons: [], facts: [{kind: 'email', value: 'work@studio.test', historical_source: true}] }];
  assert.deepEqual(directContactCounts(records), {total: 1, relevant: 0});
  records[0].facts.push({kind: 'email', value: 'work@studio.test'});
  assert.deepEqual(directContactCounts(records), {total: 1, relevant: 1});
});
