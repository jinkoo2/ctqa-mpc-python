"""Compressed MetaImage I/O. Prefer ``.mha`` over ``.nrrd`` / ``.mhd``."""

from __future__ import annotations

import tempfile
from pathlib import Path

import SimpleITK as sitk

IMAGE_EXTS = (".mha", ".nrrd", ".mhd")


def write_mha(image: sitk.Image, path: str | Path) -> Path:
    out = Path(path)
    if out.suffix.lower() != ".mha":
        out = out.with_suffix(".mha")
    out.parent.mkdir(parents=True, exist_ok=True)
    writer = sitk.ImageFileWriter()
    writer.SetFileName(str(out))
    writer.SetUseCompression(True)
    writer.Execute(image)
    return out


def read_image(path: str | Path) -> sitk.Image:
    return sitk.ReadImage(str(path))


def find_image(folder: str | Path, stem: str) -> Path | None:
    folder = Path(folder)
    for ext in IMAGE_EXTS:
        candidate = folder / f"{stem}{ext}"
        if candidate.is_file():
            return candidate
    return None


def scratch_parent() -> Path:
    """Local temp root for DICOM sort / intermediate work (not a UNC path)."""
    return Path(tempfile.gettempdir())


def require_image(folder: str | Path, stem: str) -> Path:
    found = find_image(folder, stem)
    if found is None:
        raise FileNotFoundError(f"image not found: {folder}/{stem}.mha|.nrrd|.mhd")
    return found
