from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image


HEALTHY_NAMES = {"healthy", "health", "normal", "健康", "healthy_fish", "fish_healthy"}
DISEASE_LABELS = {
    "healthy": "健康",
    "bacterial": "细菌性疾病",
    "fungal": "真菌性疾病",
    "parasitic": "寄生虫病",
    "viral": "病毒性疾病",
    "gill_or_skin_disease": "鳃部或体表疾病",
}


def safe_filename(name: str | None, fallback: str) -> str:
    candidate = Path(name or fallback).name
    candidate = re.sub(r"[^A-Za-z0-9_.-]+", "_", candidate).strip("._")
    return candidate or fallback


def public_static_url(path: Path) -> str:
    parts = path.as_posix().split("/static/", 1)
    if len(parts) == 2:
        return f"/static/{parts[1]}"
    return path.as_posix()


def normalize_label(label: str) -> str:
    raw = str(label or "").strip()
    key = raw.lower().replace(" ", "_").replace("-", "_")
    return DISEASE_LABELS.get(key, raw or "待模型识别")


def is_healthy_label(label: str) -> bool:
    key = str(label or "").strip().lower().replace(" ", "_").replace("-", "_")
    return key in HEALTHY_NAMES or "healthy" in key or label == "健康"


def severity_for_result(label: str, confidence: float) -> str:
    if is_healthy_label(label) or confidence < 0.5:
        return "low"
    if confidence >= 0.97:
        return "critical"
    if confidence >= 0.9:
        return "high"
    if confidence >= 0.75:
        return "medium"
    return "low"


