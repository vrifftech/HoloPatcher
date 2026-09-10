param(
    [Parameter(Mandatory=$true)][string]$PyKotorRoot,
    [ValidateSet("OneFile")][string]$Mode = "OneFile",
    [string]$Python = "python"
)
$ErrorActionPreference = "Stop"
if ($env:OS -ne "Windows_NT") { throw "Build a Windows executable on Windows." }
$env:HOLOPATCHER_PYKOTOR_ROOT = (Resolve-Path -LiteralPath $PyKotorRoot).Path
$env:FRONTEND_BUILD_MODE = "onefile"
if (-not $env:FRESH_PYINSTALLER_MANIFEST -or -not (Test-Path -LiteralPath $env:FRESH_PYINSTALLER_MANIFEST -PathType Leaf)) {
    throw "Run the fresh-pyinstaller fork action/helper first and set FRESH_PYINSTALLER_MANIFEST. See BUILDING_GITHUB.md."
}
$root = $PSScriptRoot
Push-Location $root
try {
    & $Python -c "import importlib; [importlib.import_module(n) for n in ['tkinter', 'ply', 'defusedxml', 'charset_normalizer', 'PyInstaller']]"
    if ($LASTEXITCODE -ne 0) { throw "Install requirements-build.txt, then build/install the PyInstaller fork. See BUILDING_GITHUB.md." }
    & $Python "$root\run.py" --backend-info
    if ($LASTEXITCODE -ne 0) { throw "Backend validation failed." }
    # --onefile and --noupx are encoded by EXE(..., upx=False) in the spec.
    & $Python -m PyInstaller --noconfirm --clean --distpath "$root\dist" --workpath "$root\.pyinstaller-build" "$root\packaging\HoloPatcher.spec"
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }
    $exe = Join-Path $root "dist\HoloPatcher.exe"
    if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { throw "Expected executable not found: $exe" }
    Get-FileHash -LiteralPath $exe -Algorithm SHA256
} finally { Pop-Location }
