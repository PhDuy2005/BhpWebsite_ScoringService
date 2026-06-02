import json
import logging
import re
from io import BytesIO
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse
from typing import Any
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import cv2
import fitz
import numpy as np

from app.core.config import DEBUG_IMAGE_DIR, RAW_IMAGE_DIR, STORAGE_ROOT_PATH, TEMPLATE_DIR


logger = logging.getLogger(__name__)
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
DEBUG_MARK_COLORS = {
    "outer": (0, 255, 255),
    "inner": (0, 0, 255),
}
TEMPLATE_POINT_COLORS = {
    "part_1": (0, 0, 255),
    "part_2": (0, 255, 255),
    "part_3": (0, 180, 0),
    "candidate_id": (255, 0, 255),
    "exam_id": (255, 0, 0),
}


class AnswerDetectionError(Exception):
    pass


def get_pdf_page_count(file_bytes: bytes) -> int:
    with fitz.open(stream=file_bytes, filetype="pdf") as doc:
        page_count = doc.page_count
    logger.info("pdf_page_count_detected page_count=%s", page_count)
    return page_count


def pdf_page_to_image(file_bytes: bytes, page_index: int = 0, zoom: int = 2) -> np.ndarray:
    logger.info("pdf_page_to_image_started page_index=%s zoom=%s", page_index + 1, zoom)
    with fitz.open(stream=file_bytes, filetype="pdf") as doc:
        if page_index < 0 or page_index >= doc.page_count:
            raise AnswerDetectionError(f"PDF page index out of range: {page_index}")

        page = doc[page_index]
        matrix = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=matrix, alpha=False)

    image = np.frombuffer(pix.samples, dtype=np.uint8)
    image = image.reshape(pix.height, pix.width, pix.n)
    converted = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
    logger.info(
        "pdf_page_to_image_completed page_index=%s width=%s height=%s",
        page_index + 1,
        converted.shape[1],
        converted.shape[0],
    )
    return converted


