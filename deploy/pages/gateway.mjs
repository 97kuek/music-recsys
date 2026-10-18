const headers = { 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' };
const offline = () => Response.json({ detail: 'ただいま利用できません。時間をおいて、同じリンクから再確認してください。' }, { status: 503, headers: { ...headers, 'Retry-After': '60' } });

function tunnelOrigin(value) {
  const url = new URL(value);
  if (url.protocol !== 'https:' || url.username || url.password || url.port || url.pathname !== '/' || url.search || url.hash || !/^[a-z0-9-]+\.trycloudflare\.com$/.test(url.hostname)) throw Error('invalid origin');
  return url.origin;
}

async function authenticatedPublisher(request, secret) {
  const token = request.headers.get('authorization') || '';
  if (!/^[0-9a-f]{64}$/.test(secret || '') || request.headers.has('origin')) return false;
  const encoder = new TextEncoder();
  const [a, b] = await Promise.all([token, `Bearer ${secret}`].map(value => crypto.subtle.digest('SHA-256', encoder.encode(value))));
  const left = new Uint8Array(a), right = new Uint8Array(b);
  let difference = 0;
  for (let i = 0; i < left.length; i++) difference |= left[i] ^ right[i];
  return difference === 0;
}

export async function handle(request, env, upstreamFetch = fetch) {
  const path = new URL(request.url).pathname;
  if (path === '/api/_tunnel') {
    if (request.method !== 'PUT') return new Response(null, { status: 405, headers });
    if (!await authenticatedPublisher(request, env.TUNNEL_PUBLISH_TOKEN)) return new Response(null, { status: 401, headers });
    if (!env.TUNNEL_ORIGINS) return offline();
    try {
      const content = await request.text();
      if (content.length > 1024) return new Response(null, { status: 413, headers });
      const body = JSON.parse(content);
      if (Object.keys(body).length !== 1) throw Error('invalid registration');
      const origin = tunnelOrigin(body.origin);
      await env.TUNNEL_ORIGINS.put('active', JSON.stringify({ origin }), { expirationTtl: 600 });
      return Response.json({ registered: true }, { headers });
    } catch { return new Response(null, { status: 400, headers }); }
  }
  if (!request.headers.get('cf-access-jwt-assertion')) return new Response(null, { status: 401, headers });
  if (!path.startsWith('/api/')) return new Response(null, { status: 404, headers });
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 8000);
  try {
    const registered = await env.TUNNEL_ORIGINS.get('active', 'json');
    if (!registered) return offline();
    const target = new URL(request.url);
    target.host = new URL(tunnelOrigin(registered.origin)).host;
    const outgoing = new Headers(request.headers);
    outgoing.delete('host'); outgoing.delete('authorization');
    outgoing.delete('cf-access-client-id'); outgoing.delete('cf-access-client-secret');
    const session = (outgoing.get('cookie') || '').split(';').find(value => value.trim().startsWith('music_session='));
    if (session) outgoing.set('cookie', session.trim()); else outgoing.delete('cookie');
    const response = await upstreamFetch(target, {
      method: request.method, headers: outgoing,
      body: ['GET', 'HEAD'].includes(request.method) ? undefined : request.body,
      redirect: 'manual', signal: controller.signal, duplex: 'half',
      cf: { cacheTtl: 0, cacheEverything: false },
    });
    clearTimeout(timeout); // Streaming audio is not subject to the header timeout.
    if (response.status >= 500) { await response.body?.cancel(); return offline(); }
    const result = new Response(response.body, response);
    result.headers.set('Cache-Control', 'no-store');
    if (result.headers.has('location')) {
      const destination = new URL(result.headers.get('location'), target);
      if (destination.origin !== target.origin) return offline();
      result.headers.set('location', new URL(destination.pathname + destination.search + destination.hash, request.url).href);
    }
    return result;
  } catch { return offline(); }
  finally { clearTimeout(timeout); }
}
