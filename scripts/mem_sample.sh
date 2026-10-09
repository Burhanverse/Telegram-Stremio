#!/usr/bin/env bash
# scripts/mem_sample.sh
# Polls GET /api/admin/mem-stats every INTERVAL seconds (default 30) and appends a CSV row.
#
# The admin API uses a session cookie (the same login as the web UI), not a bearer token,
# so this script logs in with the admin username/password and keeps the cookie in a temp jar.
#
# Usage:   ./scripts/mem_sample.sh HOST_URL ADMIN_USERNAME ADMIN_PASSWORD [OUTPUT_CSV]
#   or:    MEM_USER=admin MEM_PASS=secret ./scripts/mem_sample.sh HOST_URL [OUTPUT_CSV]
# Example: ./scripts/mem_sample.sh http://localhost:8000 admin 's3cret' mem_samples.csv
# Needs:   bash, curl, python3 (for JSON parsing; handles the nested cache stats correctly)

set -u

HOST="${1:-}"
if [ -n "${MEM_USER:-}" ] && [ -n "${MEM_PASS:-}" ]; then
    USER_NAME="$MEM_USER"; PASS="$MEM_PASS"; OUTPUT="${2:-mem_samples.csv}"
else
    USER_NAME="${2:-}"; PASS="${3:-}"; OUTPUT="${4:-mem_samples.csv}"
fi
INTERVAL="${INTERVAL:-30}"

if [ -z "$HOST" ] || [ -z "$USER_NAME" ] || [ -z "$PASS" ]; then
    echo "Usage: $0 HOST_URL ADMIN_USERNAME ADMIN_PASSWORD [OUTPUT_CSV]"
    echo "   or: MEM_USER=... MEM_PASS=... $0 HOST_URL [OUTPUT_CSV]"
    exit 1
fi
command -v python3 >/dev/null 2>&1 || { echo "python3 is required"; exit 1; }

JAR="$(mktemp)"
trap 'rm -f "$JAR"' EXIT

login() {
    curl -s -o /dev/null -c "$JAR" \
        --data-urlencode "username=$USER_NAME" \
        --data-urlencode "password=$PASS" \
        "$HOST/login"
}

fetch() {
    curl -s -b "$JAR" -H "Accept: application/json" "$HOST/api/admin/mem-stats"
}

read -r -d '' PARSE_PY <<'PY'
import json, sys
try:
    d = json.load(sys.stdin)["data"]
except Exception:
    sys.exit(1)

def flat(k, v):
    if isinstance(v, dict):
        return ";".join(flat(k + "." + kk, vv) for kk, vv in v.items())
    return k + "=" + str(v)

caches = ";".join(flat(k, v) for k, v in d.get("caches", {}).items())
cols = [str(d.get("rss_mb", "")), str(d.get("asyncio_tasks", "")), str(d.get("thread_count", ""))]
print(",".join(cols) + ',"' + caches + '"')
PY

# Reads the JSON response on stdin; prints "rss_mb,tasks,threads,caches" or exits non-zero.
parse() {
    python3 -c "$PARSE_PY"
}

if [ ! -f "$OUTPUT" ]; then
    echo "timestamp,rss_mb,asyncio_tasks,thread_count,caches" > "$OUTPUT"
fi

echo "Sampling $HOST/api/admin/mem-stats every ${INTERVAL}s into $OUTPUT (Ctrl+C to stop)..."
login

while true; do
    TS="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
    ROW="$(fetch | parse)" || ROW=""
    if [ -z "$ROW" ]; then
        # Session may have expired (or the first login failed): log in again and retry once.
        login
        ROW="$(fetch | parse)" || ROW=""
    fi
    if [ -n "$ROW" ]; then
        echo "$TS,$ROW" >> "$OUTPUT"
        echo "[$TS] ${ROW%%,\"*}"
    else
        echo "[$TS] No valid response (check HOST_URL and admin credentials)"
    fi
    sleep "$INTERVAL"
done
