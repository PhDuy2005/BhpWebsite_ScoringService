$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$sharedProtoRoot = Join-Path (Split-Path -Parent $repoRoot) "proto"
$sharedProtoFile = Join-Path $sharedProtoRoot "scoring\v1\scoring_normal.proto"
$outputDir = Join-Path $repoRoot "generated"

if (-not (Test-Path $sharedProtoFile)) {
    throw "Shared proto file not found: $sharedProtoFile"
}

New-Item -ItemType Directory -Force -Path $outputDir | Out-Null

python -m grpc_tools.protoc `
    -I $sharedProtoRoot `
    --python_out=$outputDir `
    --grpc_python_out=$outputDir `
    $sharedProtoFile

Write-Output "Generated gRPC stubs from $sharedProtoFile into $outputDir"
