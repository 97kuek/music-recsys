#!/bin/bash
set -euo pipefail
umask 077
root="$(cd "$(dirname "$0")/.." && pwd)"
action="${1:-start}"
case "$action" in start|stop|status|logs) ;; *) echo 'Usage: bash deploy/mac.sh [start|stop|status|logs]' >&2; exit 2;; esac
if [ "$(uname -s)" != Darwin ]; then
  echo 'この起動スクリプトは個人のMac専用です。研究室サーバーでは起動しません。' >&2
  exit 1
fi
if ! command -v docker >/dev/null || ! docker info >/dev/null 2>&1; then
  echo 'Docker Desktopを起動してから再実行してください。' >&2
  exit 1
fi
export OTOLAB_BUNDLE="${OTOLAB_BUNDLE:-$root/bundle}"
# 旧配布版のローカルstateがあれば継続利用し、新規版は共通stateを使う。
if [ -z "${OTOLAB_STATE:-}" ]; then
  if [ -f "$root/state/sessions.sqlite" ]; then
    export OTOLAB_STATE="$root/state"
  else
    export OTOLAB_STATE="$HOME/OtoLab/state"
  fi
fi
compose=(docker compose -f "$root/deploy/compose.yaml")
case "$action" in
  start)
    if [ -n "${OTOLAB_ACCESS_TEAM:-}${OTOLAB_ACCESS_AUD:-}${OTOLAB_ALLOWED_ORIGINS:-}${OTOLAB_ALLOWED_HOSTS:-}" ]; then
      echo 'これはローカル確認専用です。遠隔設定がないターミナルで実行してください。' >&2
      exit 1
    fi
    if [ ! -f "$OTOLAB_BUNDLE/transfer.json" ]; then
      echo 'bundle/transfer.jsonがありません。移行アーカイブ全体を展開してください。' >&2
      exit 1
    fi
    mkdir -p "$OTOLAB_STATE"
    "${compose[@]}" config --quiet
    "${compose[@]}" up --build -d
    # 別アプリの同一ポート応答を成功とせず、対象コンテナ内部で準備完了を確認する。
    for ((i=0; i<60; i++)); do
      if "${compose[@]}" exec -T app python -c 'import json, urllib.request; s=json.load(urllib.request.urlopen("http://127.0.0.1:8765/api/status", timeout=2)); assert s["ready"]' >/dev/null 2>&1; then
        echo 'music-recsys起動完了: http://127.0.0.1:8765'
        echo 'デモでは合成音源を使います。実曲の試聴結果とは区別してください。'
        exit 0
      fi
      sleep 2
    done
    echo '起動を確認できませんでした。bash deploy/mac.sh logs で確認してください。' >&2
    exit 1
    ;;
  stop) "${compose[@]}" down ;;
  status) "${compose[@]}" ps ;;
  logs) "${compose[@]}" logs --tail 50 app ;;
esac
