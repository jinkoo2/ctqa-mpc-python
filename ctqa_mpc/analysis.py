"""MPC marker geometry: BB centers, pairwise distances, and axis angles."""

from __future__ import annotations

import json
import logging
import math
import re
from pathlib import Path

import numpy as np
import SimpleITK as sitk

from .image_io import write_mha
from .param import Param

logger = logging.getLogger(__name__)

RESULT_JSON_NAME = "analysis.result.json"
CASE_RESULT_NAME = "result.json"
_RESULT_HEAD_RE = re.compile(r'"result"\s*:\s*"(pass|fail)"', re.IGNORECASE)
CSV_SKIP_SUBSTR = ("copy",)


def analysis_dir(folder: str | Path) -> Path:
    """Prefer ``3.analysis``; fall back to C# ``out`` when that is all a case has."""
    folder = Path(folder)
    nested = folder / "3.analysis"
    legacy = folder / "out"
    if (nested / CASE_RESULT_NAME).is_file() or (nested / "report.html").is_file() or (nested / "points.txt").is_file():
        return nested
    if (legacy / "points.txt").is_file() or (legacy / "report.html").is_file() or (legacy / "comparison_points.csv").is_file():
        return legacy
    if nested.is_dir():
        return nested
    if legacy.is_dir():
        return legacy
    return nested


def analysis_result_path(folder: str | Path) -> Path:
    return Path(folder) / RESULT_JSON_NAME


def case_result_path(folder: str | Path) -> Path:
    folder = Path(folder)
    nested = folder / "3.analysis" / CASE_RESULT_NAME
    if nested.is_file():
        return nested
    legacy = folder / "out" / CASE_RESULT_NAME
    if legacy.is_file():
        return legacy
    return nested


def float_list(value) -> list[float]:
    if isinstance(value, (list, tuple)):
        return [float(x) for x in value if str(x).strip() != ""]
    text = str(value or "").replace(";", ",").replace("[", " ").replace("]", " ")
    return [float(p) for p in text.replace(",", " ").split() if p.strip()]


def machine_float(machine: dict | None, key: str, default: float = 0.0) -> float:
    try:
        return float((machine or {}).get(key) if (machine or {}).get(key) not in (None, "") else default)
    except (TypeError, ValueError):
        return default


def marker_count(machine: dict | None) -> int:
    try:
        return int((machine or {}).get("num_of_markers") or 0)
    except (TypeError, ValueError):
        return 0


def marker_names(machine: dict | None) -> list[str]:
    n = marker_count(machine)
    return [f"pt{i}" for i in range(n)]


def seed_points(machine: dict | None) -> list[tuple[str, tuple[float, float, float]]]:
    points: list[tuple[str, tuple[float, float, float]]] = []
    for name in marker_names(machine):
        vals = float_list((machine or {}).get(name))
        if len(vals) < 3:
            raise ValueError(f"machine {name} needs x,y,z")
        points.append((name, (vals[0], vals[1], vals[2])))
    return points


def named_string_list(machine: dict | None, key: str) -> list[str]:
    value = (machine or {}).get(key)
    if isinstance(value, (list, tuple)):
        return [str(x).strip() for x in value if str(x).strip()]
    return [p.strip() for p in str(value or "").replace(";", ",").split(",") if p.strip()]


def _threshold_upper(image: sitk.Image, th: float) -> float:
    """Upper bound that fits the pixel type. ``1e12`` overflows int16 CT.mha."""
    arr = sitk.GetArrayViewFromImage(image)
    if np.issubdtype(arr.dtype, np.integer):
        upper = float(np.iinfo(arr.dtype).max)
    else:
        upper = float(np.finfo(arr.dtype).max)
    lower = float(th)
    if lower > upper:
        raise ValueError(
            f"threshold th={lower} exceeds {image.GetPixelIDTypeAsString()} max {upper}"
        )
    return upper


def threshold_bb(image: sitk.Image, th: float) -> sitk.Image:
    """Voxels >= *th* become 255, else 0 (C# ``threshold_3d_f(..., 0, th, 255)``)."""
    lower = float(th)
    upper = _threshold_upper(image, lower)
    return sitk.BinaryThreshold(
        image,
        lowerThreshold=lower,
        upperThreshold=upper,
        insideValue=255,
        outsideValue=0,
    )


def _clip_index_size(image: sitk.Image, i0, i1) -> tuple[list[int], list[int]]:
    size = image.GetSize()
    start: list[int] = []
    dim: list[int] = []
    for d in range(3):
        a, b = int(i0[d]), int(i1[d])
        if a > b:
            a, b = b, a
        a = max(0, min(a, size[d] - 1))
        b = max(a + 1, min(b + 1, size[d]))
        start.append(a)
        dim.append(b - a)
    return start, dim


