$ErrorActionPreference = 'Stop'
$Bootstrap = Join-Path $PSScriptRoot 'scripts/setup.py'
if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 $Bootstrap --launch @args
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python $Bootstrap --launch @args
} else {
    Write-Error 'Install Python 3.10+ from python.org (include Python launcher). See README.md.'
    exit 1
}
exit $LASTEXITCODE
