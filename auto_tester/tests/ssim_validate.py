"""SSIM-based screenshot comparison with union text masking.

Public API: evaluate(ref, actual) -> EvalResult
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import easyocr
import numpy as np
from skimage.metrics import structural_similarity as ssim

# ── Config ────────────────────────────────────────────────────────────────────
MODEL_DIR = "models/easyocr"
SSIM_THRESHOLD = 0.85
_MASK_PADDING = 4

_reader: easyocr.Reader | None = None


def _get_reader() -> easyocr.Reader:
    global _reader
    if _reader is None:
        _reader = easyocr.Reader(["en"], model_storage_directory=MODEL_DIR)
    return _reader


@dataclass
class EvalResult:
    score: float
    passed: bool


# ── Preprocessing ────────────────────────────────────────────────────────────

_UPSCALE_FACTOR = 2.0


def preprocess(img: np.ndarray) -> np.ndarray:
    """Upscale 2× then apply CLAHE — improves OCR on small phone-screen text."""
    up = cv2.resize(
        img, None, fx=_UPSCALE_FACTOR, fy=_UPSCALE_FACTOR, interpolation=cv2.INTER_CUBIC
    )
    lab = cv2.cvtColor(up, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    l = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(l)
    return cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2BGR)


# ── Internal helpers ──────────────────────────────────────────────────────────


def _detection_boxes(
    img: np.ndarray,
) -> list[tuple[int, int, int, int]]:
    processed = preprocess(img)
    detections = _get_reader().readtext(
        processed, paragraph=False, text_threshold=0.3, x_ths=0.5
    )
    boxes = []
    for bbox, _, _ in detections:
        pts = (np.array(bbox, dtype=np.float32) / _UPSCALE_FACTOR).astype(np.int32)
        x1 = max(0, pts[:, 0].min() - _MASK_PADDING)
        y1 = max(0, pts[:, 1].min() - _MASK_PADDING)
        x2 = min(img.shape[1], pts[:, 0].max() + _MASK_PADDING)
        y2 = min(img.shape[0], pts[:, 1].max() + _MASK_PADDING)
        boxes.append((x1, y1, x2, y2))
    return boxes


def _apply_union_mask(
    img: np.ndarray, boxes: list[tuple[int, int, int, int]]
) -> np.ndarray:
    masked = np.zeros_like(img)  # start black — background excluded
    for x1, y1, x2, y2 in boxes:
        masked[y1:y2, x1:x2] = 128  # uniform fill — layout yes, content no
    return masked


def _binarize(img_bgr: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return binary


# ── Public API ────────────────────────────────────────────────────────────────


def evaluate(
    ref: str,
    actual: str,
) -> EvalResult:
    """Run the full SSIM evaluation pipeline and return an EvalResult."""
    ref_bgr = cv2.imread(str(ref))
    if ref_bgr is None:
        raise FileNotFoundError(f"Could not read reference image: {ref}")
    actual_bgr = cv2.imread(str(actual))
    if actual_bgr is None:
        raise FileNotFoundError(f"Could not read actual image: {actual}")

    union_boxes = list({*_detection_boxes(ref_bgr), *_detection_boxes(actual_bgr)})
    ref_masked_bgr = _apply_union_mask(ref_bgr, union_boxes)
    act_masked_bgr = _apply_union_mask(actual_bgr, union_boxes)
    ref_bin = _binarize(ref_masked_bgr)
    act_bin = _binarize(act_masked_bgr)
    if ref_bin.shape != act_bin.shape:
        raise ValueError(
            f"Resolution mismatch: reference={ref_bin.shape}, actual={act_bin.shape}. "
            "Re-capture the reference screenshot."
        )
    score, diff = ssim(ref_bin, act_bin, full=True)
    return EvalResult(score=float(score), passed=score >= SSIM_THRESHOLD)
