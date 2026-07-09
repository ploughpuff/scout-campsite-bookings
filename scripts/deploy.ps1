# Deploy scout-campsite-bookings: build the image on the NAS and (re)start the container.
# Replaces the old build_docker.ps1 tar/load/tag workflow.
# Run from anywhere; version is derived from git tags in this repo.

$RepoDir = Split-Path -Parent $PSScriptRoot
Set-Location $RepoDir

git fetch --tags

# Get the latest tag
$LatestTag = git describe --tags --abbrev=0

# If the workspace is ahead of the latest tag, append "+dev"
$CommitsAhead = git rev-list --count HEAD "^$LatestTag"
if ($CommitsAhead -gt 0) {
    $Version = "$LatestTag+dev"
} else {
    $Version = $LatestTag
}

Write-Host "Deploying version: $Version"

ssh -i C:\Users\Chris\.ssh\nas_claude -o BatchMode=yes claude@jam "cd /volume1/docker/scout-campsite-bookings && sudo -n /usr/local/bin/docker compose build --build-arg APP_VERSION=$Version && sudo -n /usr/local/bin/docker compose up -d"

if ($LASTEXITCODE -ne 0) {
    Write-Host "Deploy FAILED (exit $LASTEXITCODE)" -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host "Deployed. Container status:"
ssh -i C:\Users\Chris\.ssh\nas_claude -o BatchMode=yes claude@jam "sudo -n /usr/local/bin/docker ps --filter name=scout-campsite-bookings-container --format '{{.Names}}: {{.Status}}'"