def extract_search_box(image: sitk.Image, center: tuple[float, float, float], box_mm: float) -> sitk.Image:
    half = float(box_mm) / 2.0
    low = tuple(c - half for c in center)
    high = tuple(c + half for c in center)
    i0 = image.TransformPhysicalPointToIndex(low)
    i1 = image.TransformPhysicalPointToIndex(high)
    start, dim = _clip_index_size(image, i0, i1)
    return sitk.RegionOfInterest(image, dim, start)


def center_of_gravity(image: sitk.Image) -> tuple[float, float, float]:
    arr = sitk.GetArrayFromImage(image).astype(np.float64)
    mass = float(np.sum(arr))
    if mass <= 0:
        return (math.nan, math.nan, math.nan)
    zz, yy, xx = np.meshgrid(
        np.arange(arr.shape[0], dtype=np.float64),
        np.arange(arr.shape[1], dtype=np.float64),
        np.arange(arr.shape[2], dtype=np.float64),
        indexing="ij",
    )
    cx = float(np.sum(xx * arr) / mass)
    cy = float(np.sum(yy * arr) / mass)
    cz = float(np.sum(zz * arr) / mass)
    return image.TransformContinuousIndexToPhysicalPoint((cx, cy, cz))


def find_markers(ct: sitk.Image, machine: dict, out_dir: Path | None = None) -> list[dict]:
    th = machine_float(machine, "th", 5000.0)
    box = machine_float(machine, "search_box_size_mm", 50.0)
    th_img = threshold_bb(ct, th)
    if out_dir is not None:
        write_mha(th_img, Path(out_dir) / "CT.th.mha")
    points: list[dict] = []
    for name, seed in seed_points(machine):
        roi = extract_search_box(th_img, seed, box)
        x, y, z = center_of_gravity(roi)
        if any(math.isnan(v) for v in (x, y, z)):
            raise RuntimeError(f"no marker signal in search box for {name} (th={th})")
        points.append({"id": name, "x": x, "y": y, "z": z})
        logger.info("%s COM=%.3f %.3f %.3f", name, x, y, z)
    return points


def _xyz(point: dict) -> np.ndarray:
    return np.array([float(point["x"]), float(point["y"]), float(point["z"])], dtype=np.float64)


