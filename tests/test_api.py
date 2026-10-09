from pathlib import Path

import cv2
import numpy as np
from fastapi.testclient import TestClient

from app import db, main

ROOT = Path(__file__).resolve().parents[1]
AUTH = ("admin", "nirikshan-demo")


def _configure_tmp(monkeypatch, tmp_path):
    monkeypatch.delenv("NIRIKSHAN_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("NIRIKSHAN_AUTH_USERNAME", AUTH[0])
    monkeypatch.setenv("NIRIKSHAN_AUTH_PASSWORD", AUTH[1])
    db.DB_PATH = tmp_path / "data" / "test.db"
    main.UPLOAD_DIR = tmp_path / "uploads"
    main.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def test_api_requires_basic_auth(monkeypatch, tmp_path):
    _configure_tmp(monkeypatch, tmp_path)
    with TestClient(main.app) as client:
        assert client.get("/api/summary").status_code == 401
        assert client.get("/uploads/not-found.jpg").status_code == 401
        assert client.get("/api/summary", auth=AUTH).status_code == 200


def test_building_age_is_validated(monkeypatch, tmp_path):
    _configure_tmp(monkeypatch, tmp_path)
    with TestClient(main.app) as client:
        response = client.post(
            "/api/buildings",
            auth=AUTH,
            json={"name": "Example", "address": "Mumbai", "age_years": "abc"},
        )
        assert response.status_code == 422


def test_low_confidence_capture_is_quarantined_and_not_next_reference(monkeypatch, tmp_path):
    _configure_tmp(monkeypatch, tmp_path)
    with TestClient(main.app) as client:
        building = client.post(
            "/api/buildings",
            auth=AUTH,
            json={"name": "Control Building", "address": "Girgaon, Mumbai", "age_years": 50},
        ).json()

        baseline_path = ROOT / "sample_data" / "demo_baseline.jpg"
        baseline_bytes = baseline_path.read_bytes()
        baseline_response = client.post(
            f"/api/buildings/{building['id']}/baseline",
            auth=AUTH,
            data={"capture_point": "North facade"},
            files={"image": ("baseline.jpg", baseline_bytes, "image/jpeg")},
        )
        assert baseline_response.status_code == 200
        baseline_id = baseline_response.json()["capture_id"]

        rng = np.random.default_rng(3)
        unrelated = rng.integers(40, 220, size=(900, 1200, 3), dtype=np.uint8)
        ok, encoded = cv2.imencode(".jpg", unrelated, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
        assert ok
        inspection = client.post(
            f"/api/buildings/{building['id']}/inspect",
            auth=AUTH,
            data={"capture_point": "North facade"},
            files={"image": ("unrelated.jpg", encoded.tobytes(), "image/jpeg")},
        )
        assert inspection.status_code == 200
        payload = inspection.json()
        assert payload["is_reliable"] is False
        assert payload["band"] == "Recapture required"
        assert payload["capture_status"] == "quarantined"

        detail = client.get(f"/api/buildings/{building['id']}", auth=AUTH).json()
        assert detail["latest_captures"][0]["id"] == baseline_id


def test_authenticated_home_renders(monkeypatch, tmp_path):
    _configure_tmp(monkeypatch, tmp_path)
    with TestClient(main.app) as client:
        response = client.get("/", auth=AUTH)
        assert response.status_code == 200
        assert "NIRIKSHAN" in response.text



def test_delete_analysis_archives_it_from_queue(monkeypatch, tmp_path):
    _configure_tmp(monkeypatch, tmp_path)
    with TestClient(main.app) as client:
        building_id = db.execute(
            "INSERT INTO buildings(name,address,ward) VALUES(?,?,?)",
            ("Queue Test", "Girgaon, Mumbai", "D Ward"),
        )
        dummy = main.UPLOAD_DIR / "dummy.jpg"
        dummy.write_bytes(b"x")
        baseline_id = db.execute(
            """INSERT INTO captures(building_id,capture_point,image_path,quality_json,status,is_baseline)
               VALUES(?,?,?,?,?,?)""",
            (building_id, "North facade", str(dummy), '{}', 'accepted', 1),
        )
        current_id = db.execute(
            """INSERT INTO captures(building_id,capture_point,image_path,quality_json,status,is_baseline)
               VALUES(?,?,?,?,?,?)""",
            (building_id, "North facade", str(dummy), '{}', 'accepted', 0),
        )
        result_dir = main.UPLOAD_DIR / 'analysis_test'
        result_dir.mkdir()
        for name in ['aligned_current.jpg','change_overlay.jpg','previous_crack_mask.png','current_crack_mask.png','new_crack_mask.png','new_surface_mask.png']:
            (result_dir / name).write_bytes(b'x')
        analysis_id = db.execute(
            """INSERT INTO analyses(
                 building_id,capture_point,previous_capture_id,current_capture_id,baseline_capture_id,
                 score,band,metrics_json,factors_json,result_dir,is_reliable
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (building_id, "North facade", baseline_id, current_id, baseline_id, 70.0, "High", '{"alignment_confidence":0.9}', '[]', str(result_dir), 1),
        )
        before = client.get('/api/summary', auth=AUTH).json()
        assert any(item['id'] == analysis_id for item in before['top_queue'])

        response = client.delete(f'/api/analyses/{analysis_id}', auth=AUTH)
        assert response.status_code == 200

        after = client.get('/api/summary', auth=AUTH).json()
        assert not any(item['id'] == analysis_id for item in after['top_queue'])
        assert db.row('SELECT archived FROM analyses WHERE id=?', (analysis_id,))['archived'] == 1


def test_building_requires_identifiable_name_and_address(monkeypatch, tmp_path):
    _configure_tmp(monkeypatch, tmp_path)
    with TestClient(main.app) as client:
        too_short = client.post(
            "/api/buildings", auth=AUTH,
            json={"name": "A", "address": "B"},
        )
        assert too_short.status_code == 422

        bad_pin = client.post(
            "/api/buildings", auth=AUTH,
            json={"name": "Shanti Sadan", "address": "Girgaon, Mumbai", "pincode": "4000"},
        )
        assert bad_pin.status_code == 422


def test_nearby_building_lookup(monkeypatch, tmp_path):
    _configure_tmp(monkeypatch, tmp_path)
    with TestClient(main.app) as client:
        created = client.post(
            "/api/buildings", auth=AUTH,
            json={
                "name": "Location Test Building",
                "address": "Girgaon, Mumbai",
                "latitude": 18.9532,
                "longitude": 72.8132,
                "pincode": "400004",
            },
        )
        assert created.status_code == 200
        response = client.get(
            "/api/buildings/nearby?lat=18.95321&lon=72.81321&radius_m=80", auth=AUTH
        )
        assert response.status_code == 200
        data = response.json()
        assert data
        assert data[0]["name"] == "Location Test Building"
        assert data[0]["distance_m"] < 5


def test_unified_record_inspection_creates_first_capture(monkeypatch, tmp_path):
    _configure_tmp(monkeypatch, tmp_path)
    with TestClient(main.app) as client:
        building = client.post(
            "/api/buildings", auth=AUTH,
            json={"name": "Inspection Demo", "address": "Girgaon, Mumbai"},
        ).json()
        image_bytes = (ROOT / "sample_data" / "demo_baseline.jpg").read_bytes()
        response = client.post(
            f"/api/buildings/{building['id']}/record-inspection",
            auth=AUTH,
            data={"area_type": "Balcony", "floor_level": "3rd floor", "area_note": "Near flat 302"},
            files={"image": ("first.jpg", image_bytes, "image/jpeg")},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["mode"] == "first_capture"
        assert payload["inspection_area"] == "Balcony · 3rd floor · Near flat 302"
        capture = db.row("SELECT * FROM captures WHERE id=?", (payload["capture_id"],))
        assert capture["is_baseline"] == 1
        assert capture["image_hash"]


def test_duplicate_photo_is_rejected(monkeypatch, tmp_path):
    _configure_tmp(monkeypatch, tmp_path)
    with TestClient(main.app) as client:
        building = client.post(
            "/api/buildings", auth=AUTH,
            json={"name": "Duplicate Test", "address": "Girgaon, Mumbai"},
        ).json()
        image_bytes = (ROOT / "sample_data" / "demo_baseline.jpg").read_bytes()
        first = client.post(
            f"/api/buildings/{building['id']}/record-inspection", auth=AUTH,
            data={"area_type": "Partition / plaster wall", "floor_level": "Ground floor", "area_note": "Near entrance"},
            files={"image": ("same.jpg", image_bytes, "image/jpeg")},
        )
        assert first.status_code == 200
        second = client.post(
            f"/api/buildings/{building['id']}/record-inspection", auth=AUTH,
            data={"existing_area": "Partition / plaster wall · Ground floor · Near entrance"},
            files={"image": ("same.jpg", image_bytes, "image/jpeg")},
        )
        assert second.status_code == 409
        assert "already recorded" in second.json()["detail"]


def test_building_structural_context_is_stored_without_strength_inference(monkeypatch, tmp_path):
    _configure_tmp(monkeypatch, tmp_path)
    with TestClient(main.app) as client:
        response = client.post(
            "/api/buildings", auth=AUTH,
            json={
                "name": "Structural Context Test",
                "address": "Girgaon, Mumbai",
                "structure_type": "RCC framed structure",
                "floor_count": 7,
                "age_years": 45,
                "primary_material": "Reinforced concrete",
                "repair_history": "Minor repairs",
                "previous_engineer_assessment": "Monitoring recommended",
                "verified_strength_data": "",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["structure_type"] == "RCC framed structure"
        assert data["floor_count"] == 7
        assert data["structural_strength_status"] == "Not assessed"


def test_invalid_structure_type_is_rejected(monkeypatch, tmp_path):
    _configure_tmp(monkeypatch, tmp_path)
    with TestClient(main.app) as client:
        response = client.post(
            "/api/buildings", auth=AUTH,
            json={
                "name": "Bad Context Building",
                "address": "Girgaon, Mumbai",
                "structure_type": "Magic strong building",
            },
        )
        assert response.status_code == 400
