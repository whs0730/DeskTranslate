$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $VenvPython)) {
    python -m venv (Join-Path $ProjectRoot ".venv")
}

# 虚拟环境的启动器也可能继承 Low，测试前先修正，避免 CLR 和桌面权限失败。
& icacls.exe $VenvPython /setintegritylevel M
if ($LASTEXITCODE -ne 0) {
    throw "无法将项目 Python 设置为 Medium，请在普通桌面 PowerShell 中重新构建。"
}

$env:PYTHONPATH = Join-Path $ProjectRoot "src"

Push-Location $ProjectRoot
try {
    & npm.cmd ci --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw "前端依赖安装失败" }
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw "界面构建失败" }
    & npm.cmd test
    if ($LASTEXITCODE -ne 0) { throw "界面测试失败" }
    & $VenvPython -m pip install -r (Join-Path $ProjectRoot "requirements-dev.txt")
    if ($LASTEXITCODE -ne 0) {
        throw "依赖安装失败，退出代码：$LASTEXITCODE"
    }

    # CLR 会拒绝带 Low 标签的托管程序集；只修正当前窗口后端实际加载的三个文件。
    $RuntimeAssemblies = @(
        (Join-Path $ProjectRoot ".venv\Lib\site-packages\pythonnet\runtime\Python.Runtime.dll")
        (Join-Path $ProjectRoot ".venv\Lib\site-packages\webview\lib\Microsoft.Web.WebView2.Core.dll")
        (Join-Path $ProjectRoot ".venv\Lib\site-packages\webview\lib\Microsoft.Web.WebView2.WinForms.dll")
    )
    foreach ($RuntimeAssembly in $RuntimeAssemblies) {
        & icacls.exe $RuntimeAssembly /setintegritylevel M
        if ($LASTEXITCODE -ne 0) {
            throw "无法将 $RuntimeAssembly 设置为 Medium，请在普通桌面 PowerShell 中重新构建。"
        }
    }

    & $VenvPython -m unittest discover -s (Join-Path $ProjectRoot "tests") -v
    if ($LASTEXITCODE -ne 0) {
        throw "测试失败，退出代码：$LASTEXITCODE"
    }

    & $VenvPython -m PyInstaller --noconfirm --clean (Join-Path $ProjectRoot "DeskTranslate.spec")
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller 打包失败，退出代码：$LASTEXITCODE"
    }

    $ApplicationExecutable = Join-Path $ProjectRoot "dist\DeskTranslate.exe"
    # 构建目录可能继承 Low 标签；交付文件需恢复普通桌面程序的完整性级别。
    & icacls.exe $ApplicationExecutable /setintegritylevel M
    if ($LASTEXITCODE -ne 0) {
        throw "无法将交付 EXE 设置为 Medium，请在普通桌面 PowerShell 中重新构建。"
    }

    # 冻结包自检：独立 EXE 必须能自己找到随包的识别运行库和模型。
    $SelfTestReport = Join-Path $ProjectRoot "dist\DeskTranslate-selftest.json"
    Remove-Item -LiteralPath $SelfTestReport -Force -ErrorAction SilentlyContinue
    $SelfTest = Start-Process -FilePath $ApplicationExecutable -ArgumentList "--self-test-subtitles" -Wait -PassThru
    if ($SelfTest.ExitCode -ne 0) {
        $Details = if (Test-Path -LiteralPath $SelfTestReport) {
            Get-Content -LiteralPath $SelfTestReport -Raw
        } else {
            "未生成自检报告"
        }
        throw "冻结 EXE 字幕自检失败，退出代码：$($SelfTest.ExitCode)`n$Details"
    }
    if (-not (Test-Path -LiteralPath $SelfTestReport)) {
        throw "冻结 EXE 字幕自检未生成报告：$SelfTestReport"
    }
    Write-Host "冻结包字幕自检通过：$SelfTestReport"

    # 静态文件清单：模型、native 运行库、界面页面和 PYZ 模块缺一不可。
    & $VenvPython (Join-Path $ProjectRoot "scripts\check-package.py") $ApplicationExecutable
    if ($LASTEXITCODE -ne 0) {
        throw "交付 EXE 文件清单检查失败"
    }
}
finally {
    Pop-Location
}

Write-Host ""
Write-Host "构建完成：$(Join-Path $ProjectRoot 'dist\DeskTranslate.exe')"
Write-Host "交付 EXE 已设置为 Medium 完整性级别。"