def pairwise_distances(points: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for i, a in enumerate(points):
        for b in points[i + 1 :]:
            d = float(np.linalg.norm(_xyz(a) - _xyz(b)))
            rows.append({"id": f"dist_{a['id']}_{b['id']}", "value": d})
    return rows


def _unit(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    if n == 0:
        return v
    return v / n


def _point_map(points: list[dict]) -> dict[str, np.ndarray]:
    return {str(p["id"]): _xyz(p) for p in points}


def _plane_normal(pts: list[np.ndarray]) -> np.ndarray:
    return _unit(np.cross(pts[1] - pts[0], pts[2] - pts[0]))


def axes_and_angles(points: list[dict], machine: dict) -> dict:
    lookup = _point_map(points)
    z_vectors: list[np.ndarray] = []
    for group_name in named_string_list(machine, "z_axis_cross_product"):
        names = named_string_list(machine, group_name)
        if len(names) < 3:
            continue
        z_vectors.append(_plane_normal([lookup[n] for n in names[:3]]))
    if not z_vectors:
        raise RuntimeError("z_axis_cross_product is empty")
    uz = _unit(np.mean(np.stack(z_vectors), axis=0))

    y_vectors: list[np.ndarray] = []
    for group_name in named_string_list(machine, "y_axis_vectors"):
        names = named_string_list(machine, group_name)
        if len(names) < 2:
            continue
        y_vectors.append(_unit(lookup[names[1]] - lookup[names[0]]))
    if not y_vectors:
        raise RuntimeError("y_axis_vectors is empty")
    uy = _unit(np.mean(np.stack(y_vectors), axis=0))
    ux = _unit(np.cross(uy, uz))
    angles = [
        math.degrees(math.acos(max(-1.0, min(1.0, float(ux[0]))))),
        math.degrees(math.acos(max(-1.0, min(1.0, float(uy[1]))))),
        math.degrees(math.acos(max(-1.0, min(1.0, float(uz[2]))))),
    ]
    return {
        "ux": [float(v) for v in ux],
        "uy": [float(v) for v in uy],
        "uz": [float(v) for v in uz],
        "angles_to_axis": angles,
    }


def _fmt(value: float, digits: int = 1) -> str:
    return f"{value:.{digits}f}"


def _stat_rows(values: list[float], prefix_cols: int, *, min_label: str = "MIN") -> list[str]:
    arr = np.array(values, dtype=np.float64)
    mean = float(arr.mean()) if arr.size else 0.0
    std = float(np.sqrt(np.mean((arr - mean) ** 2))) if arr.size else 0.0
    pad = "," * prefix_cols
    return [
        f"{min_label}{pad}{_fmt(float(arr.min()) if arr.size else 0.0)}",
        f"MAX{pad}{_fmt(float(arr.max()) if arr.size else 0.0)}",
        f"MEAN{pad}{_fmt(mean)}",
        f"STD{pad}{_fmt(std)}",
    ]


def write_points_txt(path: Path, points: list[dict]) -> None:
    lines = [f"{p['id']}={p['x']},{p['y']},{p['z']}" for p in points]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_distances_txt(path: Path, distances: list[dict]) -> None:
    path.write_text("\n".join(f"{d['id']}={d['value']}" for d in distances) + "\n", encoding="utf-8")


def write_axes_txt(path: Path, axes: dict) -> None:
    lines = [
        f"ux={','.join(str(v) for v in axes['ux'])}",
        f"uy={','.join(str(v) for v in axes['uy'])}",
        f"uz={','.join(str(v) for v in axes['uz'])}",
        f"angles_to_axis={','.join(str(v) for v in axes['angles_to_axis'])}",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_points_txt(path: Path) -> list[dict]:
    info = Param(path)
    points: list[dict] = []
    for key, value in info.items().items():
        if not key.startswith("pt"):
            continue
        vals = float_list(value)
        if len(vals) < 3:
            continue
        points.append({"id": key, "x": vals[0], "y": vals[1], "z": vals[2]})
    points.sort(key=lambda p: int(re.sub(r"\D", "", p["id"]) or 0))
    return points


def write_comparison_points_csv(path: Path, case_pts: list[dict], ref_pts: list[dict], tol: float) -> list[dict]:
    ref = {p["id"]: p for p in ref_pts}
    rows = ["Name, x,y,z, x_ref, y_ref, z_ref, Distance[mm], Result"]
    items: list[dict] = []
    distances: list[float] = []
    for pt in case_pts:
        base = ref.get(pt["id"])
        if base is None:
            continue
        dist = float(np.linalg.norm(_xyz(pt) - _xyz(base)))
        ok = dist <= tol
        distances.append(dist)
        items.append({"id": pt["id"], "value": dist, "result": "pass" if ok else "fail"})
        rows.append(
            f"{pt['id']},{_fmt(pt['x'])},{_fmt(pt['y'])},{_fmt(pt['z'])},"
            f"{_fmt(base['x'])},{_fmt(base['y'])},{_fmt(base['z'])},{_fmt(dist)},"
            f"{'Pass' if ok else 'Fail'}"
        )
    rows.extend(_stat_rows(distances, 6))
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return items


def write_comparison_distances_csv(
    path: Path, case_d: list[dict], ref_d: list[dict], tol: float
) -> list[dict]:
    ref = {d["id"]: d["value"] for d in ref_d}
    rows = ["Name, Distance, Distance_ref, Difference[mm],Result"]
    items: list[dict] = []
    diffs: list[float] = []
    for row in case_d:
        if row["id"] not in ref:
            continue
        case_v = float(row["value"])
        ref_v = float(ref[row["id"]])
        diff = case_v - ref_v
        ok = abs(diff) <= tol
        diffs.append(diff)
        items.append({"id": row["id"], "value": case_v, "baseline": ref_v, "diff": diff, "result": "pass" if ok else "fail"})
        rows.append(f"{row['id']},{_fmt(case_v)},{_fmt(ref_v)},{_fmt(diff)},{'Pass' if ok else 'Fail'}")
    rows.extend(_stat_rows(diffs, 2))
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return items


def write_angles_csv(path: Path, axes: dict, tol: float) -> list[dict]:
    labels = ("X Axis", "Y Axis", "Z Axis")
    keys = ("ux", "uy", "uz")
    rows = ["Name, x, y, z, Angle to Axis [deg], Result"]
    items: list[dict] = []
    for label, key, angle in zip(labels, keys, axes["angles_to_axis"]):
        vec = axes[key]
        ok = angle <= tol
        items.append({"id": label, "value": angle, "result": "pass" if ok else "fail"})
        rows.append(
            f"{label}, {vec[0]:.7f},{vec[1]:.7f},{vec[2]:.7f}, {_fmt(angle, 2)}, {'Pass' if ok else 'Fail'}"
        )
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return items


def analyze(ct: sitk.Image, result_dir: str | Path, machine: dict) -> dict:
    dest = Path(result_dir)
    dest.mkdir(parents=True, exist_ok=True)
    points = find_markers(ct, machine, dest)
    distances = pairwise_distances(points)
    axes = axes_and_angles(points, machine)
    write_points_txt(dest / "points.txt", points)
    write_distances_txt(dest / "distances.txt", distances)
    write_axes_txt(dest / "axes_and_angles.txt", axes)
    data = {"points": points, "distances": distances, "axes": axes}
    write_analysis_result(dest, data)
    return data


def write_analysis_result(folder: str | Path, data: dict) -> Path:
    dest = analysis_result_path(folder)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return dest


def load_analysis_result(folder: str | Path) -> dict:
    folder = Path(folder)
    dest = analysis_result_path(folder)
    if dest.is_file():
        loaded = json.loads(dest.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}
    points_file = folder / "points.txt"
    if not points_file.is_file():
        return {}
    points = read_points_txt(points_file)
    distances = pairwise_distances(points)
    axes_file = folder / "axes_and_angles.txt"
    axes = {}
    if axes_file.is_file():
        info = Param(axes_file)
        axes = {
            "ux": float_list(info.get_value("ux")),
            "uy": float_list(info.get_value("uy")),
            "uz": float_list(info.get_value("uz")),
            "angles_to_axis": float_list(info.get_value("angles_to_axis")),
        }
    return {"points": points, "distances": distances, "axes": axes}


def write_case_result(result_dir: str | Path, baseline_dir: str | Path, machine: dict) -> Path:
    dest = Path(result_dir)
    case_data = load_analysis_result(dest)
    base_data = load_analysis_result(analysis_dir(baseline_dir))
    if not case_data.get("points") or not base_data.get("points"):
        raise RuntimeError("missing case or baseline marker points")
    point_tol = machine_float(machine, "point_to_point_dist_tol", 5.0)
    dist_tol = machine_float(machine, "dist_tol", 1.0)
    axis_tol = machine_float(machine, "axis_tol_deg", 1.0)
    point_items = write_comparison_points_csv(
        dest / "comparison_points.csv", case_data["points"], base_data["points"], point_tol
    )
    dist_items = write_comparison_distances_csv(
        dest / "comparison_distances.csv",
        case_data.get("distances") or pairwise_distances(case_data["points"]),
        base_data.get("distances") or pairwise_distances(base_data["points"]),
        dist_tol,
    )
    axis_items = write_angles_csv(dest / "angles.csv", case_data.get("axes") or {}, axis_tol)
    items = []
    for row in point_items:
        items.append({**row, "table": "points", "tol": point_tol})
    for row in dist_items:
        items.append({**row, "table": "distances", "tol": dist_tol})
    for row in axis_items:
        items.append({**row, "table": "axes", "tol": axis_tol})
    n_fail = sum(1 for item in items if item["result"] == "fail")
    n_pass = sum(1 for item in items if item["result"] == "pass")
    summary = {
        "result": "fail" if n_fail else ("pass" if n_pass else "partial"),
        "n_pass": n_pass,
        "n_fail": n_fail,
        "failed": [f"{item['table']}.{item['id']}" for item in items if item["result"] == "fail"],
        "items": items,
    }
    out = dest / CASE_RESULT_NAME
    out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    logger.info("wrote %s result=%s", out, summary["result"])
    return out


def _csv_has_fail(path: Path) -> bool | None:
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    if re.search(r"\bfail\b", text, re.IGNORECASE):
        return True
    if re.search(r"\bpass\b", text, re.IGNORECASE):
        return False
    return None


def read_case_result(folder: str | Path) -> str | None:
    folder = Path(folder)
    for path in (
        folder / "3.analysis" / CASE_RESULT_NAME,
        folder / "out" / CASE_RESULT_NAME,
        folder / CASE_RESULT_NAME,
    ):
        try:
            with path.open("r", encoding="utf-8") as fh:
                head = fh.read(256)
        except OSError:
            continue
        match = _RESULT_HEAD_RE.search(head)
        if match:
            return match.group(1).lower()
    fails = [
        _csv_has_fail(analysis_dir(folder) / name)
        for name in ("comparison_points.csv", "comparison_distances.csv", "angles.csv")
    ]
    known = [v for v in fails if v is not None]
    if not known:
        return None
    return "fail" if any(known) else "pass"


def analysis_tables_for_display(folder: str | Path) -> list[tuple[str, list[list[str]]]]:
    folder = Path(folder)
    dest = analysis_dir(folder) if (folder / "3.analysis").is_dir() or (folder / "out").is_dir() else folder
    tables: list[tuple[str, list[list[str]]]] = []
    for name, title in (
        ("comparison_points.csv", "Points"),
        ("comparison_distances.csv", "Distances"),
        ("angles.csv", "Axes"),
    ):
        path = dest / name
        if not path.is_file():
            continue
        rows = [line.split(",") for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines() if line.strip()]
        tables.append((title, [[c.strip() for c in row] for row in rows]))
    return tables
