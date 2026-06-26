# 최초 1회: GitHub 연결 설정
$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root
. (Join-Path $PSScriptRoot "gh-env.ps1")

$cfg = Get-Content (Join-Path $Root "deploy.json") -Raw | ConvertFrom-Json
$remoteUrl = "https://github.com/$($cfg.github_owner)/$($cfg.github_repo).git"

Write-Host "=== GitHub 최초 연결 ==="

Ensure-GhInstalled

if (-not (Test-Path (Join-Path $Root ".git"))) {
    git init
    git branch -M main
}

$hasOrigin = @(git remote 2>$null) -contains "origin"
if (-not $hasOrigin) {
    git remote add origin $remoteUrl
    Write-Host "origin 추가: $remoteUrl"
} else {
    $existing = git remote get-url origin
    Write-Host "origin 이미 있음: $existing"
}

Write-Host ""
Write-Host "GitHub 로그인 (브라우저가 열립니다)..."
Write-Host ""

Invoke-Gh auth login

Write-Host ""
Write-Host "로그인 완료 후 배포:"
Write-Host "  .\deploy.bat"
Write-Host "  또는 채팅에서 '배포해줘'"
