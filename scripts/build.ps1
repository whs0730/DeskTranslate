$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $VenvPython)) {
    python -m venv (Join-Path $ProjectRoot ".venv")
}

$env:PYTHONPATH = Join-Path $ProjectRoot "src"

Push-Location $ProjectRoot
try {
    & $VenvPython -m pip install -r (Join-Path $ProjectRoot "requirements-dev.txt")
    if ($LASTEXITCODE -ne 0) {
        throw "依赖安装失败，退出代码：$LASTEXITCODE"
    }

    & $VenvPython -m unittest discover -s (Join-Path $ProjectRoot "tests") -v
    if ($LASTEXITCODE -ne 0) {
        throw "测试失败，退出代码：$LASTEXITCODE"
    }

    & $VenvPython -m PyInstaller --noconfirm --clean (Join-Path $ProjectRoot "DeskTranslate.spec")
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller 打包失败，退出代码：$LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

Write-Host ""
Write-Host "构建完成：$(Join-Path $ProjectRoot 'dist\DeskTranslate.exe')"
