$ImageName = "riffhams-bookings"
$DateTag = Get-Date -Format "yyyyMMdd-HHmm"

docker build -t "${ImageName}:${DateTag}" .
docker tag "${ImageName}:${DateTag}" "${ImageName}:latest"

Write-Host "Built image: ${ImageName}:${DateTag}"

docker save -o "${ImageName}-${DateTag}.tar" "${ImageName}:${DateTag}"
