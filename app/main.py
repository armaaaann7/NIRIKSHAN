from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import secrets
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import cv2
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from . import db
from .cv import analyze_pair, assess_quality, read_image
from .scoring import PriorityResult, inspection_priority

BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = BASE_DIR / "uploads"
SAMPLE_DIR = BASE_DIR / "sample_data"
UPLOAD_DIR.mkdir(exist_ok=True)

MAX_UPLOAD_BYTES = int(os.getenv("NIRIKSHAN_MAX_UPLOAD_MB", "12")) * 1024 * 1024
ALIGNMENT_THRESHOLD = float(os.getenv("NIRIKSHAN_ALIGNMENT_THRESHOLD", "0.45"))


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="NIRIKSHAN", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")


class BuildingCreate(BaseModel):
    name: str = Field(min_length=3, max_length=160)
    address: str = Field(min_length=8, max_length=300)
    ward: str = Field(default="", max_length=80)
    age_years: int | None = Field(default=None, ge=0, le=250)
    previous_category: str = Field(default="", max_length=120)
    notes: str = Field(default="", max_length=2000)
    pincode: str = Field(default="", max_length=6, pattern=r"^$|^\d{6}$")
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    structure_type: str = Field(default="Unknown", max_length=80)
    floor_count: int | None = Field(default=None, ge=1, le=200)
    primary_material: str = Field(default="Unknown", max_length=80)
    repair_history: str = Field(default="Unknown", max_length=80)
    previous_engineer_assessment: str = Field(default="No verified record", max_length=120)
    verified_strength_data: str = Field(default="", max_length=2000)


class ReviewUpdate(BaseModel):
    status: str = "Pending"
    note: str = Field(default="", max_length=2000)


def _auth_disabled() -> bool:
    return os.getenv("NIRIKSHAN_AUTH_DISABLED", "0").lower() in {"1", "true", "yes"}


def _check_basic_auth(request: Request) -> bool:
    if _auth_disabled():
        return True
    expected_user = os.getenv("NIRIKSHAN_AUTH_USERNAME", "admin")
    expected_password = os.getenv("NIRIKSHAN_AUTH_PASSWORD", "nirikshan-demo")
    header = request.headers.get("Authorization", "")
    if not header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:], validate=True).decode("utf-8")
        username, password = decoded.split(":", 1)
    except (ValueError, UnicodeDecodeError):
        return False
    return secrets.compare_digest(username, expected_user) and secrets.compare_digest(password, expected_password)


@app.middleware("http")
async def basic_auth_middleware(request: Request, call_next):
    # Static assets and health status carry no building evidence. The UI, API and uploads are
    # protected so a browser authenticates before any real project data can be accessed.
    if request.url.path.startswith("/static/") or request.url.path == "/health":
        return await call_next(request)
    if not _check_basic_auth(request):
        return JSONResponse(
            status_code=401,
            content={"detail": "Authentication required"},
            headers={"WWW-Authenticate": 'Basic realm="NIRIKSHAN"'},
        )
    return await call_next(request)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(request, "index.html")


AREA_TYPES = {
    "Building exterior / facade", "Balcony", "Column", "Beam",
    "Slab / Chajja", "Structural wall", "Partition / plaster wall",
    "Staircase exterior", "Other",
}

STRUCTURE_TYPES = {"RCC framed structure", "Load-bearing masonry", "Steel structure", "Composite / mixed", "Unknown"}
PRIMARY_MATERIALS = {"Reinforced concrete", "Brick masonry", "Stone masonry", "Steel", "Mixed / other", "Unknown"}
REPAIR_HISTORY_VALUES = {"None known", "Minor repairs", "Major structural repairs", "Unknown"}
ENGINEER_ASSESSMENT_VALUES = {
    "No verified record", "Routine / no action recorded", "Monitoring recommended",
    "Repairs recommended", "Urgent inspection previously recommended",
}


def _clean_capture_point(value: str) -> str:
    value = value.strip()
    if not value:
        raise HTTPException(400, "Inspection area is required")
    if len(value) > 160:
        raise HTTPException(400, "Inspection area is too long")
    return value


