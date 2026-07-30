#!/usr/bin/env bash
# Deploy scout-campsite-bookings: build the image on the NAS and (re)start the container.
# WSL counterpart of deploy.ps1 (which stays for use from the laptop).
# Run from anywhere; version is derived from git tags in this repo.
set -euo pipefail

repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_dir"

git fetch --tags

# Get the latest tag
latest_tag=$(git describe --tags --abbrev=0)

# If the workspace is ahead of the latest tag, append "+dev"
commits_ahead=$(git rev-list --count HEAD "^$latest_tag")
if [ "$commits_ahead" -gt 0 ]; then
    version="${latest_tag}+dev"
else
    version="$latest_tag"
fi

echo "Deploying version: $version"

# Host, user and key come from ~/.ssh/config (Host jam).
# build and up are separate commands: sudo strips env vars, so the version has to
# ride in as --build-arg, which `up --build` won't accept.
ssh -o BatchMode=yes jam \
    "cd /volume1/docker/scout-campsite-bookings && sudo -n /usr/local/bin/docker compose build --build-arg APP_VERSION=$version && sudo -n /usr/local/bin/docker compose up -d"

echo "Deployed. Container status:"
ssh -o BatchMode=yes jam \
    "sudo -n /usr/local/bin/docker ps --filter name=scout-campsite-bookings-container --format '{{.Names}}: {{.Status}}'"
