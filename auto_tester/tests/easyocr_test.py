"""EasyOCR helper for desk-phone screen captures using upscale + CLAHE preprocessing."""

from __future__ import annotations

import cv2
import easyocr

MODEL_DIR = "models/easyocr"
CONFIDENCE_THRESHOLD = 0.3

_reader: easyocr.Reader | None = None


def _get_reader() -> easyocr.Reader:
    global _reader
    if _reader is None:
        _reader = easyocr.Reader(["en"], model_storage_directory=MODEL_DIR)
    return _reader


def read_screen(image_path: str) -> list[tuple[str, list]]:
    """Run OCR on a phone screen image using upscale + CLAHE preprocessing.

    Returns a list of text and their coordinates.
    """
    img = cv2.imread(image_path)
    if img is None:
        raise FileNotFoundError(f"Could not read: {image_path}")

    up = cv2.resize(img, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    lab = cv2.cvtColor(up, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    l = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(l)
    processed = cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2BGR)

    detections = _get_reader().readtext(processed, paragraph=False)

    return [
        (text, bbox) for bbox, text, conf in detections if conf >= CONFIDENCE_THRESHOLD
    ]


def describe_screen(image_path: str) -> str:
    """Return a JSON string of detected labels with coordinates."""
    import json

    detections = read_screen(image_path)
    if not detections:
        return '{"labels": []}'

    img = cv2.imread(image_path)
    if img is None:
        return '{"labels": []}'
    h, w = img.shape[:2]

    # Convert 2x-upscaled bbox centres back to original image coordinates
    items: list[tuple[float, float, str]] = []
    for text, bbox in detections:
        cx = sum(pt[0] for pt in bbox) / (4 * 2)
        cy = sum(pt[1] for pt in bbox) / (4 * 2)
        items.append((cy, cx, text))
    items.sort()  # top-to-bottom, left-to-right

    labels = [
        {"text": text, "x": round(cx / w, 2), "y": round(cy / h, 2)}
        for cy, cx, text in items
    ]

    return json.dumps({"w": w, "h": h, "labels": labels})