def _inspection_area_label(area_type: str, floor_level: str = "", area_note: str = "") -> str:
    area_type = re.sub(r"\s+", " ", area_type.strip())
    floor_level = re.sub(r"\s+", " ", floor_level.strip())
    area_note = re.sub(r"\s+", " ", area_note.strip())
    if area_type not in AREA_TYPES:
        raise HTTPException(400, "Choose a valid inspection area")
    if area_type == "Other" and len(area_note) < 3:
        raise HTTPException(400, "Describe the inspection area when choosing Other")
    if len(floor_level) > 40 or len(area_note) > 90:
        raise HTTPException(400, "Inspection-area details are too long")
    parts = [area_type]
    if floor_level and floor_level != "Not applicable":
        parts.append(floor_level)
    if area_note:
        parts.append(area_note)
    return " · ".join(parts)


def _inspection_element_type(capture_point: str) -> str:
    """Return the user-selected building element stored at the start of the area label."""
    element = (capture_point or "").split(" · ", 1)[0].strip()
    return element if element in AREA_TYPES else "Other"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ensure_not_duplicate(building_id: int, capture_point: str, path: Path) -> str:
    image_hash = _file_sha256(path)
    duplicate = db.row(
        "SELECT id FROM captures WHERE building_id=? AND capture_point=? AND image_hash=? ORDER BY id DESC LIMIT 1",
        (building_id, capture_point, image_hash),
    )
    if duplicate:
        path.unlink(missing_ok=True)
        raise HTTPException(409, f"This exact photo was already recorded for this inspection area as Capture #{duplicate['id']}")
    return image_hash


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _public_upload_path(path: str | Path) -> str:
    p = Path(path).resolve()
    root = UPLOAD_DIR.resolve()
    try:
        rel = p.relative_to(root)
    except ValueError as exc:
        raise ValueError("Evidence path is outside the upload directory") from exc
    return "/uploads/" + rel.as_posix()


def _save_upload(upload: UploadFile, prefix: str) -> Path:
    suffix = Path(upload.filename or "image.jpg").suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise HTTPException(415, "Only JPG, PNG and WebP images are supported")
    if upload.content_type and not upload.content_type.startswith("image/"):
        raise HTTPException(415, "Uploaded file must be an image")

    path = UPLOAD_DIR / f"{prefix}_{uuid.uuid4().hex[:10]}{suffix}"
    total = 0
    try:
        with path.open("wb") as handle:
            while True:
                chunk = upload.file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, f"Image exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB upload limit")
                handle.write(chunk)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def _serialize_capture(capture: dict) -> dict:
    item = dict(capture)
    item["quality"] = json.loads(item["quality_json"])
    item["image_url"] = _public_upload_path(item["image_path"])
    return item


def _serialize_building(building: dict) -> dict:
    item = dict(building)
    latest = db.rows(
        """SELECT c.* FROM captures c
           JOIN (
             SELECT capture_point, MAX(id) max_id
             FROM captures
             WHERE building_id=? AND status='accepted'
             GROUP BY capture_point
           ) x ON c.id=x.max_id
           ORDER BY c.capture_point""",
        (item["id"],),
    )
    item["latest_captures"] = [_serialize_capture(c) for c in latest]
    item["monitored_area_count"] = len(item["latest_captures"])
    item["location_available"] = item.get("latitude") is not None and item.get("longitude") is not None
    item["structural_strength_status"] = "Verified data recorded" if (item.get("verified_strength_data") or "").strip() else "Not assessed"

    # Building-level priority is driven by the latest reliable result from each monitored area,
    # not by whichever inspection happened most recently. One photographed wall therefore does
    # not silently stand in for the entire building.
    latest_area_analyses = db.rows(
        """SELECT a.* FROM analyses a
           JOIN (
             SELECT capture_point, MAX(id) AS max_id
             FROM analyses
             WHERE building_id=? AND is_reliable=1 AND archived=0
             GROUP BY capture_point
           ) x ON a.id=x.max_id
           ORDER BY a.score DESC, a.id DESC""",
        (item["id"],),
    )
    driver = latest_area_analyses[0] if latest_area_analyses else None
    item["overall_score"] = driver["score"] if driver else None
    item["overall_band"] = driver["band"] if driver else "No reliable analysis"
    item["priority_driver"] = driver["capture_point"] if driver else None
    # Backwards-compatible aliases used by the existing UI.
    item["latest_score"] = item["overall_score"]
    item["latest_band"] = item["overall_band"]
    return item


