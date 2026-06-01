import logging

from app.grpc.server import serve


if __name__ == "__main__":
    logging.getLogger(__name__).info("run_grpc_entrypoint_started")
    serve()
