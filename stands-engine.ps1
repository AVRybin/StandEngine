[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet("create", "destroy")]
    [string]$Operation,

    [Parameter(Mandatory = $true, Position = 1)]
    [string]$Manifest,

    [ValidateSet("docker", "podman")]
    [string]$Runtime = $env:CONTAINER_RUNTIME,

    [string]$Image = $(if ($env:STANDS_ENGINE_IMAGE) { $env:STANDS_ENGINE_IMAGE } else { "stands-engine:local" }),

    [string[]]$EnvFile = @(),

    [string[]]$Resource = @()
)

$ErrorActionPreference = "Stop"

if (-not $Runtime) {
    if (Get-Command podman -ErrorAction SilentlyContinue) {
        $Runtime = "podman"
    }
    elseif (Get-Command docker -ErrorAction SilentlyContinue) {
        $Runtime = "docker"
    }
    else {
        throw "Neither podman nor docker was found in PATH."
    }
}

$manifestPath = (Resolve-Path -LiteralPath $Manifest).Path
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "Manifest does not exist: $Manifest"
}

$currentDirectory = (Get-Location).Path
$manifestDirectory = Split-Path -Parent $manifestPath
$relativeManifest = [System.IO.Path]::GetRelativePath($currentDirectory, $manifestPath)

if ($relativeManifest -notmatch '^\.\.[\\/]') {
    $workspace = $currentDirectory
    $containerManifest = "/workspace/" + ($relativeManifest -replace '\\', '/')
}
else {
    $workspace = $manifestDirectory
    $containerManifest = "/workspace/" + (Split-Path -Leaf $manifestPath)
}

$dataDirectory = Join-Path $currentDirectory ".stands-engine"
@("keys", "configsets", "output") | ForEach-Object {
    New-Item -ItemType Directory -Force -Path (Join-Path $dataDirectory $_) | Out-Null
}

$runArgs = @("run", "--rm")
foreach ($envFilePath in $EnvFile) {
    if (-not (Test-Path -LiteralPath $envFilePath -PathType Leaf)) {
        throw "Environment file does not exist: $envFilePath"
    }
    $resolvedEnvFile = (Resolve-Path -LiteralPath $envFilePath).Path
    $runArgs += @("--env-file", $resolvedEnvFile)
}

$resourceNames = @{}
$resolvedResources = @()
foreach ($resourceSpec in $Resource) {
    $parts = $resourceSpec.Split('=', 2)
    if ($parts.Count -ne 2 -or $parts[0] -notmatch '^[A-Za-z][A-Za-z0-9_-]*$' -or -not $parts[1]) {
        throw "Invalid resource '$resourceSpec'; expected NAME=PATH with a valid name."
    }

    $resourceName = $parts[0]
    if ($resourceNames.ContainsKey($resourceName)) {
        throw "Resource '$resourceName' was specified more than once."
    }

    $resourcePath = (Resolve-Path -LiteralPath $parts[1]).Path
    if (-not (Test-Path -LiteralPath $resourcePath -PathType Container)) {
        throw "Resource '$resourceName' is not a directory: $resourcePath"
    }
    $resourceNames[$resourceName] = $true
    $resolvedResources += [PSCustomObject]@{ Name = $resourceName; Path = $resourcePath }
}

$runArgs += @(
    "--volume", "${workspace}:/workspace:ro",
    "--volume", "${dataDirectory}:/data",
    "--env", "STAND__PATH_TO_KEY=/data/keys/id_ed25519",
    "--env", "STAND__PATH_TO_CONFIGSET=/data/configsets",
    "--env", "OUTPUT__FILE_PATH=/data/output"
)

$engineArgs = @()
foreach ($resource in $resolvedResources) {
    $containerResourcePath = "/resources/$($resource.Name)"
    $runArgs += @("--volume", "$($resource.Path):${containerResourcePath}:ro")
    $engineArgs += @("--resource", "$($resource.Name)=${containerResourcePath}")
}

$runArgs += @($Image) + $engineArgs + @($Operation, $containerManifest)

& $Runtime @runArgs
exit $LASTEXITCODE
