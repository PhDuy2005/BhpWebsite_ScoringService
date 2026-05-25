import json
from datetime import datetime, timezone
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4

import cv2
import fitz
import numpy as np

from app.core.config import TEMPLATE_DIR


OUTPUT_W = 800
MIN_OUTPUT_H = 1100
MEAN_GRAY_RADIUS = 6
TEMPLATE_FILES = {
    "candidate_id": "candidate_id.json",
    "exam_id": "exam_id.json",
    "part_1": "part_1.json",
    "part_2": "part_2.json",
    "part_3": "part_3.json",
}


class AnswerDetectionError(Exception):
    pass


def pdf_page_to_image(file_bytes: bytes, page_index: int = 0, zoom: int = 2) -> np.ndarray:
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    if page_index >= doc.page_count:
        raise AnswerDetectionError(f"PDF page index out of range: {page_index}")

    page = doc[page_index]
    matrix = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=matrix, alpha=False)
    image = np.frombuffer(pix.samples, dtype=np.uint8)
    image = image.reshape(pix.height, pix.width, pix.n)
    return cv2.cvtColor(image, cv2.COLOR_RGB2BGR)


def image_bytes_to_image(file_bytes: bytes) -> np.ndarray:
    data = np.frombuffer(file_bytes, dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise AnswerDetectionError("Unsupported or invalid image file.")
    return image


def file_bytes_to_image(file_bytes: bytes, filename: str | None = None) -> np.ndarray:
    suffix = Path(filename or "").suffix.lower()
    if suffix == ".pdf":
        return pdf_page_to_image(file_bytes)

    try:
        return image_bytes_to_image(file_bytes)
    except AnswerDetectionError:
        return pdf_page_to_image(file_bytes)


def order_points(points: np.ndarray) -> np.ndarray:
    points = np.array(points, dtype="float32")
    point_sum = points.sum(axis=1)
    point_diff = points[:, 0] - points[:, 1]

    top_left = points[np.argmin(point_sum)]
    bottom_right = points[np.argmax(point_sum)]
    top_right = points[np.argmax(point_diff)]
    bottom_left = points[np.argmin(point_diff)]

    return np.array([top_left, top_right, bottom_right, bottom_left], dtype="float32")


def find_marker_candidates(image: np.ndarray) -> list[dict[str, Any]]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, thresh = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    image_h, image_w = gray.shape
    min_side = min(image_h, image_w)
    min_marker_size = min_side * 0.008
    max_marker_size = min_side * 0.04
    candidates = []

    for contour in contours:
        area = cv2.contourArea(contour)
        x, y, w, h = cv2.boundingRect(contour)
        if w == 0 or h == 0:
            continue

        aspect_ratio = w / h
        extent = area / (w * h)
        is_square = 0.75 <= aspect_ratio <= 1.25
        is_solid = extent >= 0.50
        is_right_size = min_marker_size <= w <= max_marker_size and min_marker_size <= h <= max_marker_size

        if is_square and is_solid and is_right_size:
            candidates.append(
                {
                    "center": (x + w / 2, y + h / 2),
                    "bbox": (x, y, w, h),
                    "area": area,
                }
            )

    return candidates


def select_outer_4_markers(candidates: list[dict[str, Any]]) -> np.ndarray:
    if len(candidates) < 4:
        raise AnswerDetectionError(f"Could not find enough markers. Found {len(candidates)}.")

    points = np.array([candidate["center"] for candidate in candidates], dtype="float32")
    return order_points(points)


def warp_answer_sheet(image: np.ndarray, points: np.ndarray) -> np.ndarray:
    src = order_points(points)
    top_left, top_right, bottom_right, bottom_left = src

    width_top = np.linalg.norm(top_right - top_left)
    width_bottom = np.linalg.norm(bottom_right - bottom_left)
    source_width = max(width_top, width_bottom)

    height_right = np.linalg.norm(bottom_right - top_right)
    height_left = np.linalg.norm(bottom_left - top_left)
    source_height = max(height_right, height_left)

    output_h = max(MIN_OUTPUT_H, int(round(OUTPUT_W * source_height / source_width)))
    dst = np.array(
        [[0, 0], [OUTPUT_W - 1, 0], [OUTPUT_W - 1, output_h - 1], [0, output_h - 1]],
        dtype="float32",
    )

    matrix = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(image, matrix, (OUTPUT_W, output_h))


def prepare_answer_sheet(file_bytes: bytes, filename: str | None = None) -> tuple[np.ndarray, dict[str, Any]]:
    image = file_bytes_to_image(file_bytes, filename)
    candidates = find_marker_candidates(image)
    marker_points = select_outer_4_markers(candidates)
    warped = warp_answer_sheet(image, marker_points)

    debug = {
        "source_width": int(image.shape[1]),
        "source_height": int(image.shape[0]),
        "warped_width": int(warped.shape[1]),
        "warped_height": int(warped.shape[0]),
        "marker_count": len(candidates),
        "markers": marker_points.astype(float).tolist(),
    }
    return warped, debug


def get_mean_gray(image: np.ndarray, x: int, y: int, radius: int = MEAN_GRAY_RADIUS) -> float:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    x1 = max(0, x - radius)
    x2 = min(w, x + radius)
    y1 = max(0, y - radius)
    y2 = min(h, y + radius)
    roi = gray[y1:y2, x1:x2]
    if roi.size == 0:
        raise AnswerDetectionError(f"Point is outside image bounds: x={x}, y={y}")
    return float(np.mean(roi))


def load_template(name: str) -> dict[str, Any]:
    try:
        filename = TEMPLATE_FILES[name]
    except KeyError as exc:
        raise AnswerDetectionError(f"Unknown template: {name}") from exc

    path = TEMPLATE_DIR / filename
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_templates() -> dict[str, dict[str, Any]]:
    return {name: load_template(name) for name in TEMPLATE_FILES}


def iter_point_groups(template: dict[str, Any]) -> list[dict[str, Any]]:
    name = template["name"]
    groups = []

    if name in {"CandidateId", "ExamId"}:
        for character in template["characters"]:
            groups.append(character)
        return groups

    if name == "MCQ":
        for group in template["groups"]:
            groups.append(group)
        return groups

    if name == "TFQ":
        for question in template["groups"]:
            for statement in question["statements"]:
                groups.append(statement)
        return groups

    if name == "SAQ":
        for question in template["groups"]:
            for character in question["characters"]:
                groups.append(character)
        return groups

    raise AnswerDetectionError(f"Unsupported template root: {name}")


def enrich_template_with_mean_gray(template: dict[str, Any], image: np.ndarray) -> dict[str, Any]:
    enriched = deepcopy(template)
    for group in iter_point_groups(enriched):
        for point in group["points"]:
            point["mean_gray"] = get_mean_gray(image, int(point["x"]), int(point["y"]))
    return enriched


def all_points(template: dict[str, Any]) -> list[dict[str, Any]]:
    points = []
    for group in iter_point_groups(template):
        points.extend(group["points"])
    return points


def kmeans_threshold(points: list[dict[str, Any]]) -> dict[str, float]:
    values = [float(point["mean_gray"]) for point in points]
    dark_center = min(values)
    light_center = max(values)

    for _ in range(100):
        dark_values = []
        light_values = []

        for value in values:
            if abs(value - dark_center) <= abs(value - light_center):
                dark_values.append(value)
            else:
                light_values.append(value)

        if not dark_values or not light_values:
            break

        next_dark = sum(dark_values) / len(dark_values)
        next_light = sum(light_values) / len(light_values)

        if abs(next_dark - dark_center) + abs(next_light - light_center) < 0.000001:
            dark_center = next_dark
            light_center = next_light
            break

        dark_center = next_dark
        light_center = next_light

    if dark_center > light_center:
        dark_center, light_center = light_center, dark_center

    separation = light_center - dark_center
    threshold = (dark_center + light_center) / 2
    min_gap = max(4.0, separation * 0.25)

    return {
        "dark_center": dark_center,
        "light_center": light_center,
        "threshold": threshold,
        "weak_threshold": light_center - separation * 0.20,
        "min_gap": min_gap,
        "strong_gap": min_gap * 1.25,
    }


def classify_group(points: list[dict[str, Any]], threshold_info: dict[str, float]) -> dict[str, Any]:
    sorted_points = sorted(points, key=lambda point: point["mean_gray"])
    best = sorted_points[0]
    second = sorted_points[1] if len(sorted_points) > 1 else None
    gap = None if second is None else second["mean_gray"] - best["mean_gray"]
    dark_points = [point for point in points if point["mean_gray"] <= threshold_info["threshold"]]
    is_clear_dark = best["mean_gray"] <= threshold_info["threshold"]
    is_clear_relative_mark = (
        second is not None
        and best["mean_gray"] <= threshold_info["weak_threshold"]
        and gap >= threshold_info["strong_gap"]
    )

    if not dark_points:
        if is_clear_relative_mark:
            status = "selected_weak"
            selected = best
        else:
            status = "blank"
            selected = None
    elif len(dark_points) > 1:
        status = "ambiguous"
        selected = None
    elif second is not None and gap < threshold_info["min_gap"] and not is_clear_relative_mark:
        status = "ambiguous"
        selected = None
    elif is_clear_dark or is_clear_relative_mark:
        status = "selected"
        selected = best
    else:
        status = "blank"
        selected = None

    return {
        "status": status,
        "answer": selected["value"] if selected else None,
        "selected": selected,
        "best": best,
        "second": second,
        "gap": gap,
        "marked_values": get_marked_values(points, threshold_info, selected),
    }


def get_marked_values(
    points: list[dict[str, Any]],
    threshold_info: dict[str, float],
    selected: dict[str, Any] | None,
) -> list[str]:
    dark_values = [
        point["value"]
        for point in sorted(points, key=lambda item: str(item["value"]))
        if point["mean_gray"] <= threshold_info["threshold"]
    ]
    if dark_values:
        return dark_values
    if selected is not None:
        return [selected["value"]]
    return []


def build_mcq_raw_answer(question: dict[str, Any]) -> str | None:
    values = question.get("marked_values") or []
    return "".join(values) if values else None


def build_tfq_raw_answer(question: dict[str, Any]) -> str:
    chars = []
    for statement in question["statements"]:
        values = set(statement.get("marked_values") or [])
        if values == {"T"}:
            chars.append("D")
        elif values == {"F"}:
            chars.append("S")
        else:
            chars.append("B")
    return "".join(chars)


def build_saq_raw_answer(question: dict[str, Any]) -> str:
    columns = []
    for character in question["characters"]:
        values = character.get("marked_values") or []
        columns.append("".join(values))
    return "|".join(columns)


def build_omr_sections(
    part_1_result: dict[str, Any],
    part_2_result: dict[str, Any],
    part_3_result: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    return {
        "mcq": [
            {
                "sectionQuestionNumber": index,
                "rawAnswer": build_mcq_raw_answer(question),
            }
            for index, question in enumerate(part_1_result["questions"], start=1)
        ],
        "tfq": [
            {
                "sectionQuestionNumber": index,
                "rawAnswer": build_tfq_raw_answer(question),
            }
            for index, question in enumerate(part_2_result["questions"], start=1)
        ],
        "saq": [
            {
                "sectionQuestionNumber": index,
                "rawAnswer": build_saq_raw_answer(question),
            }
            for index, question in enumerate(part_3_result["questions"], start=1)
        ],
    }


def build_external_submission_id(filename: str | None = None) -> str:
    stem = Path(filename or "answer-sheet").stem or "answer-sheet"
    return f"scoring-service-omr-{stem}-{uuid4()}"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def detect_id_template(template: dict[str, Any], threshold_info: dict[str, float]) -> dict[str, Any]:
    characters = []
    answer_parts = []

    for character in template["characters"]:
        result = classify_group(character["points"], threshold_info)
        if result["answer"] is not None:
            answer_parts.append(result["answer"])
        characters.append({"name": character["name"], **result})

    return {
        "name": template["name"],
        "answer": "".join(answer_parts) if answer_parts else None,
        "characters": characters,
    }


def detect_mcq(template: dict[str, Any], threshold_info: dict[str, float]) -> dict[str, Any]:
    questions = []
    answers = {}

    for group in template["groups"]:
        result = classify_group(group["points"], threshold_info)
        questions.append({"name": group["name"], **result})
        answers[group["name"]] = result["answer"]

    return {"name": template["name"], "answers": answers, "questions": questions}


def detect_tfq(template: dict[str, Any], threshold_info: dict[str, float]) -> dict[str, Any]:
    questions = []
    answers = {}

    for question in template["groups"]:
        statements = []
        for statement in question["statements"]:
            result = classify_group(statement["points"], threshold_info)
            statements.append({"name": statement["name"], **result})
            answers[statement["name"]] = result["answer"]
        questions.append({"name": question["name"], "statements": statements})

    return {"name": template["name"], "answers": answers, "questions": questions}


def detect_saq(template: dict[str, Any], threshold_info: dict[str, float]) -> dict[str, Any]:
    questions = []
    answers = {}

    for question in template["groups"]:
        characters = []
        answer_parts = []
        for character in question["characters"]:
            result = classify_group(character["points"], threshold_info)
            if result["answer"] is not None:
                answer_parts.append(result["answer"])
            characters.append({"name": character["name"], **result})

        answer = "".join(answer_parts) if answer_parts else None
        answers[question["name"]] = answer
        questions.append({"name": question["name"], "answer": answer, "characters": characters})

    return {"name": template["name"], "answers": answers, "questions": questions}


def detect_template(template: dict[str, Any]) -> dict[str, Any]:
    threshold_info = kmeans_threshold(all_points(template))
    name = template["name"]

    if name in {"CandidateId", "ExamId"}:
        result = detect_id_template(template, threshold_info)
    elif name == "MCQ":
        result = detect_mcq(template, threshold_info)
    elif name == "TFQ":
        result = detect_tfq(template, threshold_info)
    elif name == "SAQ":
        result = detect_saq(template, threshold_info)
    else:
        raise AnswerDetectionError(f"Unsupported template root: {name}")

    return {"threshold": threshold_info, **result}


def process_answer_sheet(file_bytes: bytes, filename: str | None = None) -> dict[str, Any]:
    warped, debug = prepare_answer_sheet(file_bytes, filename)
    templates = load_templates()
    enriched = {
        name: enrich_template_with_mean_gray(template, warped)
        for name, template in templates.items()
    }

    candidate_result = detect_template(enriched["candidate_id"])
    exam_result = detect_template(enriched["exam_id"])
    part_1_result = detect_template(enriched["part_1"])
    part_2_result = detect_template(enriched["part_2"])
    part_3_result = detect_template(enriched["part_3"])

    return {
        "examUuid": None,
        "paperCode": exam_result["answer"],
        "studentUuid": candidate_result["answer"],
        "externalSubmissionId": build_external_submission_id(filename),
        "scannedAt": utc_now_iso(),
        "sections": build_omr_sections(part_1_result, part_2_result, part_3_result),
    }



