"""DICOM series sort, info.txt, and compressed CT.mha conversion."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import numpy as np
import pydicom
import SimpleITK as sitk

from .image_io import find_image, write_mha
from .param import Param

logger = logging.getLogger(__name__)


def _safe(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def parse_dicom_person_name(name: str) -> tuple[str, str]:
    """Return ``(last, first)`` from DICOM ``Last^First`` or ``Last, First``."""
    text = str(name or "").strip()
    if "^" in text:
        last, first = text.split("^", 1)
    elif "," in text:
        last, first = text.split(",", 1)
    else:
        last, first = text, ""
    return last.strip(), first.strip()


def operator_token(name: str) -> str:
    last, first = parse_dicom_person_name(name)
    raw = first or last
    return "".join(ch for ch in raw if ch.isalnum())


def write_info_txt(ds, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    fields = {
        "PatientName": _safe(getattr(ds, "PatientName", "")),
        "PatientID": _safe(getattr(ds, "PatientID", "")),
        "StationName": _safe(getattr(ds, "StationName", "")),
        "StudyDate": _safe(getattr(ds, "StudyDate", "")),
        "StudyTime": _safe(getattr(ds, "StudyTime", "") or getattr(ds, "SeriesTime", "")),
        "SeriesDate": _safe(getattr(ds, "SeriesDate", "") or getattr(ds, "StudyDate", "")),
        "SeriesTime": _safe(getattr(ds, "SeriesTime", "") or getattr(ds, "StudyTime", "")),
        "SeriesNumber": _safe(getattr(ds, "SeriesNumber", "")),
        "StudyInstanceUID": _safe(getattr(ds, "StudyInstanceUID", "")),
        "SeriesInstanceUID": _safe(getattr(ds, "SeriesInstanceUID", "")),
        "Modality": _safe(getattr(ds, "Modality", "")),
    }
    lines = [f"{k}={v}" for k, v in fields.items()]
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return dest


def iter_dicom_files(folder: Path) -> list[Path]:
    files: list[Path] = []
    for path in folder.iterdir():
        if not path.is_file():
            continue
        if path.suffix.lower() in (".dcm", "") or path.name.upper().startswith("CT."):
            files.append(path)
    return files


def read_dicom_slices(folder: Path) -> list:
    slices = []
    for path in folder.iterdir():
        if not path.is_file():
            continue
        try:
            ds = pydicom.dcmread(str(path), stop_before_pixels=False, force=True)
        except Exception:
            continue
        if not hasattr(ds, "ImagePositionPatient"):
            continue
        slices.append(ds)
    slices.sort(key=lambda s: float(s.ImagePositionPatient[2]))
    return slices


def sort_files_by_patient_study_series(
    dir_in: str | Path,
    dir_out: str | Path,
    *,
    delete_source_files: bool = False,
) -> Path:
    src = Path(dir_in)
    dest_root = Path(dir_out)
    dest_root.mkdir(parents=True, exist_ok=True)
    copied = 0
    for path in src.iterdir():
        if not path.is_file():
            continue
        try:
            ds = pydicom.dcmread(str(path), stop_before_pixels=True, force=True)
        except Exception as exc:
            logger.debug("skip %s: %s", path, exc)
            continue
        if not hasattr(ds, "SOPClassUID") and not hasattr(ds, "Modality"):
            continue
        patient = _safe(getattr(ds, "PatientID", "Unknown")) or "Unknown"
        study = _safe(getattr(ds, "StudyInstanceUID", "Unknown")) or "Unknown"
        series = _safe(getattr(ds, "SeriesInstanceUID", "Unknown")) or "Unknown"
        target_dir = dest_root / patient / study / series
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / path.name
        shutil.copy2(path, target)
        copied += 1
        if delete_source_files:
            try:
                path.unlink()
            except OSError:
                pass
    logger.info("sorted %s DICOM files into %s", copied, dest_root)
    return dest_root


def series_dirs(sort_root: Path) -> list[Path]:
    found: list[Path] = []
    if not sort_root.is_dir():
        return found
    for patient in sort_root.iterdir():
        if not patient.is_dir():
            continue
        for study in patient.iterdir():
            if not study.is_dir():
                continue
            for series in study.iterdir():
                if series.is_dir():
                    found.append(series)
    return found


def dicom_series_to_mha(dir_in: str | Path, dir_out: str | Path | None = None) -> Path:
    src = Path(dir_in)
    dest = Path(dir_out) if dir_out else src
    dest.mkdir(parents=True, exist_ok=True)
    existing = find_image(dest, "CT")
    if existing and existing.suffix.lower() == ".mha":
        logger.info("CT.mha already present: %s", existing)
        return existing

    slices = read_dicom_slices(src)
    if not slices:
        raise FileNotFoundError(f"no CT DICOM slices in {src}")
    logger.info("converting %s DICOM slices in %s", len(slices), src)

    volume = np.stack([s.pixel_array for s in slices]).astype(np.float32)
    slope = float(getattr(slices[0], "RescaleSlope", 1.0) or 1.0)
    intercept = float(getattr(slices[0], "RescaleIntercept", 0.0) or 0.0)
    volume = volume * slope + intercept
    volume = np.round(volume).astype(np.int16)

    if len(slices) > 1:
        dz = abs(
            float(slices[1].ImagePositionPatient[2])
            - float(slices[0].ImagePositionPatient[2])
        )
    else:
        dz = float(getattr(slices[0], "SliceThickness", 1.0) or 1.0)
    if getattr(slices[0], "PixelSpacing", None):
        dy, dx = map(float, slices[0].PixelSpacing)
    else:
        dx = dy = 1.0
    origin = list(map(float, slices[0].ImagePositionPatient))
    if getattr(slices[0], "ImageOrientationPatient", None):
        iop = [float(v) for v in slices[0].ImageOrientationPatient]
        row = np.array(iop[:3])
        col = np.array(iop[3:])
        slice_dir = np.cross(row, col)
        direction = tuple(np.vstack([row, col, slice_dir]).flatten())
    else:
        direction = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)

    image = sitk.GetImageFromArray(volume)
    image.SetSpacing((dx, dy, dz))
    image.SetOrigin(tuple(origin))
    image.SetDirection(direction)

    out = write_mha(image, dest / "CT.mha")
    write_info_txt(slices[0], dest / "info.txt")
    logger.info("wrote %s", out)
    return out


def case_stamp_from_info(info_file: Path, *, include_operator: bool = True) -> str:
    info = Param(info_file)
    series_date = info.get_value("SeriesDate") or info.get_value("StudyDate")
    study_time = info.get_value("StudyTime") or info.get_value("SeriesTime")
    study_time = "".join(ch for ch in study_time if ch.isdigit())[:6].ljust(6, "0")
    series_date = "".join(ch for ch in series_date if ch.isdigit())[:8]
    if not series_date:
        raise ValueError(f"SeriesDate missing in {info_file}")
    stamp = f"{series_date}_{study_time}"
    if include_operator:
        op = operator_token(info.get_value("PatientName"))
        if op:
            stamp = f"{stamp}_{op}"
    return stamp


def ensure_ct_mha(case_dir: str | Path) -> Path:
    folder = Path(case_dir)
    found = find_image(folder, "CT")
    if found and found.suffix.lower() == ".mha":
        return found
    if found:
        image = sitk.ReadImage(str(found))
        return write_mha(image, folder / "CT.mha")
    return dicom_series_to_mha(folder, folder)
