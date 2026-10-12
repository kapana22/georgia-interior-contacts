import test from 'node:test';
import assert from 'node:assert/strict';
import { GitHubClient, RememberedAuth, REMEMBERED_TOKEN_KEY } from '../core.js';

const fixtureToken = 'fixture_token_for_storage_tests';
function storage() {
  const data = new Map();
  return { getItem: key => data.get(key) || null, setItem: (key, value) => data.set(key, value), removeItem: key => data.delete(key) };
}

test('remembered authentication survives a new page instance and clearing forgets it', async () => {
  const store = storage(), first = new RememberedAuth(() => store);
  assert.equal(first.save(fixtureToken), true);
  const nextPage = new RememberedAuth(() => store);
  const calls = [];
  const client = { connect: async token => { calls.push(token); return { login: 'owner' }; } };
  assert.deepEqual(await nextPage.restore(client), {login: 'owner'});
  assert.deepEqual(calls, [fixtureToken]);
  nextPage.clear();
  assert.equal(await nextPage.restore(client), null);
});

test('invalidated GitHub credential is forgotten, transient errors preserve it', async () => {
  const store = storage(), saved = new RememberedAuth(() => store);
  saved.save(fixtureToken);
  await assert.rejects(() => saved.restore({ connect: async () => { throw new Error('network unavailable'); } }));
  assert.equal(saved.load(), fixtureToken);
  await assert.rejects(() => saved.restore({ connect: async () => { throw Object.assign(new Error('unauthorized'), {status:401}); } }));
  assert.equal(saved.load(), '');
});

test('malformed and unavailable browser storage do not break the public dashboard', async () => {
  const store = storage(), saved = new RememberedAuth(() => store);
  store.setItem(REMEMBERED_TOKEN_KEY, '<invalid credential>');
  assert.equal(saved.load(), '');
  assert.equal(store.getItem(REMEMBERED_TOKEN_KEY), null);
  const unavailable = new RememberedAuth(() => { throw new Error('storage disabled'); });
  assert.equal(unavailable.save(fixtureToken), false);
  assert.equal(await unavailable.restore({connect: () => { throw new Error('must not connect'); }}), null);
});

test('restored token goes only to GitHub authentication, never public snapshots', async () => {
  const store = storage(), saved = new RememberedAuth(() => store); saved.save(fixtureToken);
  const calls = [];
  const client = new GitHubClient(async (url, options) => {
    calls.push([url, options]);
    return new Response(JSON.stringify(url.endsWith('/user') ? {login:'owner'} : {object:{sha:'a'.repeat(40)}}), {status:200});
  });
  await saved.restore(client); await client.snapshot();
  assert.equal(calls[0][0], 'https://api.github.com/user');
  assert.equal(calls[0][1].headers.Authorization, 'Bearer ' + fixtureToken);
  assert.equal(calls[1][1].headers.Authorization, undefined);
});
