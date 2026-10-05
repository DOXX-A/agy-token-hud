# Antigravity Tokens HUD - Windows Installer
$ErrorActionPreference = "Stop"

Write-Host "🚀 Installing Antigravity Tokens HUD for Google Antigravity 2.0..." -ForegroundColor Cyan

$InstallDir = "$env:USERPROFILE\.antigravity-tokens-hud"
$SourceDir = $PSScriptRoot

# Ensure target directory exists
if (-not (Test-Path $InstallDir)) {
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
}

# Stop any running instances
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $_.Name -like "*node*" -and $_.CommandLine -like "*antigravity-tokens-hud*"
} | ForEach-Object {
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}

# Copy files
$Files = @("index.js", "token_stats.py", "client_widget.js", "config.json", "package.json", "run.vbs", "uninstall.ps1")
foreach ($file in $Files) {
    $src = Join-Path $SourceDir $file
    if (Test-Path $src) {
        Copy-Item -Path $src -Destination $InstallDir -Force
    }
}
Write-Host "✅ Files copied to $InstallDir." -ForegroundColor Green

# Create Startup shortcut in Windows Startup folder for auto-start
$startupFolder = [Environment]::GetFolderPath("Startup")
$startupVbs = Join-Path $startupFolder "antigravity-tokens-hud.vbs"
$vbsSource = Join-Path $InstallDir "run.vbs"

if (Test-Path $vbsSource) {
    Copy-Item -Path $vbsSource -Destination $startupVbs -Force
    Write-Host "✅ Created Startup shortcut: $startupVbs" -ForegroundColor Green
}

# Launch background daemon immediately
Start-Process -FilePath "wscript.exe" -ArgumentList "`"$startupVbs`""
Write-Host "✅ Started background daemon." -ForegroundColor Green

Write-Host "`n✨ Antigravity Tokens HUD is successfully installed and running!" -ForegroundColor Cyan
Write-Host "Open or focus Google Antigravity 2.0 to see the real-time token & quota HUD in the sidebar." -ForegroundColor Green
