#!/usr/bin/env bash
# Deploy scout-campsite-bookings: pull the image GitHub Actions published and
# recreate the container on the NAS. WSL counterpart of deploy.ps1.
# Nothing is built here or on the NAS: push to main, wait for the workflow to go
# green, then run this. There is no version to compute any more - the image
# carries its own version, commit and build date.
set -euo pipefail

# Host, user and key come from ~/.ssh/config (Host jam).
# pull then up: a plain restart (or a bare `up -d`) reuses the image already on
# disk, so nothing would change.
ssh -o BatchMode=yes jam \
    "cd /volume1/docker/scout-campsite-bookings && sudo -n /usr/local/bin/docker compose pull && sudo -n /usr/local/bin/docker compose up -d"

echo "Deployed. Container status:"
ssh -o BatchMode=yes jam \
    "sudo -n /usr/local/bin/docker ps --filter name=scout-campsite-bookings-container --format '{{.Names}}: {{.Status}}'"

# docker exec rather than curl: the app image definitely has Python, the NAS
# shell's curl is not something to depend on.
echo "Running build:"
ssh -o BatchMode=yes jam \
    "sudo -n /usr/local/bin/docker exec scout-campsite-bookings-container python -c \"import urllib.request; print(urllib.request.urlopen('http://localhost:80/health').read().decode())\""
