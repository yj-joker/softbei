#!/usr/bin/env bash
# 固化正确的 Python 解释器（避免命中 WindowsApps 的商店 stub 导致 RC=49 静默失败）
PY311="/c/Users/27202/AppData/Local/Programs/Python/Python311/python.exe"
cd "$(dirname "$0")/.." || exit 1
DATASET="$1"; NAME="$2"
"$PY311" -m evaluation.maintenance_eval_cli \
  --dataset "$DATASET" \
  --mode api \
  --result-name "$NAME" \
  > "D:/eval_${NAME}.log" 2>&1
echo "RC=$? -> D:/eval_${NAME}.log , results/${NAME}_summary.json"
