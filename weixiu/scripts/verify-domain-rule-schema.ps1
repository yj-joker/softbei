$ErrorActionPreference = 'Stop'

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
$migrationPath = Join-Path $repoRoot 'src/main/resources/sql/20260706_domain_rule.sql'
$fullSchemaPath = Join-Path $repoRoot 'src/main/resources/sql/fix.sql'

if (-not (Test-Path -LiteralPath $migrationPath)) {
    throw "Missing domain rule migration: $migrationPath"
}
if (-not (Test-Path -LiteralPath $fullSchemaPath)) {
    throw "Missing full schema: $fullSchemaPath"
}

$requiredSnippets = @(
    'CREATE TABLE IF NOT EXISTS `domain_rule`',
    '`rule_code`',
    '`symptom_keys_json`',
    '`options_json`',
    '`evidence_refs_json`',
    '`sync_status`',
    'UNIQUE KEY `uk_domain_rule_code`'
)

foreach ($path in @($migrationPath, $fullSchemaPath)) {
    $sql = Get-Content -Raw -LiteralPath $path
    foreach ($snippet in $requiredSnippets) {
        if (-not $sql.Contains($snippet)) {
            throw "$path missing required SQL snippet: $snippet"
        }
    }
}

Write-Output 'domain_rule schema migration verification passed'
