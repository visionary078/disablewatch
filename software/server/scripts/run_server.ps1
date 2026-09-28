param([int]$Port = 8000)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
python server.py --host 0.0.0.0 --port $Port