class FishDiseaseAI:
    def __init__(
        self,
        store: Any,
        upload_root: str | None = None,
        class_model_path: str | None = None,
        detect_model_path: str | None = None,
    ):
        self.store = store
        self.upload_root = Path(upload_root or os.getenv("AI_UPLOAD_ROOT", "static/uploads/ai")).resolve()
        self.class_model_path = Path(class_model_path or os.getenv("AI_CLASS_MODEL_PATH", "models/fish_disease_cls.pt"))
        self.detect_model_path = Path(detect_model_path or os.getenv("AI_DETECT_MODEL_PATH", "models/fish_disease_det.pt"))
        self.alarm_confidence = float(os.getenv("AI_ALARM_CONFIDENCE", "0.75"))
        self.imgsz = int(os.getenv("AI_MODEL_IMG_SIZE", "640"))
        self._class_model = None
        self._detect_model = None

    def status(self) -> dict[str, Any]:
        return {
            "ultralytics_available": self._ultralytics_available(),
            "class_model_path": str(self.class_model_path),
            "class_model_exists": self.class_model_path.exists(),
            "detect_model_path": str(self.detect_model_path),
            "detect_model_exists": self.detect_model_path.exists(),
            "alarm_confidence": self.alarm_confidence,
            "upload_root": str(self.upload_root),
        }

    def ingest_udp_image(self, image_bytes: bytes, recorded_at: datetime, meta: dict[str, Any]) -> dict[str, Any]:
        original_path, image_url = self._save_original_image(image_bytes, meta, recorded_at)
        result = self.analyze_image(original_path, recorded_at, meta)
        result["image_url"] = image_url
        result["pond_id"] = int(meta["pond_id"])
        result["source"] = "udp_hardware"
        return self.store.record_ai_diagnosis(result, alarm_confidence=self.alarm_confidence)

    def analyze_image(self, image_path: Path, recorded_at: datetime, meta: dict[str, Any]) -> dict[str, Any]:
        raw_result: dict[str, Any] = {"image_id": meta.get("image_id"), "received_at": recorded_at.isoformat()}
        class_result = self._predict_classification(image_path)
        annotated_image_url = None
        detection = self._predict_detection(image_path, recorded_at, meta)
        if detection:
            annotated_image_url = detection["annotated_image_url"]
            raw_result["detections"] = detection["detections"]

        if not class_result:
            raw_result["status"] = "classification_model_missing"
            return {
                "disease_name": "待模型识别",
                "confidence": 0.0,
                "severity": "low",
                "status": "pending",
                "model_name": "YOLO模型未配置",
                "annotated_image_url": annotated_image_url,
                "raw_result": raw_result,
            }

        label = class_result["label"]
        confidence = float(class_result["confidence"])
        raw_result["classification"] = class_result
        return {
            "disease_name": normalize_label(label),
            "confidence": confidence,
            "severity": severity_for_result(label, confidence),
            "status": "archived" if is_healthy_label(label) else "pending",
            "model_name": class_result["model_name"],
            "annotated_image_url": annotated_image_url,
            "raw_result": raw_result,
        }

    def _save_original_image(self, image_bytes: bytes, meta: dict[str, Any], recorded_at: datetime) -> tuple[Path, str]:
        image = Image.open(BytesIO(image_bytes))
        image.verify()
        image = Image.open(BytesIO(image_bytes))
        image_format = (image.format or "JPEG").lower()
        if image_format == "jpeg":
            suffix = ".jpg"
        elif image_format in {"png", "webp"}:
            suffix = f".{image_format}"
        else:
            raise ValueError(f"不支持的图片格式: {image_format}")

        day_dir = self.upload_root / recorded_at.strftime("%Y%m%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        base_name = safe_filename(meta.get("filename"), f"{meta.get('image_id') or uuid.uuid4().hex}{suffix}")
        if not Path(base_name).suffix:
            base_name = f"{base_name}{suffix}"
        final_name = f"{Path(base_name).stem}-{uuid.uuid4().hex[:8]}{Path(base_name).suffix.lower()}"
        target = day_dir / final_name
        image.save(target)
        return target, public_static_url(target)

    def _predict_classification(self, image_path: Path) -> dict[str, Any] | None:
        if not self.class_model_path.exists() or not self._ultralytics_available():
            return None
        model = self._load_model("class")
        results = model.predict(str(image_path), imgsz=self.imgsz, verbose=False)
        if not results:
            return None
        result = results[0]
        names = getattr(model, "names", {}) or getattr(result, "names", {})
        if getattr(result, "probs", None) is not None:
            top1 = int(result.probs.top1)
            confidence = float(result.probs.top1conf)
            return {"label": str(names.get(top1, top1)), "confidence": confidence, "model_name": self.class_model_path.name}
        boxes = getattr(result, "boxes", None)
        if boxes is not None and len(boxes) > 0:
            best = max(boxes, key=lambda box: float(box.conf[0]))
            class_id = int(best.cls[0])
            return {"label": str(names.get(class_id, class_id)), "confidence": float(best.conf[0]), "model_name": self.class_model_path.name}
        return None

    def _predict_detection(self, image_path: Path, recorded_at: datetime, meta: dict[str, Any]) -> dict[str, Any] | None:
        if not self.detect_model_path.exists() or not self._ultralytics_available():
            return None
        model = self._load_model("detect")
        results = model.predict(str(image_path), imgsz=self.imgsz, verbose=False)
        if not results:
            return None
        result = results[0]
        detections = []
        names = getattr(model, "names", {}) or getattr(result, "names", {})
        boxes = getattr(result, "boxes", None)
        if boxes is not None:
            for box in boxes:
                class_id = int(box.cls[0])
                detections.append(
                    {
                        "label": str(names.get(class_id, class_id)),
                        "confidence": float(box.conf[0]),
                        "xyxy": [float(value) for value in box.xyxy[0].tolist()],
                    }
                )
        annotated = result.plot()
        target_dir = self.upload_root / recorded_at.strftime("%Y%m%d") / "annotated"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{meta.get('image_id') or uuid.uuid4().hex}-det.jpg"
        Image.fromarray(annotated).save(target)
        return {"annotated_image_url": public_static_url(target), "detections": detections}

    def _load_model(self, kind: str):
        from ultralytics import YOLO

        if kind == "class":
            if self._class_model is None:
                self._class_model = YOLO(str(self.class_model_path))
            return self._class_model
        if self._detect_model is None:
            self._detect_model = YOLO(str(self.detect_model_path))
        return self._detect_model

    def _ultralytics_available(self) -> bool:
        try:
            import ultralytics  # noqa: F401
        except Exception:
            return False
        return True


def raw_result_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