def _baseline(building_id: int, capture_point: str) -> dict | None:
    return db.row(
        """SELECT * FROM captures
           WHERE building_id=? AND capture_point=? AND is_baseline=1 AND status='accepted'
           ORDER BY id ASC LIMIT 1""",
        (building_id, capture_point),
    )


def _latest_accepted(building_id: int, capture_point: str) -> dict | None:
    return db.row(
        """SELECT * FROM captures
           WHERE building_id=? AND capture_point=? AND status='accepted'
           ORDER BY id DESC LIMIT 1""",
        (building_id, capture_point),
    )


def _recent_trend(building_id: int, capture_point: str, current_change: float) -> float | None:
    """Calculate a positive trend only when current + two prior reliable observations exist."""
    recent = db.rows(
        """SELECT metrics_json FROM analyses
           WHERE building_id=? AND capture_point=? AND is_reliable=1 AND archived=0
           ORDER BY id DESC LIMIT 2""",
        (building_id, capture_point),
    )
    previous_values: list[float] = []
    for record in reversed(recent):
        metrics = json.loads(record["metrics_json"])
        previous_values.append(float(metrics.get("priority_crack_change", metrics.get("crack_change", {}).get("normalized_change", 0.0))))
    values = previous_values + [float(current_change)]
    if len(values) < 3:
        return None
    slope = (values[-1] - values[0]) / max(len(values) - 1, 1)
    return max(0.0, min(1.0, slope * 2.0))


def _result_summary(result: dict) -> dict:
    return {
        "alignment_confidence": result["alignment_confidence"],
        "alignment_method": result["alignment_method"],
        "segmentation_stable": result.get("segmentation_stable", True),
        "crack_reference": result["crack_reference"],
        "crack_current": result["crack_current"],
        "crack_new": result.get("crack_new", {}),
        "crack_change": result["crack_change"],
        "spall_reference": result["spall_reference"],
        "spall_current": result["spall_current"],
        "spall_new": result.get("spall_new", {}),
        "spall_change": result["spall_change"],
    }


def _save_result_images(result: dict, prefix: str = "analysis") -> Path:
    analysis_dir = UPLOAD_DIR / f"{prefix}_{uuid.uuid4().hex[:12]}"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "aligned": analysis_dir / "aligned_current.jpg",
        "overlay": analysis_dir / "change_overlay.jpg",
        "reference_crack_mask": analysis_dir / "previous_crack_mask.png",
        "current_crack_mask": analysis_dir / "current_crack_mask.png",
        "new_crack_mask": analysis_dir / "new_crack_mask.png",
        "reference_spall_mask": analysis_dir / "previous_surface_mask.png",
        "current_spall_mask": analysis_dir / "current_surface_mask.png",
        "new_spall_mask": analysis_dir / "new_surface_mask.png",
    }
    for key, out_path in paths.items():
        cv2.imwrite(str(out_path), result[key])
    return analysis_dir


