#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
unset MUSIC_RECSYS_SNAPSHOT_ID MUSIC_RECSYS_SPLIT_ID MUSIC_RECSYS_REPORT_PATH
export OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2
export JUPYTER_RUNTIME_DIR="$PWD/data/jupyter/runtime"
export IPYTHONDIR="$PWD/data/jupyter/ipython"
mkdir -p "$JUPYTER_RUNTIME_DIR" "$IPYTHONDIR"
chmod 700 "$JUPYTER_RUNTIME_DIR"
exec .venv/bin/jupyter lab --no-browser --ip=127.0.0.1 --port=8888 "$@"
