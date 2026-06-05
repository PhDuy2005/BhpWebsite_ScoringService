import logging
from time import perf_counter

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response

from app.schemas.scoring import ApiResponse
from app.services.answer_detection import (
    AnswerDetectionError,
    generate_pdf_template_points_preview_archive,
    generate_template_points_preview,
    get_pdf_page_count,
    process_answer_sheet,
    process_pdf_answer_sheets,
)


router = APIRouter(prefix="/scoring", tags=["scoring"])
logger = logging.getLogger(__name__)


@router.post("/read", response_model=ApiResponse)
async def read_answer_sheet(file: UploadFile = File(...)):
    started_at = perf_counter()
    content = await file.read()
    logger.info(
        "rest_read_request_received filename=%s content_type=%s bytes=%s",
        file.filename,
        file.content_type,
        len(content),
    )
    if not content:
        logger.warning("rest_read_request_empty filename=%s", file.filename)
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    try:
        if (file.filename or "").lower().endswith(".pdf"):
            logger.info("rest_read_request_mode filename=%s mode=pdf", file.filename)
            result = process_pdf_answer_sheets(content, filename=file.filename)
        else:
            logger.info("rest_read_request_mode filename=%s mode=image", file.filename)
            result = process_answer_sheet(content, filename=file.filename)
    except AnswerDetectionError as exc:
        logger.exception("rest_read_request_failed filename=%s", file.filename)
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    logger.info(
        "rest_read_request_completed filename=%s duration_ms=%.2f pages=%s",
        file.filename,
        (perf_counter() - started_at) * 1000,
        len(result) if isinstance(result, list) else 1,
    )
    return ApiResponse(
        statusCode=200,
        message="Read OMR data",
        data=result,
    )


@router.post("/test")
async def preview_answer_sheet_points(file: UploadFile = File(...)):
    started_at = perf_counter()
    content = await file.read()
    logger.info(
        "rest_test_request_received filename=%s content_type=%s bytes=%s",
        file.filename,
        file.content_type,
        len(content),
    )
    if not content:
        logger.warning("rest_test_request_empty filename=%s", file.filename)
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    try:
        if (file.filename or "").lower().endswith(".pdf"):
            page_count = get_pdf_page_count(content)
            logger.info(
                "rest_test_request_pdf_detected filename=%s page_count=%s",
                file.filename,
                page_count,
            )
            if page_count > 1:
                archive_bytes = generate_pdf_template_points_preview_archive(
                    content,
                    filename=file.filename,
                )
                logger.info(
                    "rest_test_request_completed filename=%s duration_ms=%.2f response=zip",
                    file.filename,
                    (perf_counter() - started_at) * 1000,
                )
                return Response(content=archive_bytes, media_type="application/zip")

        image_bytes = generate_template_points_preview(content, filename=file.filename)
    except AnswerDetectionError as exc:
        logger.exception("rest_test_request_failed filename=%s", file.filename)
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    logger.info(
        "rest_test_request_completed filename=%s duration_ms=%.2f response=png",
        file.filename,
        (perf_counter() - started_at) * 1000,
    )
    return Response(content=image_bytes, media_type="image/png")
