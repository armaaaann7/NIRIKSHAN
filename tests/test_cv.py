from pathlib import Path

import cv2
import numpy as np

from app.cv import align_to_reference, analyze_pair, assess_quality, compare_masks

ROOT = Path(__file__).resolve().parents[1]


def test_compare_masks_detects_new_region():
    previous = np.zeros((100, 100), np.uint8)
    current = previous.copy()
    cv2.line(previous, (20, 20), (50, 50), 255, 1)
    cv2.line(current, (20, 20), (80, 80), 255, 1)
    new, metrics = compare_masks(previous, current, 2)
    assert new.sum() > 0
    assert metrics["normalized_change"] > 0


def test_quality_returns_dimensions():
    img = np.full((600, 800, 3), 128, np.uint8)
    quality = assess_quality(img)
    assert quality.width == 800 and quality.height == 600


def test_jpeg_recompression_does_not_become_urgent():
    reference = cv2.imread(str(ROOT / "sample_data" / "demo_baseline.jpg"))
    ok, encoded = cv2.imencode(".jpg", reference, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
    assert ok
    current = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    result = analyze_pair(reference, current)
    assert result["alignment_confidence"] > 0.45
    assert result["segmentation_stable"] is True
    assert result["crack_change"]["normalized_change"] <= 0.30


def test_heavy_compression_is_quarantinable_instead_of_urgent():
    reference = cv2.imread(str(ROOT / "sample_data" / "demo_baseline.jpg"))
    ok, encoded = cv2.imencode(".jpg", reference, [int(cv2.IMWRITE_JPEG_QUALITY), 60])
    assert ok
    current = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    result = analyze_pair(reference, current)
    # For this synthetic control, heavy compression causes a crack-mask explosion. The
    # pipeline should mark it unstable rather than treating it as trustworthy deterioration.
    assert result["segmentation_stable"] is False


def test_small_rotation_aligns_without_large_change():
    reference = cv2.imread(str(ROOT / "sample_data" / "demo_baseline.jpg"))
    h, w = reference.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), 3.0, 1.0)
    matrix[:, 2] += np.array([8, -5])
    current = cv2.warpAffine(reference, matrix, (w, h), borderMode=cv2.BORDER_REFLECT)
    result = analyze_pair(reference, current)
    assert result["alignment_confidence"] > 0.45
    assert result["segmentation_stable"] is True
    assert result["crack_change"]["normalized_change"] <= 0.30
