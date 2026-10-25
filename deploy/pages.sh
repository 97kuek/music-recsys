#!/bin/bash
set -euo pipefail
umask 077
root="$(cd "$(dirname "$0")/.." && pwd)"
if [ "$(uname -s)" != Darwin ]; then
  echo 'この起動口は個人Mac専用です。研究室ではTunnelを起動しません。' >&2; exit 1
fi
if [[ ! "${MUSIC_RECSYS_PAGES_URL:-}" =~ ^https://[a-z0-9-]+\.pages\.dev$ ]]; then
  echo 'MUSIC_RECSYS_PAGES_URLへ固定のhttps://PROJECT.pages.devを設定してください。' >&2; exit 1
fi
if [[ ! "${MUSIC_RECSYS_PUBLISH_TOKEN:-}" =~ ^[0-9a-f]{64}$ ]] || ! command -v cloudflared >/dev/null; then
  echo 'cloudflaredと非公開の64桁hex登録トークンが必要です。docs/ui.mdを確認してください。' >&2; exit 1
fi
export MUSIC_RECSYS_ALLOWED_ORIGINS="$MUSIC_RECSYS_PAGES_URL"
export MUSIC_RECSYS_ALLOWED_HOSTS="${MUSIC_RECSYS_PAGES_URL#https://}"
bash "$root/deploy/mac.sh" start-remote
log_path="$(mktemp -t music-recsys-tunnel.XXXXXX)"
curl_path="$(mktemp -t music-recsys-publish.XXXXXX)"
tunnel_pid=""
cleanup() {
  if [ -n "$tunnel_pid" ]; then kill "$tunnel_pid" 2>/dev/null || true; wait "$tunnel_pid" 2>/dev/null || true; fi
  rm -f "$log_path" "$curl_path"
}
trap cleanup EXIT
trap 'exit 130' INT TERM
cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8765 --http-host-header "${MUSIC_RECSYS_PAGES_URL#https://}" >"$log_path" 2>&1 &
tunnel_pid=$!
tunnel_url=""
for ((attempt=0; attempt<60; attempt++)); do
  tunnel_url="$(awk 'match($0,/https:\/\/[a-z0-9-]+\.trycloudflare\.com/) {print substr($0,RSTART,RLENGTH); exit}' "$log_path")"
  if [ -n "$tunnel_url" ]; then break; fi
  if ! kill -0 "$tunnel_pid" 2>/dev/null; then echo 'Quick Tunnelの起動を確認できませんでした。' >&2; exit 1; fi
  sleep 1
done
if [ -z "$tunnel_url" ]; then echo 'Quick TunnelのURL取得が時間切れになりました。' >&2; exit 1; fi
# Tokens stay out of process arguments, browser builds and diagnostic output.
printf 'url = "%s/api/_tunnel"\nrequest = "PUT"\nheader = "Authorization: Bearer %s"\nheader = "Content-Type: application/json"\ndata = "{\\"origin\\":\\"%s\\"}"\nsilent\nshow-error\nfail\n' "$MUSIC_RECSYS_PAGES_URL" "$MUSIC_RECSYS_PUBLISH_TOKEN" "$tunnel_url" >"$curl_path"
if ! curl --config "$curl_path" --max-time 20 --output /dev/null; then
  echo 'Pagesへの接続先登録に失敗しました。Accessの登録用パス設定・KV・トークンを確認してください。' >&2; exit 1
fi
echo "配布する固定リンク: $MUSIC_RECSYS_PAGES_URL/"
echo '接続先の反映には時間がかかる場合があります。このターミナルを閉じると遠隔接続が止まります。'
heartbeat=0
while kill -0 "$tunnel_pid" 2>/dev/null; do
  sleep 1
  heartbeat=$((heartbeat + 1))
  if [ "$heartbeat" -ge 180 ]; then
    if ! curl --config "$curl_path" --max-time 20 --output /dev/null; then
      echo '接続先の更新に失敗しました。登録の有効期限が切れると休止案内へ切り替わります。' >&2
    fi
    heartbeat=0
  fi
done
wait "$tunnel_pid"
