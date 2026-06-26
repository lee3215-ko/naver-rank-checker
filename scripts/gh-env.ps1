function Refresh-ShellPath {
    $machine = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [System.Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user"
}

function Get-GhExe {
    Refresh-ShellPath
    $cmd = Get-Command gh -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }
    $candidates = @(
        "$env:ProgramFiles\GitHub CLI\gh.exe",
        "${env:ProgramFiles(x86)}\GitHub CLI\gh.exe",
        "$env:LOCALAPPDATA\Programs\GitHub CLI\gh.exe"
    )
    foreach ($path in $candidates) {
        if (Test-Path $path) {
            return $path
        }
    }
    return $null
}

function Invoke-Gh {
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$GhArgs
    )
    $gh = Get-GhExe
    if (-not $gh) {
        throw "GitHub CLI(gh)를 찾을 수 없습니다. winget install GitHub.cli 후 이 스크립트를 다시 실행해 주세요."
    }
    & $gh @GhArgs
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

function Ensure-GhInstalled {
    if (Get-GhExe) {
        return
    }
    Write-Host "GitHub CLI 설치 중..."
    winget install --id GitHub.cli -e --accept-source-agreements --accept-package-agreements | Out-Null
    Refresh-ShellPath
    if (-not (Get-GhExe)) {
        throw "GitHub CLI 설치 후에도 gh를 찾지 못했습니다. PowerShell을 닫았다가 다시 열고 스크립트를 실행해 주세요."
    }
}
