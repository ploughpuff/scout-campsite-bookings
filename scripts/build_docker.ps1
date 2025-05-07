$ImageName = "scout-campsite-bookings"
$DateTag = Get-Date -Format "yyyyMMdd-HHmm"

# Fetch the latest tags and commits from the remote repository
git fetch --tags

# Get the latest tag
$LatestTag = git describe --tags --abbrev=0

# Check if the workspace is ahead of the latest tag
$CommitsAhead = git rev-list --count HEAD ^$LatestTag

# If there are commits ahead, append "+dev" to the tag version
if ($CommitsAhead -gt 0) {
    $TagVersion = "$LatestTag + dev"
} else {
    $TagVersion = $LatestTag
}

# Build the Docker image with the appropriate version tag
docker build --build-arg APP_VERSION=$TagVersion --build-arg APP_ENV=production -t "${ImageName}:${DateTag}" .

# Tag the image with "latest"
docker tag "${ImageName}:${DateTag}" "${ImageName}:latest"

Write-Host "Built image: ${ImageName}:${DateTag}"
Write-Host "Tag: $TagVersion"

# Save the Docker image as a tar file
$outputFilename = "${ImageName}-${DateTag}.tar"
docker save -o $outputFilename "${ImageName}:${DateTag}"

robocopy . "\\jam\docker\scout-campsite-bookings" $outputFilename
robocopy "scripts" "\\jam\docker\scout-campsite-bookings" "docker-compose.yml"