def image_bytes_to_image(file_bytes: bytes) -> np.ndarray:
    data = np.frombuffer(file_bytes, dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise AnswerDetectionError("Unsupported or invalid image file.")
    logger.info(
        "image_bytes_decoded width=%s height=%s channels=%s",
        image.shape[1],
        image.shape[0],
        image.shape[2] if len(image.shape) > 2 else 1,
    )
    return image


def file_bytes_to_image(
    file_bytes: bytes,
    filename: str | None = None,
    page_index: int = 0,
) -> np.ndarray:
    suffix = Path(filename or "").suffix.lower()
    logger.info(
        "file_bytes_to_image_started filename=%s suffix=%s page_index=%s",
        filename,
        suffix,
        page_index + 1,
    )
    if suffix == ".pdf":
        return pdf_page_to_image(file_bytes, page_index=page_index)

    try:
        return image_bytes_to_image(file_bytes)
    except AnswerDetectionError:
        return pdf_page_to_image(file_bytes, page_index=page_index)


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

    logger.info(
        "marker_candidates_detected candidates=%s min_marker_size=%.2f max_marker_size=%.2f",
        len(candidates),
        min_marker_size,
        max_marker_size,
    )
    return candidates


def select_outer_4_markers(candidates: list[dict[str, Any]]) -> np.ndarray:
    if len(candidates) < 4:
        raise AnswerDetectionError(f"Could not find enough markers. Found {len(candidates)}.")

    points = np.array([candidate["center"] for candidate in candidates], dtype="float32")
    selected = order_points(points)
    logger.info("marker_points_selected points=%s", selected.astype(float).tolist())
    return selected


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
    warped = cv2.warpPerspective(image, matrix, (OUTPUT_W, output_h))
    logger.info(
        "warp_answer_sheet_completed output_width=%s output_height=%s",
        OUTPUT_W,
        output_h,
    )
    return warped


def prepare_answer_sheet(
    file_bytes: bytes,
    filename: str | None = None,
    page_index: int = 0,
) -> tuple[np.ndarray, dict[str, Any]]:
    logger.info(
        "prepare_answer_sheet_started filename=%s page_index=%s",
        filename,
        page_index + 1,
    )
    image = file_bytes_to_image(file_bytes, filename, page_index=page_index)
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
    logger.info(
        "prepare_answer_sheet_completed filename=%s page_index=%s marker_count=%s warped_width=%s warped_height=%s",
        filename,
        page_index + 1,
        len(candidates),
        debug["warped_width"],
        debug["warped_height"],
    )
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
    templates = {name: load_template(name) for name in TEMPLATE_FILES}
    logger.info("templates_loaded names=%s", ",".join(templates.keys()))
    return templates


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


def log_template_points(template_name: str, template: dict[str, Any]) -> None:
    for group in iter_point_groups(template):
        for point in group["points"]:
            logger.info(
                "mean_gray_read template=%s group=%s point=%s x=%s y=%s value=%s mean_gray=%.4f",
                template_name,
                group["name"],
                point["name"],
                point["x"],
                point["y"],
                point["value"],
                point["mean_gray"],
            )


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

    threshold_info = {
        "dark_center": dark_center,
        "light_center": light_center,
        "threshold": threshold,
        "weak_threshold": light_center - separation * 0.20,
        "min_gap": min_gap,
        "strong_gap": min_gap * 1.25,
    }
    logger.info(
        "kmeans_threshold_completed points=%s dark_center=%.4f light_center=%.4f threshold=%.4f",
        len(points),
        threshold_info["dark_center"],
        threshold_info["light_center"],
        threshold_info["threshold"],
    )
    return threshold_info


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


def build_external_submission_id(filename: str | None = None, page_index: int | None = None) -> str:
    stem = Path(filename or "answer-sheet").stem or "answer-sheet"
    if page_index is not None:
        stem = f"{stem}-page-{page_index + 1}"
    return f"scoring-service-omr-{stem}-{uuid4()}"


def sanitize_filename_component(value: str) -> str:
    sanitized = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip(".-")
    return sanitized or "answer-sheet"


def build_debug_image_path(filename: str | None = None, page_index: int = 0) -> Path:
    stem = sanitize_filename_component(Path(filename or "answer-sheet").stem)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return DEBUG_IMAGE_DIR / f"{stem}-page-{page_index + 1}-{timestamp}-{uuid4().hex[:8]}.png"


def build_raw_image_path(filename: str | None = None, page_index: int = 0) -> Path:
    stem = sanitize_filename_component(Path(filename or "answer-sheet").stem)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return RAW_IMAGE_DIR / f"{stem}-page-{page_index + 1}-{timestamp}-{uuid4().hex[:8]}.png"


def path_to_file_url(path: Path) -> str:
    return path.resolve().as_uri()


def resolve_pdf_url(pdf_url: str) -> Path:
    parsed = urlparse(pdf_url)
    if parsed.scheme == "file":
        netloc = f"//{parsed.netloc}" if parsed.netloc else ""
        return Path(unquote(f"{netloc}{parsed.path.lstrip('/')}"))
    if parsed.scheme in {"http", "https"}:
        raise AnswerDetectionError("HTTP/HTTPS pdf_url is not supported yet.")
    if parsed.path.startswith("/storage/"):
        relative_storage_path = parsed.path.removeprefix("/storage/").lstrip("/")
        return STORAGE_ROOT_PATH / Path(unquote(relative_storage_path))
    return Path(pdf_url)


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

    logger.info("detect_mcq_completed questions=%s answered=%s", len(questions), sum(1 for value in answers.values() if value))
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

    logger.info("detect_tfq_completed questions=%s statements=%s", len(questions), len(answers))
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

    logger.info("detect_saq_completed questions=%s answered=%s", len(questions), sum(1 for value in answers.values() if value))
    return {"name": template["name"], "answers": answers, "questions": questions}


def detect_template(template: dict[str, Any]) -> dict[str, Any]:
    threshold_info = kmeans_threshold(all_points(template))
    name = template["name"]
    logger.info(
        "threshold_detected template=%s dark_center=%.4f light_center=%.4f threshold=%.4f weak_threshold=%.4f min_gap=%.4f strong_gap=%.4f",
        name,
        threshold_info["dark_center"],
        threshold_info["light_center"],
        threshold_info["threshold"],
        threshold_info["weak_threshold"],
        threshold_info["min_gap"],
        threshold_info["strong_gap"],
    )

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


def collect_marked_debug_points(
    template_name: str,
    template: dict[str, Any],
    threshold_info: dict[str, float],
) -> list[dict[str, Any]]:
    marked_points = []
    for group in iter_point_groups(template):
        result = classify_group(group["points"], threshold_info)
        group_marked_points = [
            point
            for point in group["points"]
            if point["mean_gray"] <= threshold_info["threshold"]
        ]
        if not group_marked_points and result["selected"] is not None:
            group_marked_points = [result["selected"]]

        for point in group_marked_points:
            marked_points.append(
                {
                    "template": template_name,
                    "group": group["name"],
                    "value": point["value"],
                    "x": int(point["x"]),
                    "y": int(point["y"]),
                    "status": result["status"],
                    "mean_gray": float(point["mean_gray"]),
                }
            )

    return marked_points


def collect_template_points(
    template_name: str,
    template: dict[str, Any],
) -> list[dict[str, Any]]:
    points = []
    for group in iter_point_groups(template):
        for point in group["points"]:
            points.append(
                {
                    "template": template_name,
                    "group": group["name"],
                    "point": point["name"],
                    "value": point["value"],
                    "x": int(point["x"]),
                    "y": int(point["y"]),
                }
            )
    return points


def render_template_points_image(
    image: np.ndarray,
    template_points: list[dict[str, Any]],
) -> bytes:
    debug_image = image.copy()
    for point in template_points:
        color = TEMPLATE_POINT_COLORS[point["template"]]
        center = (point["x"], point["y"])
        cv2.circle(debug_image, center, 8, color, 2)
        cv2.circle(debug_image, center, 3, color, -1)

    success, encoded = cv2.imencode(".png", debug_image)
    if not success:
        raise AnswerDetectionError("Could not encode debug image.")

    return encoded.tobytes()


def save_marked_debug_image(
    image: np.ndarray,
    marked_points: list[dict[str, Any]],
    filename: str | None = None,
    page_index: int = 0,
) -> Path:
    DEBUG_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    debug_image = image.copy()
    for point in marked_points:
        center = (point["x"], point["y"])
        cv2.circle(debug_image, center, 10, DEBUG_MARK_COLORS["outer"], 3)
        cv2.circle(debug_image, center, 4, DEBUG_MARK_COLORS["inner"], -1)

    output_path = build_debug_image_path(filename, page_index=page_index)
    if not cv2.imwrite(str(output_path), debug_image):
        raise AnswerDetectionError(f"Could not write debug image: {output_path}")

    logger.info(
        "debug_image_saved path=%s page=%s marked_points=%s",
        output_path,
        page_index + 1,
        len(marked_points),
    )
    return output_path


def save_raw_image(
    image: np.ndarray,
    filename: str | None = None,
    page_index: int = 0,
) -> Path:
    RAW_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    output_path = build_raw_image_path(filename, page_index=page_index)
    if not cv2.imwrite(str(output_path), image):
        raise AnswerDetectionError(f"Could not write raw image: {output_path}")
    logger.info("raw_image_saved path=%s page=%s", output_path, page_index + 1)
    return output_path


def process_answer_sheet(
    file_bytes: bytes,
    filename: str | None = None,
    page_index: int = 0,
    include_page_number: bool = False,
) -> dict[str, Any]:
    logger.info(
        "process_answer_sheet_started filename=%s page_index=%s include_page_number=%s",
        filename,
        page_index + 1,
        include_page_number,
    )
    warped, debug = prepare_answer_sheet(file_bytes, filename, page_index=page_index)
    logger.info("process_answer_sheet_debug filename=%s page_index=%s debug=%s", filename, page_index + 1, debug)
    templates = load_templates()
    enriched = {
        name: enrich_template_with_mean_gray(template, warped)
        for name, template in templates.items()
    }
    logger.info("templates_enriched_with_mean_gray filename=%s page_index=%s", filename, page_index + 1)
    for template_name, template in enriched.items():
        log_template_points(template_name, template)

    candidate_result = detect_template(enriched["candidate_id"])
    exam_result = detect_template(enriched["exam_id"])
    part_1_result = detect_template(enriched["part_1"])
    part_2_result = detect_template(enriched["part_2"])
    part_3_result = detect_template(enriched["part_3"])
    marked_debug_points = []
    for template_name, template_result in {
        "candidate_id": candidate_result,
        "exam_id": exam_result,
        "part_1": part_1_result,
        "part_2": part_2_result,
        "part_3": part_3_result,
    }.items():
        marked_debug_points.extend(
            collect_marked_debug_points(
                template_name,
                enriched[template_name],
                template_result["threshold"],
            )
        )
    debug_image_path = save_marked_debug_image(
        warped,
        marked_debug_points,
        filename=filename,
        page_index=page_index,
    )
    raw_image_path = save_raw_image(
        warped,
        filename=filename,
        page_index=page_index,
    )

    result = {
        "examUuid": None,
        "paperCode": exam_result["answer"],
        "studentUuid": candidate_result["answer"],
        "externalSubmissionId": build_external_submission_id(
            filename,
            page_index=page_index if include_page_number else None,
        ),
        "scannedAt": utc_now_iso(),
        "debugImagePath": str(debug_image_path),
        "rawImagePath": str(raw_image_path),
        "rawImageUrl": path_to_file_url(raw_image_path),
        "scoredImageUrl": path_to_file_url(debug_image_path),
        "sections": build_omr_sections(part_1_result, part_2_result, part_3_result),
    }
    if include_page_number:
        result["pageNumber"] = page_index + 1

    logger.info(
        "process_answer_sheet_completed filename=%s page_index=%s paper_code=%s student_code=%s mcq=%s tfq=%s saq=%s",
        filename,
        page_index + 1,
        result["paperCode"],
        result["studentUuid"],
        len(result["sections"]["mcq"]),
        len(result["sections"]["tfq"]),
        len(result["sections"]["saq"]),
    )
    return result


def process_pdf_answer_sheets(file_bytes: bytes, filename: str | None = None) -> list[dict[str, Any]]:
    page_count = get_pdf_page_count(file_bytes)
    if page_count == 0:
        raise AnswerDetectionError("PDF has no pages.")

    logger.info("process_pdf_answer_sheets_started filename=%s page_count=%s", filename, page_count)
    results = []
    for page_index in range(page_count):
        logger.info("process_pdf_page_started filename=%s page_index=%s", filename, page_index + 1)
        results.append(
            process_answer_sheet(
                file_bytes,
                filename=filename,
                page_index=page_index,
                include_page_number=True,
            )
        )
        logger.info("process_pdf_page_completed filename=%s page_index=%s", filename, page_index + 1)
    logger.info("process_pdf_answer_sheets_completed filename=%s page_count=%s", filename, page_count)
    return results


def process_answer_sheet_from_pdf_url(
    pdf_url: str,
    exam_uuid: str,
    request_scanned_at: str | None = None,
) -> list[dict[str, Any]]:
    logger.info(
        "process_answer_sheet_from_pdf_url_started pdf_url=%s exam_uuid=%s request_scanned_at=%s",
        pdf_url,
        exam_uuid,
        request_scanned_at,
    )
    pdf_path = resolve_pdf_url(pdf_url)
    logger.info("pdf_url_resolved pdf_url=%s resolved_path=%s", pdf_url, pdf_path)
    if not pdf_path.exists():
        raise AnswerDetectionError(f"PDF file not found: {pdf_url}")

    file_bytes = pdf_path.read_bytes()
    logger.info("pdf_file_loaded path=%s bytes=%s", pdf_path, len(file_bytes))
    results = process_pdf_answer_sheets(file_bytes, filename=pdf_path.name)
    scanned_at = request_scanned_at or utc_now_iso()

    for result in results:
        result["examUuid"] = exam_uuid
        result["scannedAt"] = scanned_at

    logger.info(
        "process_answer_sheet_from_pdf_url_completed pdf_url=%s exam_uuid=%s pages=%s",
        pdf_url,
        exam_uuid,
        len(results),
    )
    return results


def generate_template_points_preview(
    file_bytes: bytes,
    filename: str | None = None,
    page_index: int = 0,
) -> bytes:
    logger.info("generate_template_points_preview_started filename=%s page_index=%s", filename, page_index + 1)
    warped, _ = prepare_answer_sheet(file_bytes, filename, page_index=page_index)
    templates = load_templates()
    template_points = []
    for template_name, template in templates.items():
        template_points.extend(collect_template_points(template_name, template))

    image_bytes = render_template_points_image(warped, template_points)
    logger.info(
        "generate_template_points_preview_completed filename=%s page_index=%s point_count=%s",
        filename,
        page_index + 1,
        len(template_points),
    )
    return image_bytes


def generate_pdf_template_points_preview_archive(
    file_bytes: bytes,
    filename: str | None = None,
) -> bytes:
    page_count = get_pdf_page_count(file_bytes)
    if page_count == 0:
        raise AnswerDetectionError("PDF has no pages.")

    logger.info(
        "generate_pdf_template_points_preview_archive_started filename=%s page_count=%s",
        filename,
        page_count,
    )
    archive_buffer = BytesIO()
    stem = sanitize_filename_component(Path(filename or "answer-sheet").stem)

    with ZipFile(archive_buffer, mode="w", compression=ZIP_DEFLATED) as archive:
        for page_index in range(page_count):
            image_bytes = generate_template_points_preview(
                file_bytes,
                filename=filename,
                page_index=page_index,
            )
            archive.writestr(
                f"{stem}-page-{page_index + 1}.png",
                image_bytes,
            )

    archive_bytes = archive_buffer.getvalue()
    logger.info(
        "generate_pdf_template_points_preview_archive_completed filename=%s page_count=%s archive_bytes=%s",
        filename,
        page_count,
        len(archive_bytes),
    )
    return archive_bytes
