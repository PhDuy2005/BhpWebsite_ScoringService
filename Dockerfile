# syntax=docker/dockerfile:1

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    GRPC_HOST=0.0.0.0 \
    GRPC_PORT=50051 \
    EXAMSERVICE_STORAGE_ROOT_PATH=/data/exam-service

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /data/exam-service logs outputs \
    && chown -R appuser:appuser /app /data/exam-service

COPY ScoringService/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

COPY ScoringService/app ./app
COPY ScoringService/generated ./generated
COPY ScoringService/run_grpc.py ./run_grpc.py

USER appuser
EXPOSE 50051
CMD ["python", "run_grpc.py"]

