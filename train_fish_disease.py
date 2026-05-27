from __future__ import annotations

import argparse
import os
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


CLASS_NAMES = ["healthy", "bacterial", "fungal", "parasitic", "viral", "gill_or_skin_disease"]
DEFAULT_CLASS_DATASET = "Saon110/bd-fish-disease-dataset"
FALLBACK_CLASS_DATASETS = ["panda992/fish_disease_datasets"]

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def normalize_class_name(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if any(key in raw for key in ["healthy", "health", "normal", "good"]):
        return "healthy"
    if any(key in raw for key in ["bacterial", "bacteria", "bacter"]):
        return "bacterial"
    if any(key in raw for key in ["fungal", "fungus", "fungi", "saprolegnia"]):
        return "fungal"
    if any(key in raw for key in ["parasitic", "parasite", "protozoa", "ich"]):
        return "parasitic"
    if any(key in raw for key in ["viral", "virus", "koi_herpes"]):
        return "viral"
    return "gill_or_skin_disease"


def require_package(name: str, install_hint: str | None = None) -> Any:
    try:
        return __import__(name)
    except Exception as exc:
        hint = install_hint or f"python -m pip install {name}"
        raise RuntimeError(f"缺少依赖 {name}，请先执行：{hint}") from exc


def resolve_device(device: str | int) -> str | int:
    if str(device) != "auto":
        return device
    try:
        import torch
    except Exception:
        return "cpu"
    return 0 if torch.cuda.is_available() else "cpu"


def find_image_and_label_columns(dataset) -> tuple[str, str]:
    image_column = None
    label_column = None
    features = getattr(dataset, "features", {})
    for name, feature in features.items():
        class_name = feature.__class__.__name__.lower()
        if image_column is None and "image" in class_name:
            image_column = name
        if label_column is None and "classlabel" in class_name:
            label_column = name
    if image_column is None:
        for name in dataset.column_names:
            if "image" in name.lower() or "img" in name.lower():
                image_column = name
                break
    if label_column is None:
        for name in dataset.column_names:
            if name.lower() in {"label", "labels", "class", "category", "disease", "target"}:
                label_column = name
                break
    if not image_column or not label_column:
        raise RuntimeError(f"无法识别数据集列，当前列：{dataset.column_names}")
    return image_column, label_column


def label_to_name(dataset, label_column: str, value: Any) -> str:
    feature = getattr(dataset, "features", {}).get(label_column)
    if feature and hasattr(feature, "int2str"):
        try:
            return str(feature.int2str(int(value)))
        except Exception:
            pass
    return str(value)


def save_dataset_split(dataset, split_name: str, output_root: Path, max_images: int | None = None) -> int:
    image_column, label_column = find_image_and_label_columns(dataset)
    saved = 0
    for item in dataset:
        if max_images and saved >= max_images:
            break
        image = item[image_column]
        label_name = normalize_class_name(label_to_name(dataset, label_column, item[label_column]))
        target_dir = output_root / split_name / label_name
        target_dir.mkdir(parents=True, exist_ok=True)
        suffix = ".jpg"
        target = target_dir / f"{uuid.uuid4().hex}{suffix}"
        if hasattr(image, "convert"):
            image.convert("RGB").save(target, quality=92)
        else:
            source = Path(str(image))
            if not source.exists():
                continue
            shutil.copy2(source, target)
        saved += 1
    return saved


def prepare_classification(args) -> None:
    datasets = require_package("datasets", "python -m pip install datasets pillow")
    output_root = Path(args.output).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    dataset_names = [args.dataset]
    if args.dataset == DEFAULT_CLASS_DATASET:
        dataset_names.extend(FALLBACK_CLASS_DATASETS)
    errors = []
    ds = None
    used_dataset = None
    for dataset_name in dataset_names:
        try:
            ds = datasets.load_dataset(dataset_name)
            used_dataset = dataset_name
            break
        except Exception as exc:
            errors.append(f"{dataset_name}: {exc}")
    if ds is None:
        raise RuntimeError("分类数据集下载失败：" + " | ".join(errors))
    train = ds["train"] if "train" in ds else next(iter(ds.values()))
    val = ds["validation"] if "validation" in ds else ds["test"] if "test" in ds else None
    if val is None:
        split = train.train_test_split(test_size=0.2, seed=42)
        train = split["train"]
        val = split["test"]
    train_count = save_dataset_split(train, "train", output_root, args.max_images)
    val_limit = max(1, int(args.max_images * 0.2)) if args.max_images else None
    val_count = save_dataset_split(val, "val", output_root, val_limit)
    custom_root = Path(args.custom_output).resolve()
    for class_name in CLASS_NAMES:
        (custom_root / "train" / class_name).mkdir(parents=True, exist_ok=True)
        (custom_root / "val" / class_name).mkdir(parents=True, exist_ok=True)
    print(f"分类数据集已整理：{output_root}，来源={used_dataset}，train={train_count}，val={val_count}")
    print(f"增量数据目录已准备：{custom_root}")


def prepare_detection(args) -> None:
    api_key = args.roboflow_api_key or os.getenv("ROBOFLOW_API_KEY")
    if not api_key:
        raise RuntimeError("缺少 ROBOFLOW_API_KEY，无法下载 Roboflow 检测数据集。可先只训练分类模型。")
    roboflow = require_package("roboflow", "python -m pip install roboflow")
    rf = roboflow.Roboflow(api_key=api_key)
    project = rf.workspace(args.roboflow_workspace).project(args.roboflow_project)
    dataset = project.version(int(args.roboflow_version)).download("yolov8", location=str(Path(args.output).resolve()))
    print(f"检测数据集已下载：{dataset.location}")


def copy_best_weight(run_dir: Path, target: Path) -> Path:
    candidates = sorted(run_dir.glob("**/weights/best.pt"), key=lambda item: item.stat().st_mtime, reverse=True)
    if not candidates:
        search_root = Path("runs")
        candidates = sorted(search_root.glob("**/weights/best.pt"), key=lambda item: item.stat().st_mtime, reverse=True)
    if not candidates:
        raise RuntimeError(f"训练结束但未找到 best.pt：{run_dir}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(candidates[0], target)
    return target


def train_classification(args) -> None:
    require_package("ultralytics", "python -m pip install ultralytics")
    from ultralytics import YOLO

    data_dir = Path(args.data).resolve()
    if not (data_dir / "train").exists() or not (data_dir / "val").exists():
        raise RuntimeError(f"分类数据目录不完整：{data_dir}，需要 train/ 和 val/")
    run_root = Path(args.project).resolve()
    model = YOLO(args.base_model)
    device = resolve_device(args.device)
    model.train(data=str(data_dir), imgsz=args.imgsz, epochs=args.epochs, batch=args.batch, device=device, project=str(run_root), name=args.name)
    target = copy_best_weight(run_root / args.name, Path(args.output).resolve())
    print(f"分类模型已输出：{target}")


def train_detection(args) -> None:
    require_package("ultralytics", "python -m pip install ultralytics")
    from ultralytics import YOLO

    data_yaml = Path(args.data).resolve()
    if not data_yaml.exists():
        raise RuntimeError(f"检测数据配置不存在：{data_yaml}")
    run_root = Path(args.project).resolve()
    model = YOLO(args.base_model)
    device = resolve_device(args.device)
    model.train(data=str(data_yaml), imgsz=args.imgsz, epochs=args.epochs, batch=args.batch, device=device, project=str(run_root), name=args.name)
    target = copy_best_weight(run_root / args.name, Path(args.output).resolve())
    print(f"检测模型已输出：{target}")


def doctor(args) -> None:
    print("依赖状态：")
    for package in ["PIL", "datasets", "roboflow", "ultralytics", "torch"]:
        try:
            __import__(package)
            print(f"  {package}: OK")
        except Exception:
            print(f"  {package}: 缺失")
    print("GPU 状态：")
    try:
        import torch

        print(f"  CUDA: {'可用' if torch.cuda.is_available() else '不可用'}")
        if torch.cuda.is_available():
            print(f"  GPU: {torch.cuda.get_device_name(0)}")
    except Exception as exc:
        print(f"  CUDA: 无法检查，{exc}")
    print("路径状态：")
    for path in [args.class_data, args.det_data, args.class_model, args.det_model]:
        current = Path(path)
        print(f"  {current}: {'存在' if current.exists() else '缺失'}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SFweb 鱼病 YOLO 数据集准备与训练工具")
    sub = parser.add_subparsers(dest="command", required=True)

    common_train = argparse.ArgumentParser(add_help=False)
    common_train.add_argument("--imgsz", type=int, default=640)
    common_train.add_argument("--epochs", type=int, default=50)
    common_train.add_argument("--batch", type=int, default=8)
    common_train.add_argument("--device", default="auto")
    common_train.add_argument("--project", default="runs/fish_disease")

    prepare_cls = sub.add_parser("prepare-cls", help="下载并整理 Hugging Face 分类数据集")
    prepare_cls.add_argument("--dataset", default=DEFAULT_CLASS_DATASET)
    prepare_cls.add_argument("--output", default="datasets/fish_disease_cls")
    prepare_cls.add_argument("--custom-output", default="datasets/fish_disease_custom")
    prepare_cls.add_argument("--max-images", type=int, default=None)
    prepare_cls.set_defaults(func=prepare_classification)

    prepare_det = sub.add_parser("prepare-det", help="下载 Roboflow YOLO 检测数据集")
    prepare_det.add_argument("--output", default="datasets/fish_diseases_det")
    prepare_det.add_argument("--roboflow-api-key", default=None)
    prepare_det.add_argument("--roboflow-workspace", default=os.getenv("ROBOFLOW_WORKSPACE", "fish-diseases"))
    prepare_det.add_argument("--roboflow-project", default=os.getenv("ROBOFLOW_PROJECT", "fish-diseases"))
    prepare_det.add_argument("--roboflow-version", default=os.getenv("ROBOFLOW_VERSION", "1"))
    prepare_det.set_defaults(func=prepare_detection)

    train_cls = sub.add_parser("train-cls", parents=[common_train], help="训练 YOLO 分类模型")
    train_cls.add_argument("--data", default="datasets/fish_disease_cls")
    train_cls.add_argument("--base-model", default="yolov8n-cls.pt")
    train_cls.add_argument("--name", default="classification")
    train_cls.add_argument("--output", default=os.getenv("AI_CLASS_MODEL_PATH", "models/fish_disease_cls.pt"))
    train_cls.set_defaults(func=train_classification)

    train_det = sub.add_parser("train-det", parents=[common_train], help="训练 YOLO 检测模型")
    train_det.add_argument("--data", default="datasets/fish_diseases_det/data.yaml")
    train_det.add_argument("--base-model", default="yolov8n.pt")
    train_det.add_argument("--name", default="detection")
    train_det.add_argument("--output", default=os.getenv("AI_DETECT_MODEL_PATH", "models/fish_disease_det.pt"))
    train_det.set_defaults(func=train_detection)

    all_cmd = sub.add_parser("all", parents=[common_train], help="准备分类数据并训练分类模型；有 Roboflow key 时再训练检测模型")
    all_cmd.add_argument("--max-images", type=int, default=None)
    all_cmd.set_defaults(func=run_all)

    check = sub.add_parser("doctor", help="检查依赖、GPU、数据集和模型路径")
    check.add_argument("--class-data", default="datasets/fish_disease_cls")
    check.add_argument("--det-data", default="datasets/fish_diseases_det/data.yaml")
    check.add_argument("--class-model", default=os.getenv("AI_CLASS_MODEL_PATH", "models/fish_disease_cls.pt"))
    check.add_argument("--det-model", default=os.getenv("AI_DETECT_MODEL_PATH", "models/fish_disease_det.pt"))
    check.set_defaults(func=doctor)
    return parser


def run_all(args) -> None:
    prepare_classification(argparse.Namespace(dataset=DEFAULT_CLASS_DATASET, output="datasets/fish_disease_cls", custom_output="datasets/fish_disease_custom", max_images=args.max_images))
    train_classification(
        argparse.Namespace(
            data="datasets/fish_disease_cls",
            base_model="yolov8n-cls.pt",
            name="classification",
            output=os.getenv("AI_CLASS_MODEL_PATH", "models/fish_disease_cls.pt"),
            imgsz=args.imgsz,
            epochs=args.epochs,
            batch=args.batch,
            device=args.device,
            project=args.project,
        )
    )
    if os.getenv("ROBOFLOW_API_KEY"):
        prepare_detection(
            argparse.Namespace(
                output="datasets/fish_diseases_det",
                roboflow_api_key=None,
                roboflow_workspace=os.getenv("ROBOFLOW_WORKSPACE", "fish-diseases"),
                roboflow_project=os.getenv("ROBOFLOW_PROJECT", "fish-diseases"),
                roboflow_version=os.getenv("ROBOFLOW_VERSION", "1"),
            )
        )
        train_detection(
            argparse.Namespace(
                data="datasets/fish_diseases_det/data.yaml",
                base_model="yolov8n.pt",
                name="detection",
                output=os.getenv("AI_DETECT_MODEL_PATH", "models/fish_disease_det.pt"),
                imgsz=args.imgsz,
                epochs=args.epochs,
                batch=args.batch,
                device=args.device,
                project=args.project,
            )
        )
    else:
        print("未配置 ROBOFLOW_API_KEY，已跳过检测数据下载和检测模型训练。")


def main() -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except Exception as exc:
        print(f"执行失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
