<#
.SYNOPSIS
    Build and deploy the ComplyLens Databricks App.

.DESCRIPTION
    Builds the React SPA, syncs the source to the workspace, and deploys the app.

    The build step is mandatory: FastAPI serves the SPA from frontend/dist, and that
    directory is gitignored, so it must exist locally before syncing.

.EXAMPLE
    ./deploy.ps1 -Profile complylens
    ./deploy.ps1 -Profile complylens -AppName complylens -SkipBuild
#>
param(
    [string]$Profile = "complylens",
    [string]$AppName = "complylens",
    [string]$WorkspacePath = "",
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }

# --- preflight -------------------------------------------------------------
Step "Preflight"

if (-not (Get-Command databricks -ErrorAction SilentlyContinue)) {
    throw "Databricks CLI not found. Install it, then run: databricks auth login --host <workspace-url> --profile $Profile"
}

$profiles = databricks auth profiles 2>&1 | Out-String
if ($profiles -notmatch [regex]::Escape($Profile)) {
    throw "Profile '$Profile' not found. Run: databricks auth login --host <workspace-url> --profile $Profile"
}
Write-Host "  CLI:     $(databricks --version)"
Write-Host "  Profile: $Profile"

if (-not $WorkspacePath) {
    $me = (databricks current-user me --profile $Profile -o json | ConvertFrom-Json).userName
    if (-not $me) { throw "Could not resolve the current user. Is the profile still authenticated?" }
    $WorkspacePath = "/Workspace/Users/$me/$AppName"
}
Write-Host "  Target:  $WorkspacePath"

# --- build -----------------------------------------------------------------
if (-not $SkipBuild) {
    Step "Building the frontend"
    if (-not (Test-Path node_modules)) {
        npm install
        if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
    }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
}

if (-not (Test-Path "frontend/dist/index.html")) {
    throw "frontend/dist/index.html is missing. Run without -SkipBuild."
}

# Databricks Apps rejects any single file over 10 MB.
$oversized = Get-ChildItem -Recurse -File frontend/dist | Where-Object { $_.Length -gt 10MB }
if ($oversized) {
    throw "These files exceed the 10 MB Apps limit: $($oversized.Name -join ', ')"
}
$total = [math]::Round((Get-ChildItem -Recurse -File frontend/dist | Measure-Object Length -Sum).Sum / 1KB, 1)
Write-Host "  Bundle:  $total KB across $((Get-ChildItem -Recurse -File frontend/dist).Count) files"

# --- sync ------------------------------------------------------------------
Step "Syncing source to the workspace"
databricks sync . $WorkspacePath --profile $Profile --full `
    --exclude "node_modules/**" --exclude "__pycache__/**" --exclude "*.pyc"
if ($LASTEXITCODE -ne 0) { throw "databricks sync failed" }

# --- deploy ----------------------------------------------------------------
Step "Deploying"

$exists = databricks apps get $AppName --profile $Profile 2>&1 | Out-String
if ($exists -match "does not exist|RESOURCE_DOES_NOT_EXIST|not found") {
    Write-Host "  App '$AppName' does not exist; creating it."
    databricks apps create $AppName --profile $Profile
    if ($LASTEXITCODE -ne 0) { throw "databricks apps create failed" }
}

databricks apps deploy $AppName --source-code-path $WorkspacePath --profile $Profile
if ($LASTEXITCODE -ne 0) { throw "databricks apps deploy failed" }

# --- report ----------------------------------------------------------------
Step "Deployed"
$app = databricks apps get $AppName --profile $Profile -o json | ConvertFrom-Json
Write-Host "  URL:   $($app.url)"
Write-Host "  State: $($app.compute_status.state)"

Write-Host @"

Next:
  1. Open the app URL and check /api/health reports "ok".
     If it reports "misconfigured", add the resource bindings:
        Genie Agent   -> key "genie-space"   -> Can run
        SQL warehouse -> key "sql-warehouse" -> Can use
     then redeploy.

  2. Grant the app's service principal read access to the serving views:
        GRANT USE CATALOG ON CATALOG <catalog> TO ``<app-sp>``;
        GRANT USE SCHEMA  ON SCHEMA <catalog>.complylens_genie TO ``<app-sp>``;
        GRANT SELECT      ON SCHEMA <catalog>.complylens_genie TO ``<app-sp>``;

  3. Warm the SQL warehouse before demoing - the first Genie query against a cold
     2X-Small warehouse can take well over a minute.

  Free Edition stops apps 24h after deploy. Restart before recording or submitting.
"@ -ForegroundColor Gray
