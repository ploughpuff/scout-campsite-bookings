param(
    [ValidateSet("major", "minor", "patch")]
    [string]$level = "patch"
)

# Get the latest tag in semantic version format
$latestTag = git tag | Where-Object { $_ -match '^v\d+\.\d+\.\d+$' } | Sort-Object { [version]($_ -replace '^v', '') } | Select-Object -Last 1

if (-not $latestTag) {
    $newVersion = "0.0.1"
} else {
    $version = [version]($latestTag -replace '^v', '')
    switch ($level) {
        "major" { $newVersion = "{0}.0.0" -f ($version.Major + 1) }
        "minor" { $newVersion = "{0}.{1}.0" -f $version.Major, ($version.Minor + 1) }
        "patch" { $newVersion = "{0}.{1}.{2}" -f $version.Major, $version.Minor, ($version.Build + 1) }
    }
}

$newTag = "v$newVersion"
git tag $newTag
git push origin $newTag

Write-Host "Created and pushed tag: $newTag"