def _analyse_saved_capture(building_id: int, capture_point: str, path: Path, quality, *, demo: bool = False) -> dict:
    baseline = _baseline(building_id, capture_point)
    previous = _latest_accepted(building_id, capture_point)
    if not baseline or not previous:
        raise HTTPException(409, "No pinned baseline exists for this capture point. Upload a baseline first.")

    current_img = read_image(path)
    previous_result = analyze_pair(read_image(previous["image_path"]), current_img)
    baseline_result = previous_result if previous["id"] == baseline["id"] else analyze_pair(read_image(baseline["image_path"]), current_img)

    previous_ok = previous_result["alignment_confidence"] >= ALIGNMENT_THRESHOLD and previous_result.get("segmentation_stable", True)
    baseline_ok = baseline_result["alignment_confidence"] >= ALIGNMENT_THRESHOLD and baseline_result.get("segmentation_stable", True)
    usable = [result for result, ok in ((previous_result, previous_ok), (baseline_result, baseline_ok)) if ok]
    reliable = bool(usable)

    building = db.row("SELECT * FROM buildings WHERE id=?", (building_id,)) or {}
    element_type = _inspection_element_type(capture_point)

    if reliable:
        # Use the reliable comparison with the strongest visible crack-change evidence to
        # derive the crack-region counts used by the evidence-accumulation score.
        priority_source = max(usable, key=lambda result: float(result["crack_change"]["normalized_change"]))
        priority_crack_change = float(priority_source["crack_change"]["normalized_change"])
        previous_regions = int(priority_source.get("crack_reference", {}).get("component_count", 0))
        current_regions = int(priority_source.get("crack_current", {}).get("component_count", 0))
        new_regions = int(priority_source.get("crack_new", {}).get("component_count", 0))
        trend = _recent_trend(building_id, capture_point, priority_crack_change)
        priority = inspection_priority(
            priority_crack_change,
            trend,
            new_crack_regions=new_regions,
            current_crack_regions=current_regions,
            previous_crack_regions=previous_regions,
            element_type=element_type,
            structure_type=building.get("structure_type") or "Unknown",
            age_years=building.get("age_years"),
            repair_history=building.get("repair_history") or "Unknown",
            previous_engineer_assessment=building.get("previous_engineer_assessment") or "No verified record",
        )
        capture_status = "accepted"
        reject_reason = None
    else:
        previous_regions = 0
        current_regions = 0
        new_regions = 0
        priority_crack_change = 0.0
        trend = None
        priority = PriorityResult(
            score=0.0,
            band="Recapture required",
            factors=[{
                "name": "No reliable comparison — capture quarantined",
                "normalized_value": max(previous_result["alignment_confidence"], baseline_result["alignment_confidence"]),
                "effective_weight": 0.0,
                "contribution": 0.0,
            }],
        )
        capture_status = "quarantined"
        reject_reason = "No reliable comparison: alignment was too weak or crack-candidate segmentation was unstable against both references"

    current_capture_id = db.execute(
        """INSERT INTO captures(building_id,capture_point,image_path,quality_json,status,is_baseline,reject_reason)
           VALUES(?,?,?,?,?,?,?)""",
        (building_id, capture_point, str(path), json.dumps(quality.__dict__), capture_status, 0, reject_reason),
    )

    # Prefer the previous-visit comparison for the evidence overlay, unless only baseline is reliable.
    primary_result = previous_result if previous_ok or not baseline_ok else baseline_result
    analysis_dir = _save_result_images(primary_result, "analysis_demo" if demo else "analysis")

    metrics = {
        "alignment_confidence": primary_result["alignment_confidence"],
        "alignment_method": primary_result["alignment_method"],
        "segmentation_stable": primary_result.get("segmentation_stable", True),
        "needs_recapture": not reliable,
        "capture_status": capture_status,
        "quality": quality.__dict__,
        "priority_crack_change": round(priority_crack_change, 4),
        "crack_reference": primary_result["crack_reference"],
        "crack_current": primary_result["crack_current"],
        "crack_new": primary_result.get("crack_new", {}),
        "crack_change": primary_result["crack_change"],
        "new_crack_regions": new_regions,
        "previous_crack_regions": previous_regions,
        "current_crack_regions": current_regions,
        "inspection_element": element_type,
        "spall_reference": primary_result["spall_reference"],
        "spall_current": primary_result["spall_current"],
        "spall_new": primary_result.get("spall_new", {}),
        "spall_change": primary_result["spall_change"],
        "previous_comparison": _result_summary(previous_result),
        "baseline_comparison": _result_summary(baseline_result),
        "trend_change": trend,
        "priority_summary": priority.summary,
        "structural_context": {
            "structure_type": building.get("structure_type") or "Unknown",
            "floor_count": building.get("floor_count"),
            "age_years": building.get("age_years"),
            "primary_material": building.get("primary_material") or "Unknown",
            "repair_history": building.get("repair_history") or "Unknown",
            "previous_engineer_assessment": building.get("previous_engineer_assessment") or "No verified record",
            "structural_strength": "Verified data recorded" if (building.get("verified_strength_data") or "").strip() else "Not assessed",
            "verified_strength_data": (building.get("verified_strength_data") or "").strip(),
        },
        "scoring_note": "Inspection priority combines visible progression (55%), damage pattern (30%) and structural context (15%). One isolated crack-like region cannot create a High or Urgent result by itself. Experimental surface/spalling candidates remain excluded from scoring.",
        "note": "Synthetic demo pair; workflow evidence only." if demo else "Classical-CV crack candidates are an MVP baseline and not validated structural diagnosis.",
    }

    analysis_id = db.execute(
        """INSERT INTO analyses(
             building_id,capture_point,previous_capture_id,current_capture_id,baseline_capture_id,
             score,band,metrics_json,factors_json,result_dir,is_reliable
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (
            building_id,
            capture_point,
            previous["id"],
            current_capture_id,
            baseline["id"],
            priority.score,
            priority.band,
            json.dumps(metrics),
            json.dumps(priority.factors),
            str(analysis_dir),
            1 if reliable else 0,
        ),
    )
    return analysis_json(db.row("SELECT * FROM analyses WHERE id=?", (analysis_id,)))


@app.get("/api/summary")
def summary():
    buildings = db.rows("SELECT * FROM buildings ORDER BY id DESC")
    reliable = db.rows("SELECT * FROM analyses WHERE is_reliable=1 AND archived=0 ORDER BY score DESC, id DESC")
    recaptures = db.rows("SELECT id FROM analyses WHERE is_reliable=0 AND archived=0")
    return {
        "building_count": len(buildings),
        "analysis_count": len(reliable) + len(recaptures),
        "recapture_count": len(recaptures),
        "urgent_count": sum(1 for item in reliable if item["band"] == "Urgent human review"),
        "high_count": sum(1 for item in reliable if item["band"] == "High"),
        "top_queue": [analysis_json(item) for item in reliable[:12]],
    }


@app.get("/api/buildings")
def list_buildings():
    return [_serialize_building(b) for b in db.rows("SELECT * FROM buildings ORDER BY id DESC")]


@app.get("/api/buildings/nearby")
def nearby_buildings(lat: float, lon: float, radius_m: float = 80.0):
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise HTTPException(400, "Invalid coordinates")
    radius_m = max(10.0, min(float(radius_m), 500.0))
    matches = []
    for building in db.rows("SELECT * FROM buildings WHERE latitude IS NOT NULL AND longitude IS NOT NULL"):
        distance = _distance_m(lat, lon, float(building["latitude"]), float(building["longitude"]))
        if distance <= radius_m:
            item = _serialize_building(building)
            item["distance_m"] = round(distance, 1)
            matches.append(item)
    matches.sort(key=lambda item: item["distance_m"])
    return matches[:8]


@app.post("/api/buildings")
def create_building(payload: BuildingCreate):
    name = re.sub(r"\s+", " ", payload.name.strip())
    address = re.sub(r"\s+", " ", payload.address.strip())
    if len(name) < 3:
        raise HTTPException(400, "Enter an identifiable building or society name (at least 3 characters)")
    if len(address) < 8:
        raise HTTPException(400, "Enter a complete enough address or locality to identify the building")
    if not re.search(r"[A-Za-z0-9]{3,}", name):
        raise HTTPException(400, "Building name must contain an identifiable word or number")
    if payload.pincode and not re.fullmatch(r"\d{6}", payload.pincode):
        raise HTTPException(400, "PIN code must contain exactly 6 digits")

    # Warn against accidental duplicate registration when a location is available.
    if payload.latitude is not None and payload.longitude is not None:
        for existing in db.rows("SELECT * FROM buildings WHERE latitude IS NOT NULL AND longitude IS NOT NULL"):
            distance = _distance_m(payload.latitude, payload.longitude, float(existing["latitude"]), float(existing["longitude"]))
            same_name = existing["name"].strip().casefold() == name.casefold()
            if distance <= 20 and same_name:
                raise HTTPException(409, f"{existing['name']} is already registered at this location")

    if payload.structure_type not in STRUCTURE_TYPES:
        raise HTTPException(400, "Choose a valid structure type")
    if payload.primary_material not in PRIMARY_MATERIALS:
        raise HTTPException(400, "Choose a valid primary material")
    if payload.repair_history not in REPAIR_HISTORY_VALUES:
        raise HTTPException(400, "Choose a valid repair-history option")
    if payload.previous_engineer_assessment not in ENGINEER_ASSESSMENT_VALUES:
        raise HTTPException(400, "Choose a valid previous engineer assessment")

    building_id = db.execute(
        """INSERT INTO buildings(
             name,address,ward,age_years,previous_category,notes,pincode,latitude,longitude,
             structure_type,floor_count,primary_material,repair_history,previous_engineer_assessment,verified_strength_data
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            name, address, payload.ward.strip(), payload.age_years, payload.previous_category.strip(),
            payload.notes.strip(), payload.pincode.strip(), payload.latitude, payload.longitude,
            payload.structure_type, payload.floor_count, payload.primary_material, payload.repair_history,
            payload.previous_engineer_assessment, payload.verified_strength_data.strip(),
        ),
    )
    return _serialize_building(db.row("SELECT * FROM buildings WHERE id=?", (building_id,)))


