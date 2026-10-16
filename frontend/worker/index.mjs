// Static Assets + a narrow same-origin API proxy. No model or research data is bundled.
const endpoints = /^\/api\/(status|catalog|sessions|profile|recommendations|events|playbacks|previews|preview-audio\/[a-zA-Z0-9-]+|audio\/[a-zA-Z0-9-]+|session\/(state|progress|next|leave|finish|export)|session)$/;
const reply = (detail, status) => Response.json({ detail }, { status, headers: { 'Cache-Control': 'no-store' } });

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (!url.pathname.startsWith('/api/') && url.pathname !== '/api') return env.ASSETS.fetch(request);
    if (!endpoints.test(url.pathname)) return reply('not found', 404);
    if (!['GET', 'POST', 'PUT', 'DELETE', 'HEAD'].includes(request.method)) return reply('method not allowed', 405);
    if (!['GET', 'HEAD'].includes(request.method) && (
      request.headers.get('X-Music-App') !== '1' ||
      (request.headers.has('Origin') && request.headers.get('Origin') !== url.origin)
    )) return reply('同じ画面から操作してください', 403);
    if (!env.BACKEND_ORIGIN || !env.UPSTREAM_API_TOKEN) return reply('OtoLabのAPI接続は準備中です。デモ・実モデルはまだ接続されていません。', 503);
    let backend;
    try { backend = new URL(env.BACKEND_ORIGIN); } catch { return reply('API接続設定が不正です', 503); }
    if (backend.protocol !== 'https:' || backend.username || backend.password || backend.pathname !== '/' || backend.search || backend.hash) return reply('API接続設定が不正です', 503);
    const headers = new Headers();
    for (const name of ['content-type', 'range', 'if-range', 'origin', 'x-music-app']) {
      if (request.headers.has(name)) headers.set(name, request.headers.get(name));
    }
    const cookie = request.headers.get('cookie')?.split(';').map(x => x.trim()).find(x => x.startsWith('music_session='));
    if (cookie) headers.set('Cookie', cookie);
    headers.set('Authorization', `Bearer ${env.UPSTREAM_API_TOKEN}`);
    // Read a bounded body. Never proxy arbitrary uploads to the inference service.
    let body;
    if (!['GET', 'HEAD'].includes(request.method)) {
      const reader = request.body?.getReader();
      const chunks = []; let size = 0;
      if (reader) for (;;) {
        const part = await reader.read(); if (part.done) break;
        size += part.value.byteLength;
        if (size > 16384) { await reader.cancel(); return reply('入力が大きすぎます', 413); }
        chunks.push(part.value);
      }
      body = new Blob(chunks);
    }
    try {
      const upstream = await fetch(new URL(url.pathname + url.search, backend), {
        method: request.method, headers, body, redirect: 'manual', signal: AbortSignal.timeout(30000),
      });
      if (upstream.status >= 300 && upstream.status < 400 && upstream.status !== 304) return reply('API接続先を確認してください', 502);
      const responseHeaders = new Headers();
      for (const name of ['content-type', 'content-range', 'accept-ranges', 'set-cookie']) {
        if (upstream.headers.has(name)) responseHeaders.set(name, upstream.headers.get(name));
      }
      responseHeaders.set('Cache-Control', 'no-store');
      responseHeaders.set('X-Content-Type-Options', 'nosniff');
      return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
    } catch { return reply('APIに接続できません。時間をおいて試してください。', 502); }
  },
};
