# Deploy scout-campsite-bookings: pull the image GitHub Actions published and
# recreate the container on the NAS. Windows counterpart of deploy.sh.
# Nothing is built here or on the NAS: push to main, wait for the workflow to go
# green, then run this. There is no version to compute any more - the image
# carries its own version, commit and build date.

# pull then up: a plain restart (or a bare `up -d`) reuses the image already on
# disk, so nothing would change.
ssh -i C:\Users\Chris\.ssh\nas_claude -o BatchMode=yes claude@jam "cd /volume1/docker/scout-campsite-bookings && sudo -n /usr/local/bin/docker compose pull && sudo -n /usr/local/bin/docker compose up -d"

if ($LASTEXITCODE -ne 0) {
    Write-Host "Deploy FAILED (exit $LASTEXITCODE)" -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host "Deployed. Container status:"
ssh -i C:\Users\Chris\.ssh\nas_claude -o BatchMode=yes claude@jam "sudo -n /usr/local/bin/docker ps --filter name=scout-campsite-bookings-container --format '{{.Names}}: {{.Status}}'"

# docker exec rather than curl: the app image definitely has Python, the NAS
# shell's curl is not something to depend on.
Write-Host "Running build:"
ssh -i C:\Users\Chris\.ssh\nas_claude -o BatchMode=yes claude@jam "sudo -n /usr/local/bin/docker exec scout-campsite-bookings-container python -c ""import urllib.request; print(urllib.request.urlopen('http://localhost:80/health').read().decode())"""
