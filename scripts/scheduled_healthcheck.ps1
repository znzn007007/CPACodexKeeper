param(
    [switch]$Once,
    [switch]$DryRun,
    [ValidateSet('summary','broadcast','alert','recovery','deleted','disabled')]
    [string]$QuotaTest,
    [string]$QuotaTestStateFile = '.\runtime\quota_healthcheck_state.test.json'
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$argsList = @()
if ($Once) { $argsList += '--once' }
if ($DryRun) { $argsList += '--dry-run' }
if ($QuotaTest) {
    $argsList += '--quota-test'
    $argsList += $QuotaTest
    $argsList += '--quota-test-state-file'
    $argsList += $QuotaTestStateFile
}

python main.py @argsList
