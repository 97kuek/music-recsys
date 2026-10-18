import { test } from 'node:test';
import assert from 'node:assert/strict';
import { handle } from './worker.mjs';
const env = { TUNNEL_ORIGIN: 'https://synthetic.ngrok-free.app' };
const request = (path = '/', options = {}) => new Request(`https://study.example.workers.dev${path}`, { ...options, headers: { 'cf-access-jwt-assertion': 'synthetic-jwt', ...options.headers } });

test('no Access assertion never reaches the tunnel', async () => {
  const response = await handle(new Request('https://study.example.workers.dev/'), env, () => { throw Error('must not fetch'); });
  assert.equal(response.status, 401);
});
test('offline tunnel and timeout show an independent notice and keep the fixed link', async () => {
  for (const upstream of [async () => new Response('ngrok offline', {status:404, headers:{'ngrok-error-code':'ERR_NGROK_3200'}}), async () => {throw Error('network');}, async () => new Response('', {status:502})]) {
    const response = await handle(request(), env, upstream);
    assert.equal(response.status, 503);
    assert.match(await response.text(), /ただいま利用できません/);
    assert.equal(response.headers.get('Cache-Control'), 'no-store');
  }
});
test('API downtime is JSON and never becomes an HTML success', async () => {
  const response = await handle(request('/api/events', {method:'POST', body:'{}'}), env, async () => {throw Error('timeout');});
  assert.equal(response.status, 503);
  assert.match((await response.json()).detail, /ただいま利用できません/);
});
test('audio ranges, session cookies and JWT pass through without buffering', async () => {
  const response = await handle(request('/api/audio/test', {headers:{Range:'bytes=2-4', Cookie:'CF_Authorization=private; music_session=session; other=ignore'}}), env, async (url, options) => {
    assert.equal(url.href, 'https://synthetic.ngrok-free.app/api/audio/test');
    assert.equal(options.headers.get('range'), 'bytes=2-4');
    assert.equal(options.headers.get('cookie'), 'music_session=session');
    assert.equal(options.headers.get('cf-access-jwt-assertion'), 'synthetic-jwt');
    return new Response('abc', {status:206, headers:{'content-range':'bytes 2-4/5', 'set-cookie':'music_session=new; HttpOnly; Secure; SameSite=strict'}});
  });
  assert.equal(response.status, 206);
  assert.equal(await response.text(), 'abc');
  assert.equal(response.headers.get('content-range'), 'bytes 2-4/5');
  assert.match(response.headers.get('set-cookie'), /HttpOnly/);
});
test('invalid configured origins never receive credentials', async () => {
  for (const origin of ['http://localhost:8765', 'https://evil.example', 'https://synthetic.ngrok-free.app/path', 'https://user:secret@synthetic.ngrok-free.app']) {
    assert.equal((await handle(request(), {TUNNEL_ORIGIN:origin}, () => {throw Error('must not fetch');})).status, 503);
  }
});
test('application errors remain visible and redirects stay on the shared host', async () => {
  assert.equal((await handle(request(), env, async () => Response.json({detail:'invalid code'}, {status:401}))).status, 401);
  const response = await handle(request(), env, async () => new Response(null, {status:307,headers:{location:env.TUNNEL_ORIGIN+'/next'}}));
  assert.equal(response.headers.get('location'), 'https://study.example.workers.dev/next');
});
