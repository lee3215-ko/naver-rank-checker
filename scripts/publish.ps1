# Naver Rank Checker — GitHub 배포 스크립트
# 사용: .\scripts\publish.ps1 -Notes "버그 수정"
param(
    [string]$Notes = "업데이트",
    [ValidateSet("patch", "minor", "major", "none")]
    [string]$Bump = "",
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root
. (Join-Path $PSScriptRoot "gh-env.ps1")

function Read-DeployConfig {
    $cfg = Get-Content (Join-Path $Root "deploy.json") -Raw | ConvertFrom-Json
    return $cfg
}

function Read-AppVersion {
    $constants = Get-Content (Join-Path $Root "naver_rank_checker\constants.py") -Raw
    if ($constants -match 'APP_VERSION\s*=\s*"([^"]+)"') {
        return $Matches[1]
    }
    throw "constants.py 에서 APP_VERSION 을 찾을 수 없습니다."
}

function Set-AppVersion([string]$Version) {
    $path = Join-Path $Root "naver_rank_checker\constants.py"
    $text = Get-Content $path -Raw
    $text = $text -replace 'APP_VERSION\s*=\s*"[^"]+"', "APP_VERSION = `"$Version`""
    Set-Content -Path $path -Value $text -Encoding UTF8
}

function Bump-Version([string]$Version, [string]$Part) {
    $parts = $Version.Split(".")
    if ($parts.Count -lt 3) { throw "버전 형식이 올바르지 않습니다: $Version" }
    [int]$major = $parts[0]
    [int]$minor = $parts[1]
    [int]$patch = $parts[2]
    switch ($Part) {
        "major" { $major++; $minor = 0; $patch = 0 }
        "minor" { $minor++; $patch = 0 }
        "patch" { $patch++ }
        "none" { }
    }
    return "$major.$minor.$patch"
}

function Write-VersionJson([string]$Version, [string]$DownloadUrl, [string]$ReleaseNotes) {
    $payload = [ordered]@{
        version = $Version
        url     = $DownloadUrl
        notes   = $ReleaseNotes
    } | ConvertTo-Json -Depth 3
    Set-Content -Path (Join-Path $Root "version.json") -Value $payload -Encoding UTF8
}

function Ensure-GhCli {
    Ensure-GhInstalled
}

function Ensure-GitRemote($cfg) {
    if (-not (Test-Path (Join-Path $Root ".git"))) {
        git init | Out-Null
    }
    $branch = git branch --show-current 2>$null
    if ($branch -and $branch -ne "main") {
        git branch -M main | Out-Null
    } elseif (-not $branch) {
        git checkout -B main 2>$null | Out-Null
    }
    $remoteUrl = "https://github.com/$($cfg.github_owner)/$($cfg.github_repo).git"
    $hasOrigin = @(git remote 2>$null) -contains "origin"
    if (-not $hasOrigin) {
        git remote add origin $remoteUrl
        Write-Host "[git] remote origin 추가: $remoteUrl"
    }
}

function Ensure-GhAuth {
    Invoke-Gh auth status *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "GitHub 로그인이 필요합니다. .\scripts\setup-github.ps1 를 실행하거나 'gh auth login' 을 실행해 주세요."
    }
}

$cfg = Read-DeployConfig
$bumpPart = if ($Bump) { $Bump } else { $cfg.default_bump }
$current = Read-AppVersion
$newVersion = Bump-Version $current $bumpPart
$tag = "v$newVersion"
$downloadUrl = "https://github.com/$($cfg.github_owner)/$($cfg.github_repo)/releases/latest/download/$($cfg.release_asset)"

Write-Host "============================================"
Write-Host " Naver Rank Checker 배포"
Write-Host " 버전: $current -> $newVersion"
Write-Host "============================================"

Set-AppVersion $newVersion
Write-VersionJson $newVersion $downloadUrl $Notes

if (-not $SkipBuild) {
    Write-Host "[1/4] 빌드 중..."
    & (Join-Path $Root "build.bat")
    if ($LASTEXITCODE -ne 0) { throw "build.bat 실패" }
}

$distDir = Join-Path $Root "dist\NaverRankChecker"
if (-not (Test-Path $distDir)) {
    throw "빌드 결과가 없습니다: $distDir"
}

Write-Host "[2/4] zip 생성 중..."
$zipPath = Join-Path $Root "dist\$($cfg.release_asset)"
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
Compress-Archive -Path $distDir -DestinationPath $zipPath -Force

Ensure-GhCli
Ensure-GitRemote $cfg
Ensure-GhAuth

Write-Host "[3/4] GitHub 에 코드 push..."
git add `
    .gitignore README.md build.bat build.spec deploy.bat deploy.json version.json `
    run_gui.py requirements.txt requirements-dev.txt `
    naver_rank_checker scripts
git add -u

$status = git status --porcelain
if ($status) {
    git commit -m "Release $newVersion"
}

git push -u origin main
if ($LASTEXITCODE -ne 0) {
    Write-Host "[git] pull 후 다시 push 시도..."
    git pull origin main --rebase
    git push -u origin main
}

Write-Host "[4/4] GitHub Release 생성..."
Invoke-Gh release view $tag *> $null
if ($LASTEXITCODE -eq 0) {
    Invoke-Gh release upload $tag $zipPath --clobber
    Invoke-Gh release edit $tag --notes $Notes --title $newVersion
} else {
    Invoke-Gh release create $tag $zipPath --title $newVersion --notes $Notes --latest
}

Write-Host ""
Write-Host "배포 완료!"
Write-Host "  버전: $newVersion"
Write-Host "  Release: https://github.com/$($cfg.github_owner)/$($cfg.github_repo)/releases/tag/$tag"
Write-Host ""
Write-Host "다른 PC 사용자는 프로그램을 다시 켜면 업데이트 알림을 받습니다."