@app.get("/api/buildings/{building_id}")
def building_detail(building_id: int):
    building = db.row("SELECT * FROM buildings WHERE id=?", (building_id,))
    if not building:
        raise HTTPException(404, "Building not found")
    result = _serialize_building(building)
    analyses = db.rows("SELECT * FROM analyses WHERE building_id=? AND archived=0 ORDER BY id DESC", (building_id,))
    result["analyses"] = [analysis_json(item) for item in analyses]
    return result


@app.post("/api/buildings/{building_id}/baseline")
def upload_baseline(building_id: int, capture_point: str = Form(...), image: UploadFile = File(...)):
    if not db.row("SELECT id FROM buildings WHERE id=?", (building_id,)):
        raise HTTPException(404, "Building not found")
    capture_point = _clean_capture_point(capture_point)
    if _baseline(building_id, capture_point):
        raise HTTPException(409, "A pinned baseline already exists for this capture point")

    path = _save_upload(image, f"b{building_id}_baseline")
    try:
        img = read_image(path)
        quality = assess_quality(img)
    except ValueError as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(400, str(exc)) from exc
    if quality.severe:
        path.unlink(missing_ok=True)
        raise HTTPException(422, {"message": "Capture quality is too low", "quality": quality.__dict__})

    image_hash = _ensure_not_duplicate(building_id, capture_point, path)
    capture_id = db.execute(
        """INSERT INTO captures(building_id,capture_point,image_path,quality_json,status,is_baseline,image_hash)
           VALUES(?,?,?,?,?,?,?)""",
        (building_id, capture_point, str(path), json.dumps(quality.__dict__), "accepted", 1, image_hash),
    )
    return {"capture_id": capture_id, "image_url": _public_upload_path(path), "quality": quality.__dict__, "pinned_baseline": True}


