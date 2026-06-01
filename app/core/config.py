from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = BASE_DIR / "app" / "services" / "coordinate_templates"
DEBUG_IMAGE_DIR = BASE_DIR / "outputs" / "debug_images"
RAW_IMAGE_DIR = BASE_DIR / "outputs" / "raw_images"
GRPC_HOST = "127.0.0.1"
GRPC_PORT = 50051
