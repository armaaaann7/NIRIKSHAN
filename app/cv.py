from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass
class Quality:
    ok: bool
    severe: bool
    blur_variance: float
    brightness: float
    width: int
    height: int
    messages: list[str]


def read_image(path: str | Path) -> np.ndarray:
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Could not read uploaded image")
    return img


def assess_quality(img: np.ndarray) -> Quality:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    brightness = float(gray.mean())
    h, w = gray.shape
    messages: list[str] = []
    severe = False

    if blur < 35:
        messages.append("Image appears blurry. Hold the phone steady and recapture if possible.")
    if blur < 10:
        severe = True
    if brightness < 45:
        messages.append("Image is dark. Increase lighting or exposure.")
    if brightness > 220:
        messages.append("Image is overexposed. Reduce glare/exposure.")
    if brightness < 15 or brightness > 245:
        severe = True
    if min(h, w) < 500:
        messages.append("Low resolution. Use a higher-resolution capture for better crack detail.")
    if not messages:
        messages.append("Image quality is suitable for prototype analysis.")

    return Quality(not severe, severe, blur, brightness, w, h, messages)


def _resize_for_alignment(img: np.ndarray, max_dim: int = 1400) -> tuple[np.ndarray, float]:
    h, w = img.shape[:2]
    scale = min(1.0, max_dim / max(h, w))
    if scale == 1.0:
        return img, 1.0
    return cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA), scale


def _corner_displacement(H: np.ndarray, width: int, height: int) -> float:
    corners = np.float32([[[0, 0]], [[width - 1, 0]], [[width - 1, height - 1]], [[0, height - 1]]])
    warped = cv2.perspectiveTransform(corners, H)
    return float(np.linalg.norm(warped.reshape(-1, 2) - corners.reshape(-1, 2), axis=1).max())


def _homography_sane(H: np.ndarray, width: int, height: int) -> bool:
    if not np.isfinite(H).all() or abs(H[2, 2]) < 1e-9:
        return False
    corners = np.float32([[[0, 0]], [[width - 1, 0]], [[width - 1, height - 1]], [[0, height - 1]]])
    warped = cv2.perspectiveTransform(corners, H).reshape(-1, 2)
    area = abs(cv2.contourArea(warped.astype(np.float32)))
    original_area = float(max((width - 1) * (height - 1), 1))
    ratio = area / original_area
    return 0.60 <= ratio <= 1.45


