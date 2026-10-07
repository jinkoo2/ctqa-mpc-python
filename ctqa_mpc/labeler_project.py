"""vtk_image_labeler_3d project JSON for an MPC CT (no paint masks)."""

from __future__ import annotations

import csv
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .app_settings import load_settings
from .image_io import find_image

logger = logging.getLogger(__name__)

PROJECT_JSON_NAME = "vtk_image_labeler_3d.project.json"
PROJECT_KIND = "vtk_image_labeler_3d_project"
CSV_SKIP_SUBSTR = ("copy",)
CSV_ORDER = (
    "comparison_points.csv",
    "comparison_distances.csv",
    "angles.csv",
)


def project_json_path(folder: str | Path) -> Path:
    return Path(folder) / PROJECT_JSON_NAME


def _rel(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def write_project_json(folder: str | Path, *, image_dir: str | Path | None = None) -> Path:
    root = Path(folder)
    root.mkdir(parents=True, exist_ok=True)
    image_root = Path(image_dir) if image_dir else root
    ct = find_image(image_root, "CT")
    if ct is None:
        raise FileNotFoundError(f"no CT image in {image_root}")
    payload = {
        "kind": PROJECT_KIND,
        "image": _rel(ct, root),
        "window_settings": {"level": 40, "width": 400},
        "segmentations": [],
    }
    dest = project_json_path(root)
    dest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    logger.info("wrote %s", dest)
    return dest


def write_baseline_project(baseline_dir: str | Path, machine: dict) -> Path:
    image_dir = baseline_dir
    if find_image(baseline_dir, "CT") is None:
        out = Path(baseline_dir) / "out"
        if find_image(out, "CT") is not None:
            image_dir = out
    return write_project_json(baseline_dir, image_dir=image_dir)


def write_case_project(case_dir: str | Path, machine: dict, *, include_labels: bool | None = None) -> Path:
    return write_project_json(case_dir)


def csv_paths(folder: str | Path) -> list[Path]:
    folder = Path(folder)
    if not folder.is_dir():
        return []
    files = [
        p
        for p in folder.glob("*.csv")
        if p.is_file() and not any(s in p.name.lower() for s in CSV_SKIP_SUBSTR)
    ]
    rank = {name.lower(): i for i, name in enumerate(CSV_ORDER)}
    files.sort(key=lambda p: (rank.get(p.name.lower(), 1000), p.name.lower()))
    return files


def read_csv_table(path: str | Path) -> list[list[str]]:
    text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    rows = list(csv.reader(text.splitlines()))
    return [[cell.strip() for cell in row] for row in rows if any(cell.strip() for cell in row)]


def labeler_command(project_json: str | Path, data: dict | None = None) -> list[str]:
    settings = data if data is not None else load_settings()
    viewer = settings.get("Viewer") if isinstance(settings.get("Viewer"), dict) else {}
    configured = str(
        (viewer or {}).get("vtk_image_labeler_3d")
        or settings.get("vtk_image_labeler_3d")
        or os.environ.get("VTK_IMAGE_LABELER_3D")
        or ""
    ).strip()
    json_path = str(Path(project_json).resolve())
    if configured:
        exe = Path(configured)
        if exe.suffix.lower() == ".py":
            return [sys.executable, str(exe), "--project", json_path]
        return [str(exe), "--project", json_path]
    found = shutil.which("vtk-image-labeler-3d") or shutil.which("ImageLabeler3D")
    if found:
        return [found, "--project", json_path]
    app_py = _bundled_labeler_app()
    if app_py is not None:
        return [sys.executable, str(app_py), "--project", json_path]
    return [sys.executable, "-m", "vtk_image_labeler_3d", "--project", json_path]


def _bundled_labeler_app() -> Path | None:
    here = Path(__file__).resolve()
    candidates = [
        here.parents[3] / "_ref_projects" / "vtk_image_labeler_3d" / "src" / "vtk_image_labeler_3d" / "app.py",
        here.parents[2] / "_ref_projects" / "vtk_image_labeler_3d" / "src" / "vtk_image_labeler_3d" / "app.py",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def launch_labeler(project_json: str | Path, data: dict | None = None) -> subprocess.Popen:
    cmd = labeler_command(project_json, data)
    logger.info("launch Image Labeler 3D: %s", subprocess.list2cmdline(cmd))
    return subprocess.Popen(cmd)
