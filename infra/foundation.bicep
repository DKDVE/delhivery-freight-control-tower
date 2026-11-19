// Freight Control Tower - foundation phase. Deployed before the Container Apps environment.
// A raw ARM PUT on Microsoft.App/managedEnvironments always lands as the "Express" tier in this subscription,
// which rejects Azure Files volumes - only `az containerapp env create --environment-mode ConsumptionOnly`
// (CLI-exclusive, no ARM template equivalent found) produces a usable environment. So the environment is
// created imperatively between this deployment and infra/main.bicep; see scripts/ or README "Cloud deployment".
//   az deployment group create -g rg-fct-capstone -f infra/foundation.bicep -p infra/main.parameters.json

@description('Azure region. Team default: centralindia.')
param location string = 'centralindia'

@description('Base name used to derive resource names.')
param appBaseName string = 'fct'

var tags = { project: 'fct-capstone' }
var workspaceName = '${appBaseName}-logs'
// Storage account names must be 3-24 chars, lowercase letters/digits only.
var storageAccountName = toLower('${appBaseName}audit${uniqueString(resourceGroup().id)}')
var fileShareName = 'audit'

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: workspaceName
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: storageAccountName
  location: location
  tags: tags
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    supportsHttpsTrafficOnly: true
  }
}

resource fileServices 'Microsoft.Storage/storageAccounts/fileServices@2023-01-01' = {
  parent: storage
  name: 'default'
}

resource auditShare 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-01-01' = {
  parent: fileServices
  name: fileShareName
  properties: {
    shareQuota: 5 // GiB - audit JSONL is tiny; raise if the team's demo period runs long
  }
}

output logAnalyticsWorkspaceName string = logAnalytics.name
output storageAccountName string = storage.name
output fileShareName string = fileShareName
