#!/usr/bin/env bash
# build locally with a native Docker Engine (docker-ce in WSL, NOT Docker Desktop - ACR Tasks
# cloud build is blocked on this "Azure for Students" subscription: TasksOperationsNotAllowed), push to ACR,
# deploy to Azure Container Apps. The Container App pulls the image using its own system-assigned managed
# identity (AcrPull role, wired in infra/main.bicep) - no registry secret is stored anywhere.
#
# Needs a secrets file at ~/.fct-secrets (chmod 600, never committed) that exports:
#   GROQ_API_KEY       - Groq API key
#   APP_ACCESS_CODE    - shared-password gate code. Leave unset/empty to deploy with the gate
#                        disabled (same as omitting -p appAccessCode below).
#
# Secrets are sourced and used only inside this script. Nothing secret is echoed, logged, or passed as a
# command-line argument that would show up in process listings (az CLI masks @secure() bicep parameters in
# its own output).
#
# Run this from inside a real Linux shell (WSL, a Linux CI runner, etc.) - not from a Windows shell driving
# `wsl ... -- bash -c "..."` with secrets interpolated into the command text itself, which can get mangled or
# leaked by the Windows-side shell's own argument handling before it ever reaches WSL. Write secret-touching
# commands into a script file (like this one) and run `wsl bash /path/to/script.sh`, or run natively in WSL.
set -euo pipefail

source ~/.fct-secrets
cd "$(dirname "$0")/.."

SHA=$(git rev-parse --short HEAD)
ACR_NAME=fctcapstoneacr
ACR_LOGIN_SERVER="${ACR_NAME}.azurecr.io"
IMAGE="${ACR_LOGIN_SERVER}/fct-app:${SHA}"
RG=rg-fct-capstone
LOC=centralindia
ENV_NAME=fct-env

echo "=== Building ${IMAGE} locally (docker-ce in WSL) ==="
docker build -t "$IMAGE" -t "${ACR_LOGIN_SERVER}/fct-app:latest" .

echo "=== Logging in to ACR (az CLI identity, no stored password) ==="
az acr login -n "$ACR_NAME"

echo "=== Pushing ${IMAGE} ==="
docker push "$IMAGE"
docker push "${ACR_LOGIN_SERVER}/fct-app:latest"

echo "=== Phase 1: foundation (Log Analytics + Storage) ==="
az deployment group create \
  -g "$RG" -n "fct-foundation" \
  -f infra/foundation.bicep \
  -p infra/foundation.parameters.json \
  -o json > /tmp/fct_foundation_result.json

STORAGE_ACCOUNT=$(python3 -c "import json; print(json.load(open('/tmp/fct_foundation_result.json'))['properties']['outputs']['storageAccountName']['value'])")
LA_WORKSPACE=$(python3 -c "import json; print(json.load(open('/tmp/fct_foundation_result.json'))['properties']['outputs']['logAnalyticsWorkspaceName']['value'])")
echo "storage account: $STORAGE_ACCOUNT | log analytics workspace: $LA_WORKSPACE"

echo "=== Phase 2: Container Apps environment (CLI-exclusive ConsumptionOnly - see infra/README.md) ==="
# Current az/ARM API versions no longer surface properties.environmentMode on `env show` (it comes back
# null/absent even for a correctly-created ConsumptionOnly environment) - workloadProfiles is the real
# signature instead: null/absent means ConsumptionOnly, a populated array means Consumption+Dedicated.
if az containerapp env show -g "$RG" -n "$ENV_NAME" >/dev/null 2>&1; then
  PROFILES=$(az containerapp env show -g "$RG" -n "$ENV_NAME" --query "properties.workloadProfiles" -o tsv)
  if [ -n "$PROFILES" ]; then
    echo "environment exists with workload profiles (Consumption+Dedicated, not ConsumptionOnly) - deleting and recreating"
    az containerapp env delete -g "$RG" -n "$ENV_NAME" --yes
    NEED_CREATE=1
  else
    NEED_CREATE=0
  fi
else
  NEED_CREATE=1
fi

if [ "${NEED_CREATE:-1}" = "1" ]; then
  LA_CUSTOMER_ID=$(az monitor log-analytics workspace show -g "$RG" -n "$LA_WORKSPACE" --query customerId -o tsv)
  LA_SHARED_KEY=$(az monitor log-analytics workspace get-shared-keys -g "$RG" -n "$LA_WORKSPACE" --query primarySharedKey -o tsv)
  az containerapp env create \
    -g "$RG" -n "$ENV_NAME" -l "$LOC" \
    --environment-mode ConsumptionOnly \
    --logs-destination log-analytics \
    --logs-workspace-id "$LA_CUSTOMER_ID" \
    --logs-workspace-key "$LA_SHARED_KEY"
  unset LA_CUSTOMER_ID LA_SHARED_KEY
fi

FINAL_PROFILES=$(az containerapp env show -g "$RG" -n "$ENV_NAME" --query "properties.workloadProfiles" -o tsv)
if [ -n "$FINAL_PROFILES" ]; then
  echo "ERROR: environment still has workload profiles (not ConsumptionOnly), aborting before phase 3" >&2
  exit 1
fi

echo "=== Phase 3: main (storage link, Container App, budget) ==="
az deployment group create \
  -g "$RG" -n "fct-deploy-${SHA}" \
  -f infra/main.bicep \
  -p infra/main.parameters.json \
  -p storageAccountName="$STORAGE_ACCOUNT" \
  -p containerImage="$IMAGE" \
  -p acrName="$ACR_NAME" \
  -p llmApiKey="$GROQ_API_KEY" \
  -p appAccessCode="${APP_ACCESS_CODE:-}" \
  -o json > /tmp/fct_deploy_result.json

echo "=== Deployment outputs ==="
python3 -c "import json; d=json.load(open('/tmp/fct_deploy_result.json')); print(json.dumps(d.get('properties',{}).get('outputs',{}), indent=2))"

echo "DONE: image=${IMAGE}"
