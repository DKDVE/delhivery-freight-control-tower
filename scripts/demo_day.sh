#!/usr/bin/env bash
# scale fct-app up before a demo, wait for readiness, warm every page's API, print the URL.
# Needs: az CLI logged in, curl. Env overrides: RESOURCE_GROUP (default rg-fct-capstone), APP_NAME (default fct-app).
# If the shared-password gate is on, set APP_ACCESS_CODE so the warm-up requests can sign in -
# without it, every /api/* warm-up call below gets 401 instead of actually warming anything.
set -euo pipefail

RESOURCE_GROUP="${RESOURCE_GROUP:-rg-fct-capstone}"
APP_NAME="${APP_NAME:-fct-app}"
COOKIE_JAR=$(mktemp)
trap 'rm -f "$COOKIE_JAR"' EXIT

echo "Setting min replicas to 1 on $APP_NAME..."
az containerapp update -g "$RESOURCE_GROUP" -n "$APP_NAME" --min-replicas 1 --max-replicas 2 >/dev/null

URL="https://$(az containerapp show -g "$RESOURCE_GROUP" -n "$APP_NAME" --query properties.configuration.ingress.fqdn -o tsv)"
echo "App URL: $URL"

echo "Waiting for /api/ready (up to 5 minutes)..."
ready=0
for i in $(seq 1 60); do
    if curl -sf "$URL/api/ready" >/dev/null 2>&1; then
        ready=1
        echo "Ready after ~$((i * 5))s."
        break
    fi
    sleep 5
done
if [ "$ready" -ne 1 ]; then
    echo "Still not ready after 5 minutes - check: az containerapp logs show -g $RESOURCE_GROUP -n $APP_NAME --follow" >&2
    exit 1
fi

if [ -n "${APP_ACCESS_CODE:-}" ]; then
    echo "Signing in to the access gate..."
    curl -sf -c "$COOKIE_JAR" -o /dev/null -X POST "$URL/signin" --data-urlencode "code=$APP_ACCESS_CODE" \
        || { echo "Sign-in failed - check APP_ACCESS_CODE" >&2; exit 1; }
fi

echo "Warming every page's API..."
for path in /api/health /api/overview /api/vendors /api/hubs /api/meta/options /api/models /api/prompts /api/audit; do
    printf "%-20s " "$path"
    curl -sf -b "$COOKIE_JAR" -o /dev/null -w "%{http_code}  %{time_total}s\n" "$URL$path" || echo "FAILED (401 if the gate is on and APP_ACCESS_CODE wasn't set)"
done

echo
echo "Demo day is ready: $URL"
echo "Run scripts/after_demo.sh when the demo is over, to scale back to 0."
