#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if bash deploy/mac.sh start; then
  open http://127.0.0.1:8765
else
  echo '起動できませんでした。表示されたエラーを確認してください。'
  read -r -p 'Enterで閉じます ' reply
  exit 1
fi
