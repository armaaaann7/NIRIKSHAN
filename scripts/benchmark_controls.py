from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cv2
import numpy as np

from app.cv import analyze_pair
from app.scoring import inspection_priority

DEFAULT_IMAGE = ROOT / "sample_data" / "demo_baseline.jpg"
OUTPUT_DIR = ROOT / "benchmarks"
ALIGNMENT_THRESHOLD = 0.45


def gamma_change(img: np.ndarray, gamma: float) -> np.ndarray:
    table = np.array([((i / 255.0) ** gamma) * 255 for i in np.arange(256)]).astype("uint8")
    return cv2.LUT(img, table)


def brightness_change(img: np.ndarray, delta: int) -> np.ndarray:
    return cv2.convertScaleAbs(img, alpha=1.0, beta=delta)


def jpeg_recompress(img: np.ndarray, quality: int) -> np.ndarray:
    ok, encoded = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise RuntimeError("JPEG encode failed")
    return cv2.imdecode(encoded, cv2.IMREAD_COLOR)


def gaussian_noise(img: np.ndarray, sigma: float = 4.0) -> np.ndarray:
    rng = np.random.default_rng(42)
    noise = rng.normal(0, sigma, img.shape).astype(np.float32)
    return np.clip(img.astype(np.float32) + noise, 0, 255).astype(np.uint8)


