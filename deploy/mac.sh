#!/bin/bash
set -euo pipefail
umask 077
root="$(cd "$(dirname "$0")/.." && pwd)"
action="${1:-start}"
case "$action" in start|stop|status|logs|migrate-state|sessions|export) ;; *) echo 'Usage: bash deploy/mac.sh [start|stop|status|logs|migrate-state|sessions|export SESSION_ID]' >&2; exit 2;; esac
if [ "$(uname -s)" != Darwin ]; then
  echo 'この起動スクリプトは個人のMac専用です。研究室サーバーでは起動しません。' >&2
  exit 1
fi
if ! command -v docker >/dev/null || ! docker info >/dev/null 2>&1; then
  echo 'Docker Desktopを起動してから再実行してください。' >&2
  exit 1
fi
# 旧環境変数は読込みだけ受け付け、新しい設定名を優先する。
for name in BUNDLE STATE MODEL_VERSION API_TOKEN ACCESS_TEAM ACCESS_AUD ALLOWED_ORIGINS ALLOWED_HOSTS NEXT_OPEN_AT; do
  current="MUSIC_RECSYS_$name"; legacy="OTOLAB_$name"
  if [ -z "${!current:-}" ] && [ -n "${!legacy:-}" ]; then
    printf -v "$current" '%s' "${!legacy}"
    export "$current"
  fi
done
export MUSIC_RECSYS_BUNDLE="${MUSIC_RECSYS_BUNDLE:-$root/bundle}"
export MUSIC_RECSYS_STATE="${MUSIC_RECSYS_STATE:-$HOME/music-recsys/state}"
compose=(docker compose -f "$root/deploy/compose.yaml")
legacy_state=""
if [ -f "$root/state/sessions.sqlite" ]; then legacy_state="$root/state"
elif [ -f "$HOME/OtoLab/state/sessions.sqlite" ]; then legacy_state="$HOME/OtoLab/state"; fi
case "$action" in
  migrate-state)
    if [ -z "$legacy_state" ]; then echo '移行対象の旧ログはありません。'; exit 0; fi
    if [ "$legacy_state" = "$MUSIC_RECSYS_STATE" ] || [ -e "$MUSIC_RECSYS_STATE" ]; then
      echo '移行先が既存です。上書きしません。保存先を確認してください。' >&2; exit 1
    fi
    "${compose[@]}" down
    docker compose -p otolab -f "$root/deploy/compose.yaml" down
    mkdir -p "$(dirname "$MUSIC_RECSYS_STATE")"
    mv "$legacy_state" "$MUSIC_RECSYS_STATE"
    echo "ログ移行完了: $MUSIC_RECSYS_STATE"
    ;;
  start)
    if [ -n "$legacy_state" ] && [ "$legacy_state" != "$MUSIC_RECSYS_STATE" ]; then
      echo '旧ログが残っています。bash deploy/mac.sh migrate-state で停止・移行してから起動してください。' >&2; exit 1
    fi
    if [ -n "${MUSIC_RECSYS_API_TOKEN:-}${MUSIC_RECSYS_ACCESS_TEAM:-}${MUSIC_RECSYS_ACCESS_AUD:-}${MUSIC_RECSYS_ALLOWED_ORIGINS:-}${MUSIC_RECSYS_ALLOWED_HOSTS:-}" ]; then
      echo 'これはローカル確認専用です。遠隔設定がないターミナルで実行してください。' >&2; exit 1
    fi
    if [ ! -f "$MUSIC_RECSYS_BUNDLE/transfer.json" ]; then
      echo 'bundle/transfer.jsonがありません。配布ZIP全体を展開してください。' >&2; exit 1
    fi
    mkdir -p "$MUSIC_RECSYS_STATE"
    "${compose[@]}" config --quiet
    "${compose[@]}" up --build -d
    for ((i=0; i<60; i++)); do
      if "${compose[@]}" exec -T app python -c 'import json, urllib.request; s=json.load(urllib.request.urlopen("http://127.0.0.1:8765/api/status", timeout=2)); assert s["ready"]' >/dev/null 2>&1; then
        echo 'music-recsys起動完了: http://127.0.0.1:8765'
        echo "ログ保存先: $MUSIC_RECSYS_STATE/sessions.sqlite"
        exit 0
      fi
      sleep 2
    done
    echo '起動を確認できませんでした。bash deploy/mac.sh logs で確認してください。' >&2; exit 1
    ;;
  stop) "${compose[@]}" down ;;
  status) "${compose[@]}" ps ;;
  logs) "${compose[@]}" logs --tail 50 app ;;
  sessions) "${compose[@]}" exec -T app python -m music_recsys.webapp.logs --database /state/sessions.sqlite list ;;
  export)
    if [ "$#" -ne 2 ]; then echo 'Usage: bash deploy/mac.sh export SESSION_ID' >&2; exit 2; fi
    "${compose[@]}" exec -T app python -m music_recsys.webapp.logs --database /state/sessions.sqlite export "$2"
    echo "保存先: $MUSIC_RECSYS_STATE/exports/"
    ;;
esac
