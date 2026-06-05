from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Iterable

import grpc

from app.core.config import BASE_DIR
from app.services.answer_detection import (
    AnswerDetectionError,
    iter_answer_sheet_from_pdf_url,
)


GENERATED_DIR = BASE_DIR / "generated"
if str(GENERATED_DIR) not in sys.path:
    sys.path.insert(0, str(GENERATED_DIR))

from scoring.v1 import scoring_normal_pb2, scoring_normal_pb2_grpc  # noqa: E402


logger = logging.getLogger(__name__)


def _build_section_answers(items: list[dict]) -> list[scoring_normal_pb2.SectionAnswer]:
    answers = []
    for item in items:
        raw_answer = item.get("rawAnswer")
        answers.append(
            scoring_normal_pb2.SectionAnswer(
                section_question_number=item["sectionQuestionNumber"],
                raw_answer="" if raw_answer is None else raw_answer,
            )
        )
    return answers


def _build_success_response(
    request_id: str,
    result: dict,
    page_number: int,
    total_pages: int,
) -> scoring_normal_pb2.ReadOmrResponse:
    sections = result["sections"]
    payload = scoring_normal_pb2.ReadOmrPayload(
        exam_uuid=result["examUuid"] or "",
        paper_code=result["paperCode"] or "",
        student_uuid=result["studentUuid"] or "",
        external_submission_id=result["externalSubmissionId"],
        scanned_at=result["scannedAt"],
        sections=scoring_normal_pb2.OmrSections(
            mcq=_build_section_answers(sections["mcq"]),
            tfq=_build_section_answers(sections["tfq"]),
            saq=_build_section_answers(sections["saq"]),
        ),
        raw_image_url=result.get("rawImageUrl", ""),
        scored_image_url=result.get("scoredImageUrl", ""),
    )
    return scoring_normal_pb2.ReadOmrResponse(
        request_id=request_id,
        page_number=page_number,
        total_pages=total_pages,
        success=True,
        error_message="",
        data=payload,
    )


def _build_error_response(
    request_id: str,
    error_message: str,
    page_number: int = 0,
    total_pages: int = 0,
) -> scoring_normal_pb2.ReadOmrResponse:
    return scoring_normal_pb2.ReadOmrResponse(
        request_id=request_id,
        page_number=page_number,
        total_pages=total_pages,
        success=False,
        error_message=error_message,
    )


class ScoringNormalGrpcService(scoring_normal_pb2_grpc.ScoringNormalServiceServicer):
    def ReadOmr(
        self,
        request: scoring_normal_pb2.ReadOmrRequest,
        context: grpc.ServicerContext,
    ) -> Iterable[scoring_normal_pb2.ReadOmrResponse]:
        logger.info(
            "grpc_read_omr request_id=%s exam_uuid=%s pdf_url=%s",
            request.request_id,
            request.exam_uuid,
            request.pdf_url,
        )

        streamed_pages = 0
        try:
            for result in iter_answer_sheet_from_pdf_url(
                pdf_url=request.pdf_url,
                exam_uuid=request.exam_uuid,
                request_scanned_at=request.scanned_at or None,
            ):
                streamed_pages += 1
                page_number = result.get("pageNumber", streamed_pages)
                total_pages = result.get("totalPages", 0)

                if result.get("success") is False:
                    logger.warning(
                        "grpc_read_omr_streaming_page_error request_id=%s page_number=%s total_pages=%s error=%s",
                        request.request_id,
                        page_number,
                        total_pages,
                        result.get("errorMessage"),
                    )
                    yield _build_error_response(
                        request_id=request.request_id,
                        error_message=result.get("errorMessage") or "Page processing failed.",
                        page_number=page_number,
                        total_pages=total_pages,
                    )
                    continue

                logger.info(
                    "grpc_read_omr_streaming_page request_id=%s page_number=%s total_pages=%s paper_code=%s student_code=%s",
                    request.request_id,
                    page_number,
                    total_pages,
                    result.get("paperCode"),
                    result.get("studentUuid"),
                )
                yield _build_success_response(
                    request_id=request.request_id,
                    result=result,
                    page_number=page_number,
                    total_pages=total_pages,
                )
        except AnswerDetectionError as exc:
            logger.exception("grpc_read_omr_failed request_id=%s", request.request_id)
            yield _build_error_response(request.request_id, str(exc))
            return
        except Exception as exc:  # pragma: no cover
            logger.exception("grpc_read_omr_unexpected request_id=%s", request.request_id)
            yield _build_error_response(request.request_id, f"Unexpected error: {exc}")
            return

        logger.info(
            "grpc_read_omr_completed request_id=%s streamed_pages=%s",
            request.request_id,
            streamed_pages,
        )
