from __future__ import annotations

from datetime import datetime
from io import BytesIO
from pathlib import Path

from PIL import Image

from ai_vision import FishDiseaseAI, severity_for_result


class FakeStore:
    def __init__(self) -> None:
        self.records = []

    def record_ai_diagnosis(self, record, alarm_confidence=0.75):
        self.records.append(record)
        return {"ok": True, "id": len(self.records), "alarm_created": False}


def make_png_bytes() -> bytes:
    image = Image.new("RGB", (8, 8), color=(28, 132, 110))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_udp_image_is_saved_and_marked_pending_when_model_missing(tmp_path):
    store = FakeStore()
    ai = FishDiseaseAI(
        store=store,
        upload_root=str(tmp_path / "uploads"),
        class_model_path=str(tmp_path / "missing-cls.pt"),
        detect_model_path=str(tmp_path / "missing-det.pt"),
    )

    result = ai.ingest_udp_image(
        make_png_bytes(),
        datetime(2026, 5, 22, 9, 30, 0),
        {"image_id": "img001", "pond_id": 1, "filename": "fish.png"},
    )

    assert result["ok"] is True
    assert len(store.records) == 1
    record = store.records[0]
    assert record["pond_id"] == 1
    assert record["disease_name"] == "待模型识别"
    assert record["confidence"] == 0.0
    assert record["status"] == "pending"
    assert record["source"] == "udp_hardware"
    assert Path(record["image_url"]).exists()


def test_disease_severity_uses_confidence_thresholds():
    assert severity_for_result("healthy", 0.99) == "low"
    assert severity_for_result("bacterial", 0.74) == "low"
    assert severity_for_result("bacterial", 0.76) == "medium"
    assert severity_for_result("bacterial", 0.91) == "high"
    assert severity_for_result("bacterial", 0.98) == "critical"
