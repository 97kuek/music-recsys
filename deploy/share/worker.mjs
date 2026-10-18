// A fixed, Access-protected entry point; all study data remains on the Mac.
const unavailable = 'ただいま利用できません。時間をおいて、同じリンクからもう一度アクセスしてください。';

function offline(request) {
  const headers = { 'Cache-Control': 'no-store', 'Retry-After': '60', 'X-Content-Type-Options': 'nosniff' };
  if (new URL(request.url).pathname.startsWith('/api/')) {
    return Response.json({ detail: unavailable }, { status: 503, headers });
  }
  headers['Content-Type'] = 'text/html; charset=utf-8';
  headers['Content-Security-Policy'] = "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'";
  return new Response(`<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>music-recsys｜現在休止中</title><style>body{font:16px/1.8 system-ui,sans-serif;background:#fafbf8;color:#202b24;max-width:34em;margin:15vh auto;padding:24px}h1{font-size:24px}a{display:inline-block;padding:12px 24px;border-radius:24px;background:#365c3f;color:white;text-decoration:none}</style><main><p>music-recsys</p><h1>ただいま利用できません</h1><p>サービスを休止しているか、接続を確認できない状態です。起動後もこのリンクをそのまま使えます。</p><p>${unavailable}</p><a href="/">状態を再確認する</a></main></html>`, { status: 503, headers });
}

export async function handle(request, env, upstreamFetch = fetch) {
  // Access must protect the deployment URL; FastAPI also verifies this signed JWT.
  if (!request.headers.get('cf-access-jwt-assertion')) {
    return new Response('Access認証が必要です', { status: 401, headers: { 'Cache-Control': 'no-store' } });
  }
  let origin;
  try {
    origin = new URL(env.TUNNEL_ORIGIN);
    if (origin.protocol !== 'https:' || origin.username || origin.password || origin.port || origin.pathname !== '/' || origin.search || origin.hash ||
        !/^[a-z0-9-]+\.(ngrok-free\.app|ngrok-free\.dev|ngrok\.app)$/.test(origin.hostname)) throw new Error('invalid origin');
  } catch { return offline(request); }
  const target = new URL(request.url);
  target.protocol = origin.protocol; target.host = origin.host;
  const headers = new Headers(request.headers);
  headers.delete('host'); headers.delete('authorization');
  headers.delete('cf-access-client-id'); headers.delete('cf-access-client-secret');
  // Only the session cookie is needed by the Mac; Access credentials stay at the edge.
  const session = (headers.get('cookie') || '').split(';').find(value => value.trim().startsWith('music_session='));
  if (session) headers.set('cookie', session.trim()); else headers.delete('cookie');
  headers.set('ngrok-skip-browser-warning', '1');
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 8000);
  try {
    const response = await upstreamFetch(target, {
      method: request.method, headers,
      body: ['GET', 'HEAD'].includes(request.method) ? undefined : request.body,
      redirect: 'manual', signal: controller.signal,
      cf: { cacheTtl: 0, cacheEverything: false },
      duplex: 'half',
    });
    clearTimeout(timeout); // Audio streaming must remain usable beyond the header timeout.
    if (response.status >= 500 || response.headers.has('ngrok-error-code')) {
      await response.body?.cancel();
      return offline(request);
    }
    const result = new Response(response.body, response);
    result.headers.set('Cache-Control', 'no-store');
    const location = result.headers.get('location');
    if (location) {
      const redirect = new URL(location, target);
      if (redirect.origin !== origin.origin) return offline(request);
      result.headers.set('location', new URL(redirect.pathname + redirect.search + redirect.hash, request.url).href);
    }
    return result;
  } catch { return offline(request); }
  finally { clearTimeout(timeout); }
}

export default { fetch: (request, env) => handle(request, env) };
