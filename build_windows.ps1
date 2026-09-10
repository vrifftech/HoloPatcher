param(
    [Parameter(Mandatory=$true)][string]$PyKotorRoot,
    [ValidateSet("OneDir", "OneFile")][string]$Mode = "OneDir",
    [string]$Python = "python"
)
$ErrorActionPreference = "Stop"
if ($env:OS -ne "Windows_NT") { throw "Build a Windows executable on Windows." }
$env:HOLOPATCHER_PYKOTOR_ROOT = (Resolve-Path -LiteralPath $PyKotorRoot).Path
$env:FRONTEND_BUILD_MODE = $Mode.ToLowerInvariant()
$root = $PSScriptRoot
Push-Location $root
try {
    & $Python -c "import importlib; [importlib.import_module(n) for n in ['tkinter', 'ply', 'defusedxml', 'charset_normalizer', 'PyInstaller']]"
    if ($LASTEXITCODE -ne 0) { throw "Install requirements-build.txt in this Python environment first. See README.md for offline installation." }
    & $Python "$root\run.py" --backend-info
    if ($LASTEXITCODE -ne 0) { throw "Backend validation failed." }
    & $Python -m PyInstaller --noconfirm --clean --distpath "$root\dist" --workpath "$root\.pyinstaller-build" "$root\packaging\HoloPatcher.spec"
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }
    $exe = if ($Mode -eq "OneDir") { Join-Path $root "dist\HoloPatcher\HoloPatcher.exe" } else { Join-Path $root "dist\HoloPatcher.exe" }
    if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { throw "Expected executable not found: $exe" }
    Get-FileHash -LiteralPath $exe -Algorithm SHA256
} finally { Pop-Location }
