"""HTML report and case-tab sections for MPC point / distance / axis checks."""

from __future__ import annotations

import csv
import html
from datetime import datetime
from pathlib import Path

from .analysis import analysis_dir, load_analysis_result, read_case_result
from .app_settings import machine_name, phantom_name
from .param import Param

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>{title}</title>
  <style>
    body {{ font-family: Segoe UI, Arial, sans-serif; margin: 16px; color: #111827; }}
    h1 {{ font-size: 22px; margin: 0 0 12px; }}
    .meta {{ margin-bottom: 16px; color: #334155; }}
    .section {{ border: 1px solid #d1d5db; border-radius: 6px; padding: 10px; margin-top: 14px; }}
    .section_head {{ font-size: 16px; font-weight: 700; margin-bottom: 8px; }}
    .section_foot {{ color: #64748b; margin-top: 8px; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #e5e7eb; padding: 4px 8px; text-align: center; }}
    th {{ background: #f3f4f6; }}
    .fail {{ color: #b91c1c; font-weight: 700; }}
    .pass {{ color: #15803d; }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  <div class="meta">Date/Time {date} / {time} &nbsp;|&nbsp; Operator {user} &nbsp;|&nbsp; Machine {machine}</div>
  <div class="section">
    <div class="section_head">Point Comparison</div>
    <table>{point_rows}</table>
    <div class="section_foot">Tolerance = {point_tol} mm</div>
  </div>
  <div class="section">
    <div class="section_head">Distance Comparison</div>
    <table>{dist_rows}</table>
    <div class="section_foot">Tolerance = {dist_tol} mm</div>
  </div>
  <div class="section">
    <div class="section_head">Axis Orientation Check</div>
    <table>{axis_rows}</table>
    <div class="section_foot">Tolerance = {axis_tol} deg</div>
  </div>
</body>
</html>
"""


def analysis_is_done(case_dir: str | Path) -> bool:
    dest = analysis_dir(case_dir)
    return (dest / "result.json").is_file() or (dest / "report.html").is_file() or (dest / "comparison_points.csv").is_file()


def _date_time_from_name(name: str) -> tuple[str, str, str]:
    parts = str(name or "").split("_")
    date = parts[0] if parts else ""
    time = parts[1] if len(parts) > 1 else ""
    user = parts[2] if len(parts) > 2 else ""
    return date, time, user


def read_case_header(case_dir: str | Path) -> tuple[str, str, str]:
    folder = Path(case_dir)
    info_path = folder / "info.txt"
    info = Param(info_path) if info_path.is_file() else Param()
    name = info.get_value("PatientName")
    if "^" in name:
        user = name.split("^", 1)[1]
    elif "," in name:
        user = name.split(",", 1)[1] if "," in name else name
    else:
        user = name
    date = info.get_value("StudyDate") or info.get_value("SeriesDate")
    time = info.get_value("StudyTime") or info.get_value("SeriesTime")
    if not date:
        date, time, name_user = _date_time_from_name(folder.name)
        user = user or name_user
    if not user:
        _d, _t, name_user = _date_time_from_name(folder.name)
        user = name_user or "NA"
    return user or "NA", date, time


def _csv_html_rows(path: Path) -> str:
    if not path.is_file():
        return ""
    rows = list(csv.reader(path.read_text(encoding="utf-8-sig", errors="replace").splitlines()))
    if not rows:
        return ""
    out = ["<tr>" + "".join(f"<th>{html.escape(c.strip())}</th>" for c in rows[0]) + "</tr>"]
    for row in rows[1:]:
        cls = " class='fail'" if any("fail" in c.lower() for c in row) else ""
        out.append(f"<tr{cls}>" + "".join(f"<td>{html.escape(c.strip())}</td>" for c in row) + "</tr>")
    return "\n".join(out)


def _summary_name(name: str) -> bool:
    return name.strip().upper() in {"MIN", "MIM", "MAX", "MEAN", "STD"}


def _parse_comparison_points(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.is_file():
        return rows
    data = list(csv.reader(path.read_text(encoding="utf-8-sig", errors="replace").splitlines()))
    for row in data[1:]:
        if not row or _summary_name(row[0]):
            continue
        result = str(row[-1] if row else "").strip().lower()
        try:
            dist = float(row[7])
        except (IndexError, ValueError):
            continue
        rows.append(
            {
                "label": row[0].strip(),
                "value": dist,
                "baseline": None,
                "diff": dist,
                "result": "fail" if result == "fail" else ("pass" if result == "pass" else ""),
            }
        )
    return rows


def _parse_comparison_distances(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.is_file():
        return rows
    data = list(csv.reader(path.read_text(encoding="utf-8-sig", errors="replace").splitlines()))
    for row in data[1:]:
        if not row or _summary_name(row[0]):
            continue
        result = str(row[-1] if row else "").strip().lower()
        try:
            value = float(row[1])
            baseline = float(row[2])
            diff = float(row[3])
        except (IndexError, ValueError):
            continue
        rows.append(
            {
                "label": row[0].strip(),
                "value": value,
                "baseline": baseline,
                "diff": diff,
                "result": "fail" if result == "fail" else ("pass" if result == "pass" else ""),
            }
        )
    return rows


def _parse_angles(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.is_file():
        return rows
    data = list(csv.reader(path.read_text(encoding="utf-8-sig", errors="replace").splitlines()))
    for row in data[1:]:
        if not row:
            continue
        result = str(row[-1] if row else "").strip().lower()
        try:
            angle = float(row[4])
        except (IndexError, ValueError):
            continue
        rows.append(
            {
                "label": row[0].strip(),
                "value": angle,
                "baseline": None,
                "diff": angle,
                "result": "fail" if result == "fail" else ("pass" if result == "pass" else ""),
            }
        )
    return rows


def build_case_report(case_dir: str | Path, machine: dict) -> dict:
    case = Path(case_dir)
    dest = analysis_dir(case)
    user, date, time = read_case_header(case)
    analyzed = analysis_is_done(case)
    sections = []
    if (dest / "comparison_points.csv").is_file():
        sections.append(
            {
                "key": "points",
                "title": "Point Comparison",
                "label_header": "Marker",
                "value_header": "Distance [mm]",
                "num_format": "0.0",
                "tolerance": f"Tolerance = {machine.get('point_to_point_dist_tol', 5.0)} mm",
                "tol": float(machine.get("point_to_point_dist_tol") or 5.0),
                "rows": _parse_comparison_points(dest / "comparison_points.csv"),
            }
        )
    if (dest / "comparison_distances.csv").is_file():
        sections.append(
            {
                "key": "distances",
                "title": "Distance Comparison",
                "label_header": "Pair",
                "value_header": "Distance [mm]",
                "num_format": "0.0",
                "tolerance": f"Tolerance = {machine.get('dist_tol', 1.0)} mm",
                "tol": float(machine.get("dist_tol") or 1.0),
                "rows": _parse_comparison_distances(dest / "comparison_distances.csv"),
            }
        )
    if (dest / "angles.csv").is_file():
        sections.append(
            {
                "key": "axes",
                "title": "Axis Orientation Check",
                "label_header": "Axis",
                "value_header": "Angle [deg]",
                "num_format": "0.00",
                "tolerance": f"Tolerance = {machine.get('axis_tol_deg', 1.0)} deg",
                "tol": float(machine.get("axis_tol_deg") or 1.0),
                "rows": _parse_angles(dest / "angles.csv"),
            }
        )
    n_fail = sum(1 for sec in sections for row in sec["rows"] if row["result"] == "fail")
    n_pass = sum(1 for sec in sections for row in sec["rows"] if row["result"] == "pass")
    if analyzed and n_fail:
        overall = "fail"
    elif analyzed and n_pass:
        overall = "pass"
    else:
        overall = read_case_result(case) or "new"
    return {
        "operator": user,
        "date": date,
        "time": time,
        "datetime": f"{date} / {time}".strip(" /"),
        "analyzed": analyzed,
        "result": overall,
        "sections": sections,
    }


def write_report(case_dir: Path, baseline_dir: Path, result_dir: Path, machine: dict) -> Path:
    info = Param(case_dir / "info.txt") if (case_dir / "info.txt").is_file() else Param()
    user, date, time = read_case_header(case_dir)
    if not date:
        now = datetime.now()
        date = now.strftime("%Y%m%d")
        time = now.strftime("%H%M%S")
    title = str(machine.get("report_title") or f"CT Geometry Check using MPC phantom ({machine_name(machine)})")
    point_rows = _csv_html_rows(result_dir / "comparison_points.csv")
    dist_rows = _csv_html_rows(result_dir / "comparison_distances.csv")
    axis_rows = _csv_html_rows(result_dir / "angles.csv")
    html_text = _HTML_TEMPLATE.format(
        title=html.escape(title),
        date=html.escape(date),
        time=html.escape(time),
        user=html.escape(user or info.get_value("PatientName") or "NA"),
        machine=html.escape(machine_name(machine) or phantom_name(machine)),
        point_rows=point_rows,
        dist_rows=dist_rows,
        axis_rows=axis_rows,
        point_tol=machine.get("point_to_point_dist_tol", 5.0),
        dist_tol=machine.get("dist_tol", 1.0),
        axis_tol=machine.get("axis_tol_deg", 1.0),
    )
    template = Path(str(machine.get("html_report_template") or ""))
    if template.is_file():
        raw = template.read_text(encoding="utf-8")
        html_text = (
            raw.replace("{{{date}}}", date)
            .replace("{{{time}}}", time)
            .replace("{{{user}}}", user)
            .replace("{{{title}}}", title)
            .replace("{{{point_to_point_dist_tol}}}", str(machine.get("point_to_point_dist_tol") or ""))
            .replace("{{{dist_tol}}}", str(machine.get("dist_tol") or ""))
            .replace("{{{axis_tol_deg}}}", str(machine.get("axis_tol_deg") or ""))
            .replace("{{{point_rows}}}", point_rows)
            .replace("{{{dist_rows}}}", dist_rows)
            .replace("{{{axis_rows}}}", axis_rows)
        )
    dest = result_dir / "report.html"
    dest.write_text(html_text, encoding="utf-8")
    return dest
