from __future__ import annotations

import logging
import sys
from concurrent import futures
from pathlib import Path

import grpc

from app.core.config import BASE_DIR, GRPC_HOST, GRPC_PORT
from app.grpc.service import ScoringNormalGrpcService


GENERATED_DIR = BASE_DIR / "generated"
if str(GENERATED_DIR) not in sys.path:
    sys.path.insert(0, str(GENERATED_DIR))

from scoring.v1 import scoring_normal_pb2_grpc  # noqa: E402


logger = logging.getLogger(__name__)


def create_server() -> grpc.Server:
    logger.info("grpc_server_creating host=%s port=%s", GRPC_HOST, GRPC_PORT)
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    scoring_normal_pb2_grpc.add_ScoringNormalServiceServicer_to_server(
        ScoringNormalGrpcService(),
        server,
    )
    server.add_insecure_port(f"{GRPC_HOST}:{GRPC_PORT}")
    return server


def serve() -> None:
    server = create_server()
    server.start()
    logger.info("gRPC server listening on %s:%s", GRPC_HOST, GRPC_PORT)
    server.wait_for_termination()
