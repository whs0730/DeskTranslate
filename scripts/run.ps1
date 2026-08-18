$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw "请先运行 scripts\build.ps1 创建环境。"
}

$env:PYTHONPATH = Join-Path $ProjectRoot "src"
& $VenvPython (Join-Path $ProjectRoot "run.py")