@app.post("/api/buildings/{building_id}/inspect")
def inspect(building_id: int, capture_point: str = Form(...), image: UploadFile = File(...)):
    if not db.row("SELECT id FROM buildings WHERE id=?", (building_id,)):
        raise HTTPException(404, "Building not found")
    capture_point = _clean_capture_point(capture_point)
    if not _baseline(building_id, capture_point):
        raise HTTPException(409, "No pinned baseline exists for this capture point. Upload a baseline first.")

    path = _save_upload(image, f"b{building_id}_visit")
    try:
        current_img = read_image(path)
        quality = assess_quality(current_img)
    except ValueError as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(400, str(exc)) from exc
    if quality.severe:
        path.unlink(missing_ok=True)
        raise HTTPException(422, {"message": "Capture quality is too low", "quality": quality.__dict__})

    image_hash = _ensure_not_duplicate(building_id, capture_point, path)
    result = _analyse_saved_capture(building_id, capture_point, path, quality)
    db.update("UPDATE captures SET image_hash=? WHERE id=?", (image_hash, result["current_capture_id"]))
    return result


@app.post("/api/buildings/{building_id}/record-inspection")
def record_inspection(
    building_id: int,
    image: UploadFile = File(...),
    existing_area: str = Form(""),
    area_type: str = Form(""),
    floor_level: str = Form(""),
    area_note: str = Form(""),
):
    building = db.row("SELECT id FROM buildings WHERE id=?", (building_id,))
    if not building:
        raise HTTPException(404, "Building not found")

    capture_point = _clean_capture_point(existing_area) if existing_area.strip() else _inspection_area_label(area_type, floor_level, area_note)
    path = _save_upload(image, f"b{building_id}_inspection")
    try:
        current_img = read_image(path)
        quality = assess_quality(current_img)
    except ValueError as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(400, str(exc)) from exc
    if quality.severe:
        path.unlink(missing_ok=True)
        raise HTTPException(422, {"message": "Photo quality is too low. Retake a clearer image.", "quality": quality.__dict__})

    image_hash = _ensure_not_duplicate(building_id, capture_point, path)
    baseline = _baseline(building_id, capture_point)
    if not baseline:
        capture_id = db.execute(
            """INSERT INTO captures(building_id,capture_point,image_path,quality_json,status,is_baseline,image_hash)
               VALUES(?,?,?,?,?,?,?)""",
            (building_id, capture_point, str(path), json.dumps(quality.__dict__), "accepted", 1, image_hash),
        )
        return {
            "mode": "first_capture",
            "capture_id": capture_id,
            "inspection_area": capture_point,
            "image_url": _public_upload_path(path),
            "quality": quality.__dict__,
            "message": "First inspection photo recorded. Future photos of this area will be compared with it automatically.",
        }

    result = _analyse_saved_capture(building_id, capture_point, path, quality)
    db.update("UPDATE captures SET image_hash=? WHERE id=?", (image_hash, result["current_capture_id"]))
    return {"mode": "comparison", "analysis": result}