def align_to_reference(reference: np.ndarray, current: np.ndarray):
    """Align ``current`` onto ``reference`` using ORB + RANSAC homography.

    A homography is appropriate only for roughly planar capture points photographed from a
    similar viewpoint. The function therefore rejects implausible transformations and marks
    the resize-only fallback as low confidence.
    """

    ref_small, s_ref = _resize_for_alignment(reference)
    cur_small, s_cur = _resize_for_alignment(current)
    ref_gray = cv2.cvtColor(ref_small, cv2.COLOR_BGR2GRAY)
    cur_gray = cv2.cvtColor(cur_small, cv2.COLOR_BGR2GRAY)

    orb = cv2.ORB_create(nfeatures=5000, scaleFactor=1.2, nlevels=8)
    kp1, des1 = orb.detectAndCompute(ref_gray, None)
    kp2, des2 = orb.detectAndCompute(cur_gray, None)

    if des1 is not None and des2 is not None and len(kp1) >= 12 and len(kp2) >= 12:
        matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
        raw = matcher.knnMatch(des2, des1, k=2)
        good = [pair[0] for pair in raw if len(pair) == 2 and pair[0].distance < 0.75 * pair[1].distance]

        if len(good) >= 10:
            src = np.float32([kp2[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
            dst = np.float32([kp1[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
            H_small, mask = cv2.findHomography(src, dst, cv2.RANSAC, 4.0)
            if H_small is not None and mask is not None:
                inliers = int(mask.sum())
                inlier_ratio = inliers / max(len(good), 1)
                S_ref = np.array([[s_ref, 0, 0], [0, s_ref, 0], [0, 0, 1]], dtype=np.float64)
                S_cur = np.array([[s_cur, 0, 0], [0, s_cur, 0], [0, 0, 1]], dtype=np.float64)
                H = np.linalg.inv(S_ref) @ H_small @ S_cur
                h, w = reference.shape[:2]

                if inliers >= 10 and inlier_ratio >= 0.35 and _homography_sane(H, w, h):
                    confidence = float(min(1.0, inlier_ratio * min(1.0, inliers / 40.0)))
                    # Avoid needless sub-pixel resampling for effectively identical captures.
                    if current.shape[:2] == reference.shape[:2] and _corner_displacement(H, w, h) < 0.75:
                        return current.copy(), confidence, "ORB homography (near-identity; warp skipped)", H
                    aligned = cv2.warpPerspective(
                        current,
                        H,
                        (w, h),
                        flags=cv2.INTER_LINEAR,
                        borderMode=cv2.BORDER_REFLECT,
                    )
                    return aligned, confidence, "ORB homography", H

    aligned = cv2.resize(current, (reference.shape[1], reference.shape[0]), interpolation=cv2.INTER_AREA)
    return aligned, 0.15, "resize fallback (low confidence)", None


def normalize_illumination(img: np.ndarray) -> np.ndarray:
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l = clahe.apply(l)
    return cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2BGR)


def _remove_small_components(
    mask: np.ndarray,
    min_area: int | None = None,
    max_area_ratio: float = 0.035,
    min_span: int = 8,
) -> np.ndarray:
    """Suppress tiny resampling/compression components and very large filled regions."""

    if min_area is None:
        min_area = max(40, int(mask.size * 0.00003))
    n, labels, stats, _ = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), connectivity=8)
    out = np.zeros_like(mask)
    max_area = mask.size * max_area_ratio
    for i in range(1, n):
        area = int(stats[i, cv2.CC_STAT_AREA])
        width = int(stats[i, cv2.CC_STAT_WIDTH])
        height = int(stats[i, cv2.CC_STAT_HEIGHT])
        if min_area <= area <= max_area and max(width, height) >= min_span:
            out[labels == i] = 255
    return out


def segment_cracks(img: np.ndarray) -> np.ndarray:
    """Classical-CV crack *candidate* baseline.

    This deliberately remains a replaceable baseline, not a validated structural-damage
    model. It now suppresses small JPEG/resampling components that caused severe false
    positives in the original baseline, but competition accuracy must still be established with a real
    benchmark and a trained segmentation model.
    """

    norm = normalize_illumination(img)
    gray = cv2.cvtColor(norm, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (11, 11))
    blackhat = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, kernel)
    blackhat = cv2.GaussianBlur(blackhat, (3, 3), 0)
    _, mask = cv2.threshold(blackhat, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # Percentile gating is retained only as a second gate; component filtering prevents the
    # gate from forcing thousands of compression specks into the final mask.
    strong_threshold = max(float(np.percentile(blackhat, 82)), 18.0)
    strong = np.where(blackhat >= strong_threshold, 255, 0).astype(np.uint8)
    mask = cv2.bitwise_and(mask, strong)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    return _remove_small_components(mask)


def segment_spalling_candidates(img: np.ndarray) -> np.ndarray:
    """Experimental texture-based surface-damage candidate mask.

    This output is visual-only and is excluded from the priority score until a
    labelled spalling model is validated.
    """

    norm = normalize_illumination(img)
    gray = cv2.cvtColor(norm, cv2.COLOR_BGR2GRAY)
    lap = cv2.Laplacian(gray, cv2.CV_32F, ksize=3)
    rough = cv2.GaussianBlur(np.abs(lap), (15, 15), 0)
    threshold = float(np.percentile(rough, 92))
    mask = (rough > max(threshold, 8.0)).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    return _remove_small_components(mask, min_area=max(100, int(mask.size * 0.00008)), max_area_ratio=0.12)


def mask_metrics(mask: np.ndarray) -> dict:
    binary = mask > 0
    pixels = int(binary.sum())
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    lengths = [cv2.arcLength(c, False) for c in contours if cv2.contourArea(c) >= 2]
    orientations = []
    for contour in contours:
        if len(contour) >= 5:
            (_, _), (minor_axis, major_axis), angle = cv2.fitEllipse(contour)
            if max(minor_axis, major_axis) > 4:
                orientations.append(float(angle))
    return {
        "pixel_count": pixels,
        "pixel_fraction": round(pixels / mask.size, 6),
        "component_count": len(contours),
        "relative_extent_px": round(float(sum(lengths)), 1),
        "dominant_orientation_deg": round(float(np.median(orientations)), 1) if orientations else None,
    }


def _filter_new_components(mask: np.ndarray) -> np.ndarray:
    min_area = max(4, min(30, int(mask.size * 0.00003)))
    n, labels, stats, _ = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), connectivity=8)
    out = np.zeros_like(mask)
    for i in range(1, n):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area >= min_area:
            out[labels == i] = 255
    return out


def compare_masks(previous: np.ndarray, current: np.ndarray, tolerance_px: int = 6) -> tuple[np.ndarray, dict]:
    """Compare binary masks with spatial tolerance and an absolute-evidence floor.

    The original baseline normalised new pixels almost entirely by the previous mask size. When the baseline
    mask was tiny, a few JPEG/resampling specks saturated the score. The current logic requires both a
    meaningful image-area change and relative growth before the indicator becomes large.
    """

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (tolerance_px * 2 + 1, tolerance_px * 2 + 1))
    prev_dilated = cv2.dilate(previous, kernel)
    new_mask = cv2.bitwise_and(current, cv2.bitwise_not(prev_dilated))
    new_mask = _filter_new_components(new_mask)

    prev_pixels = int((previous > 0).sum())
    cur_pixels = int((current > 0).sum())
    new_pixels = int((new_mask > 0).sum())
    image_pixels = int(previous.size)

    new_fraction = new_pixels / max(image_pixels, 1)
    baseline_floor = max(int(image_pixels * 0.0015), 1)
    relative_growth = new_pixels / max(prev_pixels, baseline_floor)
    relative_total_change = (cur_pixels - prev_pixels) / max(prev_pixels, baseline_floor)

    # A small absolute image fraction is deliberately treated as weak evidence. These are
    # prototype calibration constants and must be re-tuned from benchmark data.
    magnitude = float(np.clip((new_fraction - 0.00015) / 0.0015, 0.0, 1.0))
    growth = float(np.clip(relative_growth, 0.0, 1.0))
    normalized = float(np.clip(0.80 * magnitude + 0.20 * growth, 0.0, 1.0))

    metrics = {
        "previous_pixels": prev_pixels,
        "current_pixels": cur_pixels,
        "new_pixels": new_pixels,
        "new_pixel_fraction": round(float(new_fraction), 6),
        "relative_growth": round(float(relative_growth), 4),
        "relative_total_change": round(float(relative_total_change), 4),
        "normalized_change": round(normalized, 4),
    }
    return new_mask, metrics


