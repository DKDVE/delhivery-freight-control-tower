// Freight Control Tower - Azure Container Apps. One container: FastAPI serves the API and the built
// React SPA. Three-phase deploy into an existing resource group (az group create first):
//   1. az deployment group create -g rg-fct-capstone -f infra/foundation.bicep -p infra/main.parameters.json
//   2. az containerapp env create -g rg-fct-capstone -n fct-env -l centralindia --environment-mode ConsumptionOnly
//      --logs-workspace-id <foundation output> --logs-workspace-key <workspace shared key>
//      (CLI-exclusive: a raw ARM PUT on managedEnvironments always lands as the unusable "Express" tier here -
//      see infra/foundation.bicep's header comment)
//   3. az deployment group what-if|create -g rg-fct-capstone -f infra/main.bicep -p infra/main.parameters.json
// Secrets (llmApiKey, appAccessCode) are never in main.parameters.json - pass them with --parameters at deploy time
// or from a pipeline secret store (see README "Cloud deployment" for the GitHub Actions / OIDC option).

@description('Azure region. Team default: centralindia. If the student subscription blocks it, pass the nearest allowed region and say so in the PR.')
param location string = 'centralindia'

@description('Base name used to derive resource names (environment, app, workspace, storage).')
param appBaseName string = 'fct'

@description('The storage account created by infra/foundation.bicep (its "storageAccountName" output).')
param storageAccountName string

@description('Container image, e.g. fctcapstoneacr.azurecr.io/fct-app:<tag> (Azure Container Registry).')
param containerImage string

@description('Azure Container Registry name (no domain), e.g. fctcapstoneacr. When set, the Container App pulls via its system-assigned managed identity + an AcrPull role assignment - no registry secret needed.')
param acrName string = ''

@secure()
@description('Groq API key, stored as the Container Apps secret "llm-api-key".')
param llmApiKey string

@secure()
@description('Shared-password access gate code, stored as the Container Apps secret "app-access-code". Empty disables the gate - never leave it empty on a publicly-reachable deployment.')
param appAccessCode string = ''

@description('LLM endpoint. Default: Groq direct (OpenAI-compatible). freellmapi stays local-only.')
param llmBaseUrl string = 'https://api.groq.com/openai/v1'

@description('Pinned model id. Do not use auto routing.')
param llmModel string = 'openai/gpt-oss-120b'

@description('0 = live mode (needs llmApiKey); 1 = force DEMO mode regardless of the key.')
@allowed([0, 1])
param demoMode int = 0

@description('Comma-separated ALLOWED_HOSTS allow-list. Leave as "*" until the first deploy reveals the app FQDN, then tighten it with az containerapp update.')
param allowedHosts string = '*'

@description('Min replicas. 0 by default (scale to zero between demos); scripts/demo_day.sh sets this to 1 before a demo and scripts/after_demo.sh sets it back to 0.')
@minValue(0)
@maxValue(2)
param minReplicas int = 0

@description('Max replicas.')
@minValue(1)
@maxValue(2)
param maxReplicas int = 2

@description('Monthly budget alert amount in INR on this resource group.')
param budgetAmountInr int = 1000

@description('Email(s) notified at 50%/80%/100% of the budget.')
param budgetAlertEmails array

@description('First-of-month start date for the budget. utcNow() may only be a parameter default, not used inline in a resource.')
param budgetStartDate string = utcNow('yyyy-MM-01')

var tags = { project: 'fct-capstone' }
var envName = '${appBaseName}-env'
var appName = '${appBaseName}-app'
var fileShareName = 'audit'
var volumeName = 'audit-share'
var auditMountPath = '/mnt/audit'

// Created imperatively (az containerapp env create --environment-mode ConsumptionOnly) - see the header comment.
resource environment 'Microsoft.App/managedEnvironments@2023-05-01' existing = {
  name: envName
}

// Created by infra/foundation.bicep.
resource storage 'Microsoft.Storage/storageAccounts@2023-01-01' existing = {
  name: storageAccountName
}

// Only deployed/resolved when acrName is set (ACR pull-via-managed-identity path).
resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = if (!empty(acrName)) {
  name: acrName
}

resource envStorage 'Microsoft.App/managedEnvironments/storages@2023-05-01' = {
  parent: environment
  name: volumeName
  properties: {
    azureFile: {
      accountName: storage.name
      accountKey: storage.listKeys().keys[0].value
      shareName: fileShareName
      accessMode: 'ReadWrite'
    }
  }
}

