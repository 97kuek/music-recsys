import { test } from 'node:test';
import assert from 'node:assert/strict';
import { handle } from './gateway.mjs';
const secret = '1'.repeat(64);
function environment() {
  let value;
  return { TUNNEL_PUBLISH_TOKEN: secret, TUNNEL_ORIGINS: {
    async put(key, text, options) { assert.equal(key, 'active'); assert.equal(options.expirationTtl, 600); value = JSON.parse(text); },
    async get(key, type) { assert.equal(type, 'json'); return value; },
  }};
}
const publish = (origin, token = secret) => new Request('https://study.pages.dev/api/_tunnel', {method:'PUT',headers:{Authorization:`Bearer ${token}`},body:JSON.stringify({origin})});
const request = (path = '/api/status', options={}) => new Request(`https://study.pages.dev${path}`, {...options, headers:{'cf-access-jwt-assertion':'synthetic-jwt',...options.headers}});

test('Mac restart changes only the backend target; the shared URL stays fixed', async () => {
  const env = environment();
  for (const origin of ['https://first.trycloudflare.com', 'https://second.trycloudflare.com']) {
    assert.equal((await handle(publish(origin), env)).status, 200);
    const reply = await handle(request(), env, async target => {
      assert.equal(target.origin, origin);
      return Response.json({ready:true});
    });
    assert.equal((await reply.json()).ready, true);
  }
});
test('publisher requires private token and only permits Quick Tunnel origins', async () => {
  const env = environment();
  assert.equal((await handle(publish('https://first.trycloudflare.com','bad'),env)).status,401);
  for (const origin of ['http://localhost:8765','https://evil.example','https://first.trycloudflare.com/path']) {
    assert.equal((await handle(publish(origin),env)).status,400);
  }
  const browser = publish('https://first.trycloudflare.com'); browser.headers.set('Origin','https://evil.example');
  assert.equal((await handle(browser,env)).status,401);
});
test('no registration or an offline Mac yields an explicit unavailable JSON', async () => {
  const env=environment();
  assert.equal((await handle(request(),env)).status,503);
  await handle(publish('https://first.trycloudflare.com'),env);
  for(const upstream of [async()=>{throw Error('offline');},async()=>new Response(null,{status:530})]) {
    const reply=await handle(request(),env,upstream);
    assert.equal(reply.status,503);assert.match((await reply.json()).detail,/ただいま利用できません/);
  }
});
test('Access is mandatory and range, cookies and application errors survive forwarding',async()=>{
  const env=environment();await handle(publish('https://first.trycloudflare.com'),env);
  assert.equal((await handle(new Request('https://study.pages.dev/api/status'),env)).status,401);
  const reply=await handle(request('/api/audio/test',{headers:{Range:'bytes=1-3',Cookie:'CF_Authorization=ignore; music_session=synthetic'}}),env,async(target,options)=>{
    assert.equal(options.headers.get('cookie'),'music_session=synthetic');
    assert.equal(options.headers.get('range'),'bytes=1-3');
    assert.equal(options.headers.get('cf-access-jwt-assertion'),'synthetic-jwt');
    return new Response('abc',{status:206,headers:{'Content-Range':'bytes 1-3/5'}});
  });
  assert.equal(reply.status,206);assert.equal(await reply.text(),'abc');
  const denied=await handle(request(),env,async()=>new Response(null,{status:401}));
  assert.equal(denied.status,401);
});
