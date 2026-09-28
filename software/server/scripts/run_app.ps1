param([int]$Port = 7860)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
python app.py --host 0.0.0.0 --port $Port