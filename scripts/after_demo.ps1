# scale fct-app back to zero after a demo, so it stops burning the student budget while idle.
param(
    [string]$ResourceGroup = $(if ($env:RESOURCE_GROUP) { $env:RESOURCE_GROUP } else { "rg-fct-capstone" }),
    [string]$AppName = $(if ($env:APP_NAME) { $env:APP_NAME } else { "fct-app" })
)
$ErrorActionPreference = "Stop"

Write-Host "Setting min replicas back to 0 on $AppName..."
az containerapp update -g $ResourceGroup -n $AppName --min-replicas 0 --max-replicas 2 | Out-Null
Write-Host "Done. The app will scale to zero once idle; the next request pays the cold-start cost again."
