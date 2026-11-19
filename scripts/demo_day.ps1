# scale fct-app up before a demo, wait for readiness, warm every page's API, print the URL.
# Needs: az CLI logged in. Override with -ResourceGroup / -AppName, or env vars RESOURCE_GROUP / APP_NAME.
# If the shared-password gate is on, set $env:APP_ACCESS_CODE so the warm-up requests can sign in -
# without it, every /api/* warm-up call below gets 401 instead of actually warming anything.
param(
    [string]$ResourceGroup = $(if ($env:RESOURCE_GROUP) { $env:RESOURCE_GROUP } else { "rg-fct-capstone" }),
    [string]$AppName = $(if ($env:APP_NAME) { $env:APP_NAME } else { "fct-app" })
)
$ErrorActionPreference = "Stop"
$session = $null

Write-Host "Setting min replicas to 1 on $AppName..."
az containerapp update -g $ResourceGroup -n $AppName --min-replicas 1 --max-replicas 2 | Out-Null

$fqdn = az containerapp show -g $ResourceGroup -n $AppName --query properties.configuration.ingress.fqdn -o tsv
$url = "https://$fqdn"
Write-Host "App URL: $url"

Write-Host "Waiting for /api/ready (up to 5 minutes)..."
$ready = $false
for ($i = 1; $i -le 60; $i++) {
    try {
        Invoke-RestMethod -Uri "$url/api/ready" -TimeoutSec 5 | Out-Null
        $ready = $true
        Write-Host "Ready after ~$($i * 5)s."
        break
    } catch {
        Start-Sleep -Seconds 5
    }
}
if (-not $ready) {
    Write-Error "Still not ready after 5 minutes - check: az containerapp logs show -g $ResourceGroup -n $AppName --follow"
    exit 1
}

if ($env:APP_ACCESS_CODE) {
    Write-Host "Signing in to the access gate..."
    $session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
    try {
        Invoke-WebRequest -Uri "$url/signin" -Method Post -Body @{ code = $env:APP_ACCESS_CODE } -WebSession $session -UseBasicParsing | Out-Null
    } catch {
        Write-Error "Sign-in failed - check `$env:APP_ACCESS_CODE"
        exit 1
    }
}

Write-Host "Warming every page's API..."
$paths = "/api/health", "/api/overview", "/api/vendors", "/api/hubs", "/api/meta/options", "/api/models", "/api/prompts", "/api/audit"
foreach ($path in $paths) {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        $resp = if ($session) { Invoke-WebRequest -Uri "$url$path" -TimeoutSec 15 -UseBasicParsing -WebSession $session } else { Invoke-WebRequest -Uri "$url$path" -TimeoutSec 15 -UseBasicParsing }
        $sw.Stop()
        "{0,-20} {1}  {2:N2}s" -f $path, $resp.StatusCode, ($sw.ElapsedMilliseconds / 1000) | Write-Host
    } catch {
        Write-Host "$path  FAILED (401 if the gate is on and `$env:APP_ACCESS_CODE wasn't set)"
    }
}

Write-Host ""
Write-Host "Demo day is ready: $url"
Write-Host "Run scripts/after_demo.ps1 when the demo is over, to scale back to 0."
