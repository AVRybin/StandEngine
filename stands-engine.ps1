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

    [string]$EnvFile
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
if ($EnvFile) {
    $resolvedEnvFile = (Resolve-Path -LiteralPath $EnvFile).Path
    $runArgs += @("--env-file", $resolvedEnvFile)
}

$runArgs += @(
    "--volume", "${workspace}:/workspace:ro",
    "--volume", "${dataDirectory}:/data",
    "--env", "STAND__PATH_TO_KEY=/data/keys/id_ed25519",
    "--env", "STAND__PATH_TO_CONFIGSET=/data/configsets",
    "--env", "OUTPUT__FILE_PATH=/data/output",
    $Image,
    $Operation,
    $containerManifest
)

& $Runtime @runArgs
exit $LASTEXITCODE