def analysis_json(analysis: dict) -> dict:
    item = dict(analysis)
    item["metrics"] = json.loads(item.pop("metrics_json"))
    item["factors"] = json.loads(item.pop("factors_json"))
    item["is_reliable"] = bool(item.get("is_reliable", 1))
    item["building"] = db.row("SELECT * FROM buildings WHERE id=?", (item["building_id"],))
    current = db.row("SELECT image_path,status FROM captures WHERE id=?", (item["current_capture_id"],))
    previous = db.row("SELECT image_path FROM captures WHERE id=?", (item["previous_capture_id"],))
    baseline = db.row("SELECT image_path FROM captures WHERE id=?", (item.get("baseline_capture_id"),)) if item.get("baseline_capture_id") else None
    item["previous_image_url"] = _public_upload_path(previous["image_path"]) if previous else None
    item["current_image_url"] = _public_upload_path(current["image_path"]) if current else None
    item["baseline_image_url"] = _public_upload_path(baseline["image_path"]) if baseline else None
    item["capture_status"] = current["status"] if current else "unknown"
    item["inspection_area"] = item.get("capture_point", "")
    result_dir = Path(item["result_dir"])
    item["result_urls"] = {
        "aligned": _public_upload_path(result_dir / "aligned_current.jpg"),
        "overlay": _public_upload_path(result_dir / "change_overlay.jpg"),
        "previous_crack_mask": _public_upload_path(result_dir / "previous_crack_mask.png"),
        "current_crack_mask": _public_upload_path(result_dir / "current_crack_mask.png"),
        "new_crack_mask": _public_upload_path(result_dir / "new_crack_mask.png"),
        "new_surface_mask": _public_upload_path(result_dir / "new_surface_mask.png"),
    }
    return item


