#!/usr/bin/env bash
# Bump the semantic version tag and push it. Usage: bash scripts/bump-tag.sh [major|minor|patch]
# Pushing the tag triggers the release build in .github/workflows/docker-publish.yml,
# which publishes ghcr.io/ploughpuff/scout-campsite-bookings:X.Y.Z and :X.Y.
set -euo pipefail

# The tag build publishes :X.Y.Z, so the commit must already be on origin/main -
# otherwise the release names a commit main doesn't have, while :latest (which
# only tracks main) stays behind.
git fetch --quiet origin main
if ! git merge-base --is-ancestor HEAD origin/main; then
    echo "HEAD is not on origin/main - push your commits first" >&2; exit 1
fi

level="${1:-patch}"
case "$level" in
    major|minor|patch) ;;
    *) echo "usage: bash scripts/bump-tag.sh [major|minor|patch]" >&2; exit 1 ;;
esac

# Latest tag in vX.Y.Z form, ordered by version rather than lexically
latest_tag=$(git tag --list 'v[0-9]*.[0-9]*.[0-9]*' | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sort -V | tail -n 1)

if [ -z "$latest_tag" ]; then
    new_version="0.0.1"
else
    IFS=. read -r major minor patch <<< "${latest_tag#v}"
    case "$level" in
        major) new_version="$((major + 1)).0.0" ;;
        minor) new_version="${major}.$((minor + 1)).0" ;;
        patch) new_version="${major}.${minor}.$((patch + 1))" ;;
    esac
fi

new_tag="v$new_version"
git tag "$new_tag"
git push origin "$new_tag"

echo "Created and pushed tag: $new_tag"
