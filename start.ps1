# 右键此文件，选择“使用 PowerShell 运行”。
param(
    [string]$Directory = $PSScriptRoot
)

$ErrorActionPreference = 'Stop'
$launchExitCode = 0
Push-Location -LiteralPath $PSScriptRoot

try {
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    $pythonArguments = @()
    if ($null -eq $pythonCommand) {
        $pythonCommand = Get-Command py.exe -ErrorAction SilentlyContinue
        $pythonArguments = @('-3')
    }
    if ($null -eq $pythonCommand) {
        throw '未找到 Python，请先安装 Python 并安装 requirements.txt 中的依赖。'
    }

    & $pythonCommand.Source @pythonArguments -B -X utf8 (Join-Path $PSScriptRoot 'homework_gui.py') $Directory
    if ($LASTEXITCODE -ne 0) {
        $launchExitCode = $LASTEXITCODE
        throw "程序退出，错误码：$launchExitCode。请查看上方错误信息。"
    }
}
catch {
    if ($launchExitCode -eq 0) { $launchExitCode = 1 }
    Write-Host "启动失败：$($_.Exception.Message)" -ForegroundColor Red
    [void](Read-Host '按回车关闭窗口')
}
finally {
    Pop-Location
}

exit $launchExitCode
