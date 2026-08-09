[CmdletBinding()]
param(
    [string]$EnvFile,
    [switch]$VerifyMiddleware
)

$ErrorActionPreference = "Stop"

$projectDirectory = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ([string]::IsNullOrWhiteSpace($EnvFile)) {
    $EnvFile = Join-Path $projectDirectory "..\.env"
}

if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
    throw "Environment file not found: $EnvFile"
}
$EnvFile = (Resolve-Path -LiteralPath $EnvFile).Path

$loadedKeys = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
$lineNumber = 0

foreach ($rawLine in Get-Content -LiteralPath $EnvFile -Encoding UTF8) {
    $lineNumber++
    $line = $rawLine.Trim()
    if ([string]::IsNullOrWhiteSpace($line) -or $line.StartsWith("#")) {
        continue
    }
    if ($line.StartsWith("export ", [System.StringComparison]::OrdinalIgnoreCase)) {
        $line = $line.Substring(7).TrimStart()
    }

    $separatorIndex = $line.IndexOf("=")
    if ($separatorIndex -lt 1) {
        throw "Invalid .env line ${lineNumber}: expected KEY=VALUE"
    }

    $key = $line.Substring(0, $separatorIndex).Trim()
    if ($key -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') {
        throw "Invalid .env line ${lineNumber}: invalid key '$key'"
    }

    $value = $line.Substring($separatorIndex + 1).Trim()
    if ($value.Length -ge 1 -and ($value[0] -eq '"' -or $value[0] -eq "'")) {
        $quote = $value[0]
        if ($value.Length -lt 2 -or $value[$value.Length - 1] -ne $quote) {
            throw "Invalid .env line ${lineNumber}: unterminated quoted value"
        }
        $value = $value.Substring(1, $value.Length - 2)
    } else {
        $value = [regex]::Replace($value, '\s+#.*$', '').Trim()
    }

    [Environment]::SetEnvironmentVariable($key, $value, "Process")
    [void]$loadedKeys.Add($key)
}

$requiredVariables = @(
    "DASHSCOPE_API_KEY",
    "MYSQL_HOST", "MYSQL_PORT", "MYSQL_DATABASE", "MYSQL_USER", "MYSQL_PASSWORD",
    "REDIS_HOST", "REDIS_PORT",
    "NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD",
    "MINIO_ENDPOINT", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY", "MINIO_DOCUMENT_BUCKET",
    "API_TOKEN", "INTERNAL_TOKEN"
)
$missingVariables = @($requiredVariables | Where-Object {
    [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($_, "Process"))
})
if ($missingVariables.Count -gt 0) {
    throw "Required Java variables are missing from .env: $($missingVariables -join ', ')"
}

$apiToken = [Environment]::GetEnvironmentVariable("API_TOKEN", "Process")
$internalToken = [Environment]::GetEnvironmentVariable("INTERNAL_TOKEN", "Process")
if ($apiToken -eq $internalToken) {
    throw "API_TOKEN and INTERNAL_TOKEN must be different"
}

$minioEndpoint = [Environment]::GetEnvironmentVariable("MINIO_ENDPOINT", "Process")
if ($minioEndpoint -notmatch '^[A-Za-z][A-Za-z0-9+.-]*://') {
    $minioSecure = [Environment]::GetEnvironmentVariable("MINIO_SECURE", "Process")
    $scheme = if ($minioSecure -match '^(?i:true|1|yes)$') { "https" } else { "http" }
    $minioEndpoint = "${scheme}://$minioEndpoint"
}
[Environment]::SetEnvironmentVariable("MINIO_ENDPOINT", $minioEndpoint, "Process")

# Do not inherit a stale dev profile unless .env explicitly configures one.
if (-not $loadedKeys.Contains("SPRING_PROFILES_ACTIVE")) {
    [Environment]::SetEnvironmentVariable("SPRING_PROFILES_ACTIVE", $null, "Process")
}
[Environment]::SetEnvironmentVariable(
    "MIDDLEWARE_VERIFICATION_ENABLED",
    $(if ($VerifyMiddleware) { "true" } else { "false" }),
    "Process"
)

Write-Host "Loaded $($loadedKeys.Count) environment variables from $EnvFile (values hidden)"
Write-Host "Spring profile: default (application-dev.yml is not loaded)"
if ($VerifyMiddleware) {
    Write-Host "Java middleware connectivity verification is enabled"
}

Push-Location $projectDirectory
try {
    & mvn "-Dmaven.test.skip=true" "spring-boot:run"
    if ($LASTEXITCODE -ne 0) {
        throw "Spring Boot failed to start; Maven exit code: $LASTEXITCODE"
    }
} finally {
    Pop-Location
}
