const base = (import.meta.env.VITE_API_BASE || '/api').replace(/\/$/, '');
if (!/^\/(?!\/)[a-zA-Z0-9/_-]+$/.test(base)) throw new Error('API接続は同一Originのパスを指定してください');
export const apiUrl = (path: string) => `${base}/${path.replace(/^\/api\//, '')}`;

export async function request<T>(path: string, method = 'GET', body?: unknown, keepalive = false): Promise<T> {
  const response = await fetch(apiUrl(path), {
    method, keepalive, credentials: 'same-origin', signal: AbortSignal.timeout(path === 'status' ? 5000 : 30000),
    headers: { 'X-Music-App': '1', ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `入力を確認してください (${response.status})`);
  return data as T;
}