def make_overlay(reference: np.ndarray, aligned_current: np.ndarray, new_crack: np.ndarray, new_spall: np.ndarray) -> np.ndarray:
    blended = cv2.addWeighted(reference, 0.45, aligned_current, 0.55, 0)
    blended[new_crack > 0] = (40, 40, 230)   # red: crack candidate
    blended[new_spall > 0] = (30, 170, 230)  # amber: experimental surface-change candidate
    return blended


def analyze_pair(reference: np.ndarray, current: np.ndarray) -> dict:
    aligned, align_conf, method, _ = align_to_reference(reference, current)
    ref_norm = normalize_illumination(reference)
    cur_norm = normalize_illumination(aligned)

    ref_crack = segment_cracks(ref_norm)
    cur_crack = segment_cracks(cur_norm)
    new_crack, crack_change = compare_masks(ref_crack, cur_crack, tolerance_px=6)

    ref_spall = segment_spalling_candidates(ref_norm)
    cur_spall = segment_spalling_candidates(cur_norm)
    new_spall, spall_change = compare_masks(ref_spall, cur_spall, tolerance_px=9)

    ref_fraction = mask_metrics(ref_crack)["pixel_fraction"]
    cur_fraction = mask_metrics(cur_crack)["pixel_fraction"]
    # A sudden mask explosion is usually compression/resampling/texture instability in this
    # classical baseline. Quarantine it instead of converting it into an urgent score.
    segmentation_stable = not (cur_fraction > 0.02 and cur_fraction > max(ref_fraction * 20.0, ref_fraction + 0.01))

    return {
        "aligned": aligned,
        "alignment_confidence": round(float(align_conf), 3),
        "alignment_method": method,
        "segmentation_stable": segmentation_stable,
        "reference_crack_mask": ref_crack,
        "current_crack_mask": cur_crack,
        "new_crack_mask": new_crack,
        "reference_spall_mask": ref_spall,
        "current_spall_mask": cur_spall,
        "new_spall_mask": new_spall,
        "overlay": make_overlay(reference, aligned, new_crack, new_spall),
        "crack_reference": mask_metrics(ref_crack),
        "crack_current": mask_metrics(cur_crack),
        "crack_new": mask_metrics(new_crack),
        "crack_change": crack_change,
        "spall_reference": mask_metrics(ref_spall),
        "spall_current": mask_metrics(cur_spall),
        "spall_new": mask_metrics(new_spall),
        "spall_change": spall_change,
    }
