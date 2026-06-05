# Shared Proto Source

ScoringService uses the shared contract at:

`D:\DoAn\DoAn1\proto\scoring\v1\scoring_normal.proto`

This repository should not keep a separate local copy of `scoring_normal.proto`.

To generate Python gRPC stubs from the shared contract:

```powershell
cd D:\DoAn\DoAn1\ScoringService
.\venv\Scripts\Activate.ps1
.\scripts\generate_grpc_stubs.ps1
```