@app.get("/api/analyses/{analysis_id}")
def get_analysis(analysis_id: int):
    analysis = db.row("SELECT * FROM analyses WHERE id=?", (analysis_id,))
    if not analysis:
        raise HTTPException(404, "Analysis not found")
    return analysis_json(analysis)




@app.delete("/api/analyses/{analysis_id}")
def delete_analysis(analysis_id: int):
    analysis = db.row("SELECT id, archived FROM analyses WHERE id=?", (analysis_id,))
    if not analysis:
        raise HTTPException(404, "Analysis not found")
    if analysis.get("archived"):
        return {"id": analysis_id, "message": "Inspection already removed from queue"}
    # Archive instead of destroying evidence. This removes it from active queues and counts
    # while preserving the audit trail and capture chain.
    db.update("UPDATE analyses SET archived=1 WHERE id=?", (analysis_id,))
    return {"id": analysis_id, "message": "Inspection removed from active queue"}

@app.post("/api/analyses/{analysis_id}/review")
def review_analysis(analysis_id: int, payload: ReviewUpdate):
    allowed = {"Confirmed", "False alarm", "Needs site visit", "Pending"}
    if payload.status not in allowed:
        raise HTTPException(400, "Invalid review status")
    db.update(
        "UPDATE analyses SET review_status=?, review_note=? WHERE id=?",
        (payload.status, payload.note.strip(), analysis_id),
    )
    return get_analysis(analysis_id)


@app.post("/api/demo/seed")
def seed_demo():
    existing = db.row("SELECT * FROM buildings WHERE name='Raghunath House — Demo'")
    if existing:
        latest = db.row("SELECT id FROM analyses WHERE building_id=? ORDER BY id DESC LIMIT 1", (existing["id"],))
        return {"building_id": existing["id"], "analysis_id": latest["id"] if latest else None, "message": "Demo already exists"}

    building_id = db.execute(
        """INSERT INTO buildings(
             name,address,ward,age_years,previous_category,notes,pincode,latitude,longitude,
             structure_type,floor_count,primary_material,repair_history,previous_engineer_assessment,verified_strength_data
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "Raghunath House — Demo",
            "Girgaon, Mumbai (synthetic demo record)",
            "D Ward",
            78,
            "Demo only",
            "Synthetic images included with the prototype. Not a real safety assessment.",
            "400004",
            None,
            None,
            "Load-bearing masonry",
            4,
            "Brick masonry",
            "Unknown",
            "No verified record",
            "",
        ),
    )
    capture_point = "Structural wall · 3rd floor · Synthetic demo area"
    baseline_src = SAMPLE_DIR / "demo_baseline.jpg"
    follow_src = SAMPLE_DIR / "demo_followup.jpg"
    baseline_dst = UPLOAD_DIR / f"demo_b{building_id}_baseline.jpg"
    follow_dst = UPLOAD_DIR / f"demo_b{building_id}_followup.jpg"
    shutil.copy2(baseline_src, baseline_dst)
    baseline_quality = assess_quality(read_image(baseline_dst))
    db.execute(
        """INSERT INTO captures(building_id,capture_point,image_path,quality_json,status,is_baseline,image_hash)
           VALUES(?,?,?,?,?,?,?)""",
        (building_id, capture_point, str(baseline_dst), json.dumps(baseline_quality.__dict__), "accepted", 1, _file_sha256(baseline_dst)),
    )
    shutil.copy2(follow_src, follow_dst)
    follow_quality = assess_quality(read_image(follow_dst))
    result = _analyse_saved_capture(building_id, capture_point, follow_dst, follow_quality, demo=True)
    return {"building_id": building_id, "analysis_id": result["id"], "message": "Synthetic demo loaded"}
