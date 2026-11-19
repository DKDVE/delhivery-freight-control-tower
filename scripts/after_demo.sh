#!/usr/bin/env bash
# scale fct-app back to zero after a demo, so it stops burning the student budget while idle.
set -euo pipefail

RESOURCE_GROUP="${RESOURCE_GROUP:-rg-fct-capstone}"
APP_NAME="${APP_NAME:-fct-app}"

echo "Setting min replicas back to 0 on $APP_NAME..."
az containerapp update -g "$RESOURCE_GROUP" -n "$APP_NAME" --min-replicas 0 --max-replicas 2 >/dev/null
echo "Done. The app will scale to zero once idle; the next request pays the cold-start cost again."
