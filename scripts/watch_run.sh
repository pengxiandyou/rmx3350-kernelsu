#!/bin/bash
export GH_TOKEN="$1"
RID="$2"
for i in $(seq 1 130); do
  S=$(curl -s -m 60 -x http://127.0.0.1:7890 -H "Authorization: Bearer $GH_TOKEN" \
      "https://api.github.com/repos/pengxiandyou/rmx3350-kernelsu/actions/runs/$RID")
  ST=$(echo "$S" | python -c "import json,sys;d=json.load(sys.stdin);print(d['status'],d['conclusion'])" 2>/dev/null)
  echo "[$(date +%H:%M:%S)] $ST"
  case "$ST" in
    "completed"*) echo "DONE: $ST"; break ;;
  esac
  sleep 20
done