// ------------------------------------------------------------------ Container App
resource containerApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: appName
  location: location
  tags: tags
  // Needed only for the ACR path (identity-based registry pull below); harmless no-op otherwise.
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    environmentId: environment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto' // plain HTTP/1.1 chunked works for the Ask-the-data SSE stream; no need for http2
        allowInsecure: false
      }
      registries: !empty(acrName) ? [
        {
          server: acr.properties.loginServer
          identity: 'system'
        }
      ] : []
      secrets: concat(
        [{ name: 'llm-api-key', value: llmApiKey }],
        empty(appAccessCode) ? [] : [{ name: 'app-access-code', value: appAccessCode }]
      )
    }
    template: {
      containers: [
        {
          name: 'fct-app'
          image: containerImage
          resources: {
            cpu: json('1.0')
            memory: '2Gi'
          }
          env: concat([
            { name: 'PORT', value: '8000' }
            { name: 'DEMO_MODE', value: string(demoMode) }
            { name: 'LLM_BASE_URL', value: llmBaseUrl }
            { name: 'LLM_MODEL', value: llmModel }
            { name: 'LLM_API_KEY', secretRef: 'llm-api-key' }
            { name: 'AUDIT_LOG_DIR', value: auditMountPath }
            { name: 'ALLOWED_HOSTS', value: allowedHosts }
          ], empty(appAccessCode) ? [] : [{ name: 'APP_ACCESS_CODE', secretRef: 'app-access-code' }])
          volumeMounts: [
            { volumeName: volumeName, mountPath: auditMountPath }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: { path: '/api/health', port: 8000 }
              initialDelaySeconds: 10
              periodSeconds: 10
              failureThreshold: 3
            }
            {
              // measured cold start (tables + 6 model pipelines + SHAP explainers + cached payloads) is ~13s
              // locally; 20s initial delay + 5s period x 10 failures gives ~70s of grace before traffic is cut.
              type: 'Readiness'
              httpGet: { path: '/api/ready', port: 8000 }
              initialDelaySeconds: 20
              periodSeconds: 5
              failureThreshold: 10
            }
            {
              // generous: covers a slow cold pull/start on a fresh revision without flapping the other probes.
              type: 'Startup'
              httpGet: { path: '/api/health', port: 8000 }
              initialDelaySeconds: 5
              periodSeconds: 5
              failureThreshold: 30
            }
          ]
        }
      ]
      volumes: [
        { name: volumeName, storageType: 'AzureFile', storageName: volumeName }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
        rules: [
          {
            name: 'http-concurrency'
            http: { metadata: { concurrentRequests: '20' } }
          }
        ]
      }
    }
  }
  // template.volumes[].storageName is a plain string match against envStorage's name, not a resource
  // reference, so bicep infers no implicit dependency - without this, the Container App can be created
  // before the storage link exists ("ManagedEnvironment Storage 'audit-share' was not found").
  dependsOn: [envStorage]
}

// ------------------------------------------------------------------ ACR pull permission for the Container App's identity
resource acrPullRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(acrName)) {
  name: guid(acr.id, containerApp.id, 'AcrPull')
  scope: acr
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d') // AcrPull
    principalId: containerApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// ------------------------------------------------------------------ Budget alert on the resource group
resource budget 'Microsoft.Consumption/budgets@2024-08-01' = {
  name: '${appBaseName}-capstone-budget'
  properties: {
    category: 'Cost'
    amount: budgetAmountInr
    timeGrain: 'Monthly'
    timePeriod: {
      startDate: budgetStartDate
    }
    notifications: {
      Actual_50: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 50
        contactEmails: budgetAlertEmails
        thresholdType: 'Actual'
      }
      Actual_80: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 80
        contactEmails: budgetAlertEmails
        thresholdType: 'Actual'
      }
      Actual_100: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 100
        contactEmails: budgetAlertEmails
        thresholdType: 'Actual'
      }
    }
  }
}

// ------------------------------------------------------------------ outputs
output appUrl string = 'https://${containerApp.properties.configuration.ingress.fqdn}'
output containerAppName string = containerApp.name
output environmentName string = environment.name
output storageAccountName string = storage.name
output fileShareName string = fileShareName
output budgetName string = budget.name
