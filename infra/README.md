# Infrastructure notes

## Placeholders

The parameter files in this folder are templates. Before deploying, replace:

| Placeholder | Where | Meaning |
|---|---|---|
| `<owner-email>`, `<second-owner-email>` | `main.parameters.json` (`budgetAlertEmails`) | Addresses that receive the monthly budget alerts |
| `<subscription-id>` | `az account set --subscription <subscription-id>` before deploying | Your Azure subscription |

No subscription ID, tenant ID, key or access code is stored in the repository. Secrets are read from `~/.fct-secrets` at deploy time (see `scripts/cloud_deploy.sh` and `.env.cloud.example`).

The image copies `data/processed` and `models` from the build context. The vendor-name tables are not in the repository, so rebuild them first (see `data/README.md`) or the container will start without vendor data.

## Why the Container Apps environment is created via the CLI, not Bicep

A plain ARM/Bicep `PUT` on `Microsoft.App/managedEnvironments` always lands as the lightweight **"Express"**
tier in this subscription — confirmed across api-versions `2025-01-01`, `2026-01-01` and `2023-05-01`, and
regardless of whether a `workloadProfiles` array is set. Express environments reject Azure Files volumes and
`Microsoft.App/managedEnvironments/storages` resources outright:

```
ExpressEnvironmentResourceNotSupported: 'Storage' resources are not supported on express environments.
ExpressVolumeStorageTypeNotSupported: Volume storage type 'AzureFile' is not supported ... on express environments.
```

The environment's `properties.environmentMode` field (`"Express"` vs `"ConsumptionOnly"`) is what actually
determines this, but it is **read-only** from ARM's point of view — a template that tries to set it directly is
rejected ("does not conform to Managed Environment schema"). The only way found to produce a `ConsumptionOnly`
environment is the Azure CLI's `containerapp` extension:

```bash
az containerapp env create -g rg-fct-capstone -n fct-env -l centralindia \
  --environment-mode ConsumptionOnly \
  --logs-destination log-analytics \
  --logs-workspace-id <log analytics customerId> \
  --logs-workspace-key <log analytics shared key>
```

This is a CLI-exclusive code path with no discovered ARM template equivalent - not documented anywhere we
found, confirmed empirically during the first deployment.

## Deploy is therefore three phases

1. **`infra/foundation.bicep`** - Log Analytics workspace + Storage account + Azure Files share. Plain ARM works
   fine for these; nothing here is affected by the Express/ConsumptionOnly issue.
2. **The Container Apps environment** - created imperatively via `az containerapp env create
   --environment-mode ConsumptionOnly`, wired to the Log Analytics workspace from phase 1. Idempotent: if an
   environment with the right name already exists in `ConsumptionOnly` mode, this phase is a no-op; if it
   exists in the wrong mode, it's deleted and recreated (the mode can't be changed in place).
3. **`infra/main.bicep`** - references the environment and storage account from phases 1-2 as `existing`
   resources, then creates the Azure Files storage link, the Container App and the budget alert.

`scripts/cloud_deploy.sh` runs all three phases in order. It reads `GROQ_API_KEY` and `APP_ACCESS_CODE` from `~/.fct-secrets` (never committed, never echoed), builds the
image, pushes it to Azure Container Registry and then deploys. See that script for the exact sequence.

## Other things worth knowing

- `containerApp.dependsOn: [envStorage]` is explicit, not inferred. `template.volumes[].storageName` is a plain
  string match against the storage link's name, not a resource reference, so Bicep does not pick up the
  dependency automatically - without it, the Container App can be created before its volume exists
  (`ManagedEnvironmentStorageNotFound`).
- The image is stored in Azure Container Registry. The Container App pulls it with its system-assigned managed identity (`AcrPull` role assignment in `main.bicep`), so no registry password exists anywhere. ACR Tasks cloud builds are blocked on the Azure for Students subscription, so the image is built locally and pushed.
- `storageAccountName` is a required parameter of `main.bicep` (not computed with `uniqueString()` inside it)
  because that computation lives in `foundation.bicep`; the deploy script passes phase 1's output straight into
  phase 3's `-p storageAccountName=...`.
