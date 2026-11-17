# Installs the QuickBooks read-only MCP server for the Claude desktop app (Windows).
# Copies the server to a local folder and adds it to Claude's config, keeping a backup.
# Run from this folder:  powershell -ExecutionPolicy Bypass -File install.ps1
$ErrorActionPreference = "Stop"

$Src = Join-Path $PSScriptRoot "qbo_mcp.py"
if (-not (Test-Path $Src)) { throw "qbo_mcp.py not found next to install.ps1" }

# Find a real Python 3.8+ (not the Microsoft Store placeholder in WindowsApps)
$Py = $null
$Candidates = @(
    @{ Exe = "py";      Args = @("-3") },
    @{ Exe = "python";  Args = @() },
    @{ Exe = "python3"; Args = @() }
)
foreach ($c in $Candidates) {
    try {
        $probe = $c.Args + @("-c", "import sys; assert sys.version_info >= (3, 8); print(sys.executable)")
        $exe = & $c.Exe @probe 2>$null
        if ($LASTEXITCODE -eq 0 -and $exe -and ($exe -notmatch "WindowsApps")) { $Py = $exe.Trim(); break }
    } catch { }
}
if (-not $Py) { throw "Python 3.8 or later not found. Install it from python.org (tick 'Add python.exe to PATH'), then run this again." }
Write-Host "Using Python: $Py"

$DestDir = Join-Path $env:LOCALAPPDATA "qbo-readonly"
New-Item -ItemType Directory -Force -Path $DestDir | Out-Null
$Dest = Join-Path $DestDir "qbo_mcp.py"
Copy-Item $Src $Dest -Force
Write-Host "Copied server to: $Dest"

# Claude desktop config: the standard location, plus the packaged-app location if it exists
$Configs = @(Join-Path $env:APPDATA "Claude\claude_desktop_config.json")
Get-ChildItem (Join-Path $env:LOCALAPPDATA "Packages") -Directory -Filter "Claude*" -ErrorAction SilentlyContinue | ForEach-Object {
    $alt = Join-Path $_.FullName "LocalCache\Roaming\Claude\claude_desktop_config.json"
    if (Test-Path (Split-Path $alt)) { $Configs += $alt }
}

$Merge = @'
import json, os, sys
path, py, server = sys.argv[1:4]
cfg = {}
if os.path.exists(path) and os.path.getsize(path):
    with open(path, encoding="utf-8-sig") as f:
        cfg = json.load(f)
cfg.setdefault("mcpServers", {})["qbo-readonly"] = {"command": py, "args": [server]}
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, "w", encoding="utf-8") as f:
    json.dump(cfg, f, indent=2)
print("Added 'qbo-readonly' to", path)
'@

foreach ($Config in $Configs) {
    if (Test-Path $Config) {
        Copy-Item $Config "$Config.bak-$(Get-Date -Format yyyyMMdd-HHmmss)"
        Write-Host "Backed up $Config"
    }
    $Merge | & $Py - $Config $Py $Dest
}

Write-Host ""
Write-Host "Next: if you haven't yet, run:  `"$Py`" `"$Dest`" setup"
Write-Host "Then quit Claude completely (right-click its tray icon, Quit) and reopen it."
Write-Host "If the tools don't appear, open Claude > Settings > Developer > Edit Config and check the 'qbo-readonly' entry is in that file."
