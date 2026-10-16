import test from 'node:test';
import assert from 'node:assert/strict';
import worker from '../worker/index.mjs';

const request = (path, options) => new Request(`https://music-recsys.example${path}`, options);
test('assets use the asset binding; no API configuration is needed', async () => {
  const response = await worker.fetch(request('/listen'), { ASSETS: { fetch: () => new Response('music-recsys') } });
  assert.equal(await response.text(), 'music-recsys');
});
test('unconfigured preview is explicit and never returns SPA HTML as API', async () => {
  const response = await worker.fetch(request('/api/status'), {});
  assert.equal(response.status, 503);
  assert.match((await response.json()).detail, /準備中/);
});
test('research endpoints and cross-origin writes are blocked', async () => {
  assert.equal((await worker.fetch(request('/api/comparison'), {})).status, 404);
  assert.equal((await worker.fetch(request('/api/sessions', { method: 'POST', headers: { Origin: 'https://other.example', 'X-Music-App': '1' } }), {})).status, 403);
});
test('proxy sends only the session cookie and server secret, with no cache', async () => {
  const original = globalThis.fetch;
  let received;
  globalThis.fetch = async (url, options) => {
    received = { url: String(url), options };
    return new Response('{"accepted":true}', { headers: { 'Content-Type': 'application/json', 'Set-Cookie': 'music_session=test; Secure; HttpOnly; SameSite=Strict', 'X-Internal': 'hidden' } });
  };
  try {
    const response = await worker.fetch(request('/api/events', { method: 'POST', headers: { 'X-Music-App': '1', Origin: 'https://music-recsys.example', Cookie: 'other=private; music_session=abc', Authorization: 'Bearer client-value' }, body: '{}' }), { BACKEND_ORIGIN: 'https://api.example', UPSTREAM_API_TOKEN: 'server-value' });
    assert.equal(received.url, 'https://api.example/api/events');
    assert.equal(received.options.headers.get('Cookie'), 'music_session=abc');
    assert.equal(received.options.headers.get('Authorization'), 'Bearer server-value');
    assert.equal(response.headers.get('Cache-Control'), 'no-store');
    assert.equal(response.headers.get('X-Internal'), null);
    assert.match(response.headers.get('Set-Cookie'), /Secure/);
  } finally { globalThis.fetch = original; }
});
test('invalid backend and redirects fail closed', async () => {
  const env = { BACKEND_ORIGIN: 'http://api.example', UPSTREAM_API_TOKEN: 'test' };
  assert.equal((await worker.fetch(request('/api/status'), env)).status, 503);
  const original = globalThis.fetch;
  globalThis.fetch = async () => new Response(null, { status: 302, headers: { Location: 'https://other.example' } });
  try {
    env.BACKEND_ORIGIN = 'https://api.example';
    assert.equal((await worker.fetch(request('/api/status'), env)).status, 502);
  } finally { globalThis.fetch = original; }
});
test('large request bodies are refused before forwarding', async () => {
  const response = await worker.fetch(request('/api/events', { method: 'POST', headers: { 'X-Music-App': '1' }, body: 'x'.repeat(17000) }), { BACKEND_ORIGIN: 'https://api.example', UPSTREAM_API_TOKEN: 'test' });
  assert.equal(response.status, 413);
});
test('profile preview endpoints use the protected API proxy', async () => {
  assert.equal((await worker.fetch(request('/api/previews', {method: 'POST', headers: {'X-Music-App':'1'}}), {})).status, 503);
  assert.equal((await worker.fetch(request('/api/preview-audio/abc-123'), {})).status, 503);
});
test('availability stays public without exposing configuration or assuming a running Mac', async () => {
  const response=await worker.fetch(request('/availability'),{NEXT_OPEN_AT:'2030-01-02T10:00:00+09:00'});
  const data=await response.json();
  assert.equal(data.state,'unconfigured');
  assert.equal(data.next_open_at,'2030-01-02T01:00:00.000Z');
  assert.equal(response.headers.get('Cache-Control'),'no-store');
  assert.equal((await worker.fetch(request('/availability',{method:'POST'}),{})).status,405);
});
test('availability distinguishes healthy, unavailable and authentication-gated servers', async () => {
  const original=globalThis.fetch;
  const env={BACKEND_ORIGIN:'https://api.example',UPSTREAM_API_TOKEN:'private'};
  try {
    globalThis.fetch=async () => Response.json({ready:true,models:['private-model']});
    let response=await worker.fetch(request('/availability'),env);
    assert.equal((await response.clone().json()).state,'online');
    assert.ok(!(await response.text()).includes('private-model'));
    globalThis.fetch=async () => new Response(null,{status:503});
    assert.equal((await (await worker.fetch(request('/availability'),env)).json()).state,'offline');
    globalThis.fetch=async () => new Response(null,{status:302,headers:{Location:'https://login.example'}});
    assert.equal((await (await worker.fetch(request('/availability'),env)).json()).state,'restricted');
    globalThis.fetch=async () => {throw new Error('network');};
    assert.equal((await (await worker.fetch(request('/availability'),env)).json()).state,'offline');
  } finally {globalThis.fetch=original;}
});