def shadow_gradient(img: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    gradient = np.linspace(0.58, 1.0, w, dtype=np.float32)[None, :, None]
    return np.clip(img.astype(np.float32) * gradient, 0, 255).astype(np.uint8)


def rotate_shift(img: np.ndarray, degrees: float, dx: float, dy: float) -> np.ndarray:
    h, w = img.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), degrees, 1.0)
    matrix[:, 2] += np.array([dx, dy])
    return cv2.warpAffine(img, matrix, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


def perspective_warp(img: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    src = np.float32([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]])
    dst = np.float32([[8, 7], [w - 12, 2], [w - 4, h - 9], [12, h - 3]])
    H = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(img, H, (w, h), borderMode=cv2.BORDER_REFLECT)


def staged_cracks(img: np.ndarray, count: int) -> np.ndarray:
    """Draw separated synthetic crack-like marks for pipeline regression only."""
    out = img.copy()
    h, w = out.shape[:2]
    starts = [(0.20, 0.25), (0.35, 0.55), (0.55, 0.35), (0.70, 0.60), (0.78, 0.28)]
    for x, y in starts[:count]:
        points = np.array([
            [int(w * x), int(h * y)],
            [int(w * (x + 0.015)), int(h * (y + 0.035))],
            [int(w * (x - 0.008)), int(h * (y + 0.070))],
            [int(w * (x + 0.018)), int(h * (y + 0.105))],
        ], dtype=np.int32)
        cv2.polylines(out, [points], False, (20, 20, 20), thickness=3, lineType=cv2.LINE_AA)
    return out


def build_cases(img: np.ndarray) -> list[tuple[str, str, np.ndarray]]:
    return [
        ("identical", "unchanged", img.copy()),
        ("jpeg_q90", "unchanged", jpeg_recompress(img, 90)),
        ("jpeg_q75", "unchanged", jpeg_recompress(img, 75)),
        ("jpeg_q60", "unchanged", jpeg_recompress(img, 60)),
        ("gamma_0_8", "unchanged", gamma_change(img, 0.8)),
        ("gamma_1_2", "unchanged", gamma_change(img, 1.2)),
        ("brightness_plus_40", "unchanged", brightness_change(img, 40)),
        ("brightness_minus_40", "unchanged", brightness_change(img, -40)),
        ("mild_noise", "unchanged", gaussian_noise(img, 4.0)),
        ("shadow_gradient", "unchanged", shadow_gradient(img)),
        ("rotate_1deg_shift", "unchanged", rotate_shift(img, 1.0, 4, -3)),
        ("rotate_3deg_shift", "unchanged", rotate_shift(img, 3.0, 8, -5)),
        ("mild_perspective", "unchanged", perspective_warp(img)),
        ("single_staged_mark", "single_changed", staged_cracks(img, 2)),
        ("multiple_staged_marks_3", "multi_changed", staged_cracks(img, 3)),
        ("multiple_staged_marks_4", "multi_changed", staged_cracks(img, 4)),
    ]


def run(image_path: Path) -> dict:
    reference = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if reference is None:
        raise SystemExit(f"Could not read benchmark image: {image_path}")

    records: list[dict] = []
    for name, label, current in build_cases(reference):
        result = analyze_pair(reference, current)
        reliable = result["alignment_confidence"] >= ALIGNMENT_THRESHOLD and result.get("segmentation_stable", True)
        crack_change = float(result["crack_change"]["normalized_change"]) if reliable else 0.0
        new_regions = int(result.get("crack_new", {}).get("component_count", 0)) if reliable else 0
        current_regions = int(result.get("crack_current", {}).get("component_count", 0)) if reliable else 0
        previous_regions = int(result.get("crack_reference", {}).get("component_count", 0)) if reliable else 0
        priority = inspection_priority(
            crack_change,
            None,
            new_crack_regions=new_regions,
            current_crack_regions=current_regions,
            previous_crack_regions=previous_regions,
            element_type="Structural wall",
            structure_type="Load-bearing masonry",
            age_years=78,
        ) if reliable else None
        records.append({
            "case": name,
            "label": label,
            "alignment_confidence": result["alignment_confidence"],
            "reliable": reliable,
            "crack_change": crack_change,
            "new_crack_regions": new_regions,
            "spall_visual_change": float(result["spall_change"]["normalized_change"]),
            "score": priority.score if priority else None,
            "band": priority.band if priority else "Recapture required",
        })

    unchanged_reliable = [r for r in records if r["label"] == "unchanged" and r["reliable"]]
    changed_reliable = [r for r in records if r["label"] != "unchanged" and r["reliable"]]
    multi_reliable = [r for r in records if r["label"] == "multi_changed" and r["reliable"]]
    false_alarms = [r for r in unchanged_reliable if (r["score"] or 0) > 30]
    visual_changed_detected = [r for r in changed_reliable if r["crack_change"] >= 0.20]
    multi_priority_detected = [r for r in multi_reliable if (r["score"] or 0) > 30]

    return {
        "source_image": str(image_path),
        "scope_note": "Synthetic control benchmark from one image. It measures pipeline regression/failure modes, not real-facade accuracy or structural safety.",
        "priority_rule_note": "Single isolated crack-like evidence is intentionally allowed to remain Routine; review escalation is evaluated separately on multiple staged regions.",
        "summary": {
            "unchanged_reliable_cases": len(unchanged_reliable),
            "false_alarm_count": len(false_alarms),
            "false_alarm_rate": round(len(false_alarms) / max(len(unchanged_reliable), 1), 4),
            "changed_reliable_cases": len(changed_reliable),
            "visual_change_detection_recall": round(len(visual_changed_detected) / max(len(changed_reliable), 1), 4),
            "multi_region_priority_recall": round(len(multi_priority_detected) / max(len(multi_reliable), 1), 4),
        },
        "cases": records,
    }


def write_outputs(report: dict) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "benchmark_latest.json").write_text(json.dumps(report, indent=2))

    fields = ["case", "label", "alignment_confidence", "reliable", "crack_change", "new_crack_regions", "spall_visual_change", "score", "band"]
    with (OUTPUT_DIR / "benchmark_latest.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(report["cases"])

    summary = report["summary"]
    lines = [
        "# NIRIKSHAN synthetic control benchmark",
        "",
        f"> {report['scope_note']}",
        "",
        f"> {report['priority_rule_note']}",
        "",
        f"- Unchanged reliable cases: **{summary['unchanged_reliable_cases']}**",
        f"- False alarms (>30 priority): **{summary['false_alarm_count']}**",
        f"- Synthetic unchanged-pair false-alarm rate: **{summary['false_alarm_rate']:.1%}**",
        f"- Synthetic visible-change detection recall: **{summary['visual_change_detection_recall']:.1%}**",
        f"- Synthetic multi-region priority recall (>30): **{summary['multi_region_priority_recall']:.1%}**",
        "",
        "| Case | Label | Align | Crack change | New regions | Score | Band |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for row in report["cases"]:
        score = "—" if row["score"] is None else f"{row['score']:.1f}"
        lines.append(f"| {row['case']} | {row['label']} | {row['alignment_confidence']:.3f} | {row['crack_change']:.3f} | {row['new_crack_regions']} | {score} | {row['band']} |")
    (OUTPUT_DIR / "benchmark_latest.md").write_text("\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run NIRIKSHAN synthetic unchanged/changed control pairs.")
    parser.add_argument("--image", type=Path, default=DEFAULT_IMAGE, help="Reference exterior image to perturb")
    args = parser.parse_args()
    report = run(args.image)
    write_outputs(report)
    print(json.dumps(report["summary"], indent=2))
    print(f"Wrote benchmark outputs to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
