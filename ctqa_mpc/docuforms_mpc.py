"""Upload one MPC case to DocuForms2 (same pattern as CatPhan / Winston-Lutz)."""

from __future__ import annotations

import csv
import json
import logging
import math
import mimetypes
import os
import re
import shutil
import ssl
import subprocess
import tempfile
import urllib.error
import urllib.request
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

from .analysis import analysis_dir, load_analysis_result, marker_count, pairwise_distances
from .app_settings import form_id_for_machine
from .identity import session_operator
from .report import analysis_is_done, read_case_header

logger = logging.getLogger(__name__)

MARKER_NAME = ".docuforms2_mpc.json"
AXIS_FIELD_IDS = ("x_axis", "y_axis", "z_axis")
AXIS_LABELS = ("X Axis", "Y Axis", "Z Axis")
_SKIP_CSV_NAMES = {"min", "max", "mean", "std"}
_INPUT_ID_RE = re.compile(
    r"<(?:input|select|textarea)\b[^>]*\bid\s*=\s*[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)


def _tol(machine: dict | None, key: str, default: float) -> float:
    if not machine or machine.get(key) in (None, ""):
        return default
    try:
        return abs(float(machine[key]))
    except (TypeError, ValueError):
        return default


def error_ranges_for_machine(machine: dict | None) -> dict:
    n = marker_count(machine) or 16
    point_tol = _tol(machine, "point_to_point_dist_tol", 5.0)
    dist_tol = _tol(machine, "dist_tol", 1.0)
    axis_tol = _tol(machine, "axis_tol_deg", 1.0)
    ranges: dict = {}
    for i in range(n):
        ranges[f"pt{i}"] = {"pass": f"0:{point_tol:g}"}
    for i in range(n):
        for j in range(i + 1, n):
            ranges[f"dist_pt{i}_pt{j}_error"] = {"pass": f"{-dist_tol:g}:{dist_tol:g}"}
    for field_id in AXIS_FIELD_IDS:
        ranges[field_id] = {"pass": f"0:{axis_tol:g}"}
    return ranges


def parse_float(value) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def performed_at_from_case(case_dir: Path) -> datetime | None:
    """Study date/time from info.txt, else the case folder name."""
    case_dir = Path(case_dir)
    _user, date, time = read_case_header(case_dir)
    digits_d = re.sub(r"\D", "", date or "")
    digits_t = re.sub(r"\D", "", time or "")[:6].ljust(6, "0") if time else "000000"
    if len(digits_d) == 8:
        try:
            return datetime.strptime(digits_d + digits_t, "%Y%m%d%H%M%S")
        except ValueError:
            pass
    match = re.match(r"^(\d{8})_(\d{6})", case_dir.name)
    if match:
        try:
            return datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S")
        except ValueError:
            pass
    return None


def _set_triplet(values: dict, field_id: str, value, baseline, error) -> None:
    v = parse_float(value)
    b = parse_float(baseline)
    e = parse_float(error)
    if v is not None:
        values[field_id] = str(v)
    if b is not None:
        values[f"{field_id}_baseline"] = str(b)
    if e is None and v is not None and b is not None:
        e = v - b
    if e is not None:
        values[f"{field_id}_error"] = f"{e:.2f}"


def _pt_xyz(point: dict) -> tuple[float, float, float]:
    return (float(point["x"]), float(point["y"]), float(point["z"]))


def _csv_rows(path: Path) -> list[list[str]]:
    if not path.is_file():
        return []
    rows: list[list[str]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        for raw in csv.reader(fh):
            if not raw:
                continue
            name = str(raw[0] or "").strip()
            if not name or name.lower() in _SKIP_CSV_NAMES or name.lower() == "name":
                continue
            rows.append([str(c).strip() for c in raw])
    return rows


def _axis_field_id(label: str) -> str | None:
    text = str(label or "").strip().lower().replace(" ", "_")
    if text.startswith("x"):
        return "x_axis"
    if text.startswith("y"):
        return "y_axis"
    if text.startswith("z"):
        return "z_axis"
    return None


def _fill_from_csvs(values: dict, dest: Path) -> None:
    for row in _csv_rows(dest / "comparison_points.csv"):
        if len(row) < 8:
            continue
        field_id = row[0]
        dist = parse_float(row[7])
        if field_id.startswith("pt") and dist is not None:
            values[field_id] = str(dist)
    for row in _csv_rows(dest / "comparison_distances.csv"):
        if len(row) < 4:
            continue
        field_id = row[0]
        if field_id.startswith("dist_"):
            _set_triplet(values, field_id, row[1], row[2], row[3])
    for row in _csv_rows(dest / "angles.csv"):
        if len(row) < 5:
            continue
        field_id = _axis_field_id(row[0])
        angle = parse_float(row[4])
        if field_id and angle is not None:
            values[field_id] = str(angle)


def _fill_from_results(values: dict, case_data: dict, base_data: dict) -> None:
    case_pts = {str(p.get("id")): p for p in (case_data.get("points") or []) if isinstance(p, dict)}
    base_pts = {str(p.get("id")): p for p in (base_data.get("points") or []) if isinstance(p, dict)}
    for pid, pt in case_pts.items():
        base = base_pts.get(pid)
        if not base:
            continue
        dist = math.dist(_pt_xyz(pt), _pt_xyz(base))
        values[pid] = str(dist)
    case_d = case_data.get("distances") or pairwise_distances(case_data.get("points") or [])
    base_d = {
        str(d.get("id")): parse_float(d.get("value"))
        for d in (base_data.get("distances") or pairwise_distances(base_data.get("points") or []))
        if isinstance(d, dict)
    }
    for row in case_d:
        if not isinstance(row, dict):
            continue
        field_id = str(row.get("id") or "")
        if not field_id.startswith("dist_"):
            continue
        _set_triplet(values, field_id, row.get("value"), base_d.get(field_id), None)
    angles = (case_data.get("axes") or {}).get("angles_to_axis") or []
    for field_id, angle in zip(AXIS_FIELD_IDS, angles):
        parsed = parse_float(angle)
        if parsed is not None:
            values[field_id] = str(parsed)


def values_from_case(
    case_dir: str | Path,
    machine: dict | None,
    *,
    performed_by: str = "",
) -> dict:
    """Form values from the HTML report tables (points, pairwise distances, axes)."""
    case = Path(case_dir)
    machine = machine or {}
    dest = analysis_dir(case)
    baseline_dir = Path(str(machine.get("baseline_dir") or ""))
    values: dict = {}
    stamp = performed_at_from_case(case)
    if stamp:
        values["performed_at"] = stamp.isoformat()
    operator = str(performed_by or "").strip()
    if not operator:
        user, _date, _time = read_case_header(case)
        operator = session_operator(user if user and user != "NA" else "")
    if operator:
        values["performed_by"] = operator
    _fill_from_csvs(values, dest)
    if not any(k.startswith("pt") or k.startswith("dist_") or k.endswith("_axis") for k in values):
        case_data = load_analysis_result(dest)
        base_data = load_analysis_result(analysis_dir(baseline_dir)) if baseline_dir.is_dir() else {}
        _fill_from_results(values, case_data, base_data)
    return values


def calculate_result(error_value: float, error_field_id: str, ranges: dict) -> str:
    block = ranges.get(error_field_id) or {}
    pass_range = str(block.get("pass") or "").split(":")
    warning_range = str(block.get("warning") or "").split(":")
    if len(pass_range) == 2:
        try:
            if float(pass_range[0]) <= error_value <= float(pass_range[1]):
                return "PASS"
        except (TypeError, ValueError):
            pass
    if len(warning_range) == 2:
        try:
            if float(warning_range[0]) <= error_value <= float(warning_range[1]):
                return "WARNING"
        except (TypeError, ValueError):
            pass
    return "FAIL"


def build_metadata(values: dict, machine: dict | None = None) -> dict:
    ranges = error_ranges_for_machine(machine)
    metadata: dict = {}
    for field_id, field_value in values.items():
        if field_id.endswith("_baseline"):
            base_field = field_id[: -len("_baseline")]
            metadata[field_id] = {"script": f"autofill_baseline('{base_field}', '{field_id}')"}
            continue
        if field_id.endswith("_error"):
            base_field = field_id[: -len("_error")]
            error_value = parse_float(field_value)
            result = calculate_result(error_value, field_id, ranges) if error_value is not None else ""
            error_metadata = {
                "script": (
                    "calc_physical_error({inputId: "
                    f"'{base_field}', baselineField: '{base_field}', "
                    f"baselineInputId: '{base_field}_baseline', outputId: '{field_id}', "
                    f"resultId: '{base_field}_result'}});"
                )
            }
            if field_id in ranges:
                error_metadata["passRange"] = ranges[field_id].get("pass", "")
                if ranges[field_id].get("warning"):
                    error_metadata["warningRange"] = ranges[field_id]["warning"]
            if result:
                error_metadata["result"] = result
            metadata[field_id] = error_metadata
            continue
        if field_id in ranges:
            error_value = parse_float(field_value)
            result = calculate_result(error_value, field_id, ranges) if error_value is not None else ""
            block = {"passRange": ranges[field_id].get("pass", ""), "result": result or ""}
            if ranges[field_id].get("warning"):
                block["warningRange"] = ranges[field_id]["warning"]
            metadata[field_id] = block
            continue
        metadata[field_id] = {"result": ""}
    return metadata


def calculate_overall_result(metadata: dict) -> str:
    results = [
        str(meta.get("result") or "").upper()
        for meta in metadata.values()
        if isinstance(meta, dict) and meta.get("result")
    ]
    if not results:
        return ""
    if any(r == "FAIL" for r in results):
        return "FAIL"
    if any(r == "WARNING" for r in results):
        return "WARNING"
    if all(r == "PASS" for r in results):
        return "PASS"
    return ""


def _ssl_context(verify: bool):
    if verify:
        return ssl.create_default_context()
    return ssl._create_unverified_context()


def _urlopen(req: urllib.request.Request, timeout: float, verify: bool):
    return urllib.request.urlopen(req, timeout=timeout, context=_ssl_context(verify))


def post_json(url: str, payload: dict, *, timeout: float, verify: bool) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with _urlopen(req, timeout, verify) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"HTTP {exc.code} {exc.reason}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"request failed: {exc.reason}") from exc
    if not body.strip():
        return {}
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def get_json(url: str, *, timeout: float, verify: bool):
    req = urllib.request.Request(url, method="GET")
    try:
        with _urlopen(req, timeout, verify) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"HTTP {exc.code} {exc.reason}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"request failed: {exc.reason}") from exc
    if not body.strip():
        return {}
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {}


def extract_form_fields(html: str) -> list[dict]:
    fields: list[dict] = []
    seen: set[str] = set()
    for match in _INPUT_ID_RE.finditer(html or ""):
        name = match.group(1).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        fields.append({"name": name, "type": "number" if name not in ("performed_at", "performed_by") else "text"})
    return fields


def mpc_form_html(
    *,
    n_markers: int = 16,
    point_tol: float = 5.0,
    dist_tol: float = 1.0,
    axis_tol: float = 1.0,
    title: str = "CT QA MPC Case Review Form",
) -> str:
    """DocuForms2 form HTML matching the MPC report tables."""
    point_range = f"0:{point_tol:g}"
    dist_range = f"{-dist_tol:g}:{dist_tol:g}"
    axis_range = f"0:{axis_tol:g}"
    parts = [
        "<body>",
        '<div class="container-fluid">',
        f'  <h2 class="mb-4">{title}</h2>',
        '  <div class="card mb-3">',
        '    <div class="card-header bg-primary text-white"><h5 class="mb-0">Case Information</h5></div>',
        '    <div class="card-body"><div class="row mb-3">',
        '      <div class="col-md-6"><label for="performed_at" class="form-label">Performed At</label>',
        '      <input type="datetime-local" id="performed_at" name="performed_at" required></div>',
        '      <div class="col-md-6"><label for="performed_by" class="form-label">Performed By</label>',
        '      <input type="text" id="performed_by" name="performed_by" placeholder="e.g., JA" required></div>',
        "    </div></div>",
        "  </div>",
        '  <div class="card mb-3">',
        '    <div class="card-header bg-secondary text-white"><h5 class="mb-0">Point Comparison (Distance to baseline [mm])</h5></div>',
        '    <div class="card-body">',
    ]
    for i in range(n_markers):
        fid = f"pt{i}"
        parts.append(
            '      <div class="mb-3"><div class="input-group mb-1">'
            f'<span class="input-group-text">{fid}</span>'
            f'<input id="{fid}" name="{fid}" type="number" data-pass-range="{point_range}">'
            f'<span id="{fid}_result" class="input-group-text fw-bold" data-result=""></span>'
            "</div></div>"
        )
    parts.extend(
        [
            "    </div>",
            "  </div>",
            '  <div class="card mb-3">',
            '    <div class="card-header bg-secondary text-white"><h5 class="mb-0">Distance Comparison [mm]</h5></div>',
            '    <div class="card-body">',
        ]
    )
    for i in range(n_markers):
        for j in range(i + 1, n_markers):
            fid = f"dist_pt{i}_pt{j}"
            label = f"pt{i}-pt{j}"
            parts.append(
                '      <div class="mb-3"><div class="input-group mb-1">'
                f'<span class="input-group-text">{label}</span>'
                f'<input id="{fid}" name="{fid}" type="number">'
                '<span class="input-group-text">Baseline</span>'
                f'<input id="{fid}_baseline" name="{fid}_baseline" type="number" data-script="autofill_baseline(\'{fid}\', \'{fid}_baseline\')">'
                '<span class="input-group-text">Error</span>'
                f'<input id="{fid}_error" name="{fid}_error" type="number" readonly data-pass-range="{dist_range}" '
                f'data-script="calc_physical_error({{inputId: \'{fid}\', baselineField: \'{fid}\', baselineInputId: \'{fid}_baseline\', outputId: \'{fid}_error\', resultId: \'{fid}_result\'}});">'
                f'<span id="{fid}_result" class="input-group-text fw-bold" data-result=""></span>'
                "</div></div>"
            )
    parts.extend(
        [
            "    </div>",
            "  </div>",
            '  <div class="card mb-3">',
            '    <div class="card-header bg-secondary text-white"><h5 class="mb-0">Axis Orientation Check [deg]</h5></div>',
            '    <div class="card-body">',
        ]
    )
    for fid, label in zip(AXIS_FIELD_IDS, AXIS_LABELS):
        parts.append(
            '      <div class="mb-3"><div class="input-group mb-1">'
            f'<span class="input-group-text">{label}</span>'
            f'<input id="{fid}" name="{fid}" type="number" data-pass-range="{axis_range}">'
            f'<span id="{fid}_result" class="input-group-text fw-bold" data-result=""></span>'
            "</div></div>"
        )
    parts.extend(["    </div>", "  </div>", "</div>", "</body>", ""])
    return "\n".join(parts)


def upload_file(
    backend_url: str,
    file_path: Path,
    original_name: str,
    *,
    timeout: float,
    verify: bool,
) -> dict | None:
    raw = file_path.read_bytes()
    boundary = "----CTQAMPC" + uuid.uuid4().hex
    ctype = mimetypes.guess_type(original_name)[0] or "application/octet-stream"
    header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{original_name}"\r\n'
        f"Content-Type: {ctype}\r\n\r\n"
    ).encode("utf-8")
    footer = f"\r\n--{boundary}--\r\n".encode("utf-8")
    req = urllib.request.Request(
        f"{backend_url.rstrip('/')}/api/upload",
        data=header + raw + footer,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with _urlopen(req, timeout, verify) as resp:
            parsed = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        logger.warning("DocuForms2 upload %s failed: %s", original_name, exc)
        return None
    url = str((parsed or {}).get("url") or "")
    if not url:
        return None
    return {"url": url, "originalName": original_name}


_ZIP_SKIP_DIRS = {"results", "out", "1.reg", "2.seg"}


def _is_input_dicom(path: Path) -> bool:
    name = path.name.upper()
    suffix = path.suffix.lower()
    return suffix == ".dcm" or suffix == "" and name.startswith("CT")


def zip_dicoms(case_dir: Path, dest: Path) -> bool:
    """Zip input CT DICOMs as ``input_dcm.zip`` (same name as CatPhan)."""
    case_dir = Path(case_dir)
    files: list[Path] = []
    for path in case_dir.rglob("*"):
        if not path.is_file() or not _is_input_dicom(path):
            continue
        if any(part in _ZIP_SKIP_DIRS for part in path.relative_to(case_dir).parts[:-1]):
            continue
        files.append(path)
    files.sort()
    if not files:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            try:
                arc = path.relative_to(case_dir).as_posix()
            except ValueError:
                arc = path.name
            zf.write(path, arc)
    logger.info("wrote %s (%s files, %s KB)", dest.name, len(files), f"{dest.stat().st_size / 1024:.1f}")
    return True


def case_report_html(case_dir: Path) -> Path | None:
    dest = analysis_dir(case_dir)
    for path in (dest / "report.html", Path(case_dir) / "results" / "report.html", Path(case_dir) / "report.html"):
        if path.is_file():
            return path
    return None


def html_to_pdf(html_path: Path, dest: Path) -> bool:
    """Convert report.html to PDF (WeasyPrint, else Chrome/Edge)."""
    html_path = Path(html_path)
    dest = Path(dest)
    if not html_path.is_file():
        return False
    try:
        text = html_path.read_text(encoding="utf-8")
    except OSError:
        logger.warning("could not read %s for PDF", html_path)
        return False
    text = text.replace(".\\", "./").replace("\\", "/")
    tmp_html = html_path.parent / f".{html_path.stem}.pdfsrc.html"
    local_dir = None
    try:
        tmp_html.write_text(text, encoding="utf-8")
        if _try_write_pdf(tmp_html, dest):
            return True
        local_html, local_dir = _local_pdf_workspace(html_path, text)
        if local_html is not None and _try_write_pdf(local_html, dest):
            return True
    except Exception:
        logger.warning("report.pdf conversion failed", exc_info=True)
    finally:
        try:
            tmp_html.unlink(missing_ok=True)
        except OSError:
            pass
        if local_dir is not None:
            shutil.rmtree(local_dir, ignore_errors=True)
    logger.warning("report.pdf conversion failed for %s", html_path)
    return False


def _try_write_pdf(html_path: Path, dest: Path) -> bool:
    if not (_pdf_weasyprint(html_path, dest) or _pdf_chromium(html_path, dest)):
        return False
    size = dest.stat().st_size if dest.is_file() else 0
    if size <= 0:
        return False
    logger.info("created report.pdf (%s KB)", f"{size / 1024:.1f}")
    return True


def _local_pdf_workspace(html_path: Path, rewritten_html: str) -> tuple[Path | None, Path | None]:
    try:
        work = Path(tempfile.mkdtemp(prefix="mpc_pdf_"))
        dest_html = work / html_path.name
        dest_html.write_text(rewritten_html, encoding="utf-8")
        for src in html_path.parent.iterdir():
            if not src.is_file() or src.suffix.lower() in (".html", ".htm"):
                continue
            shutil.copy2(src, work / src.name)
        return dest_html, work
    except OSError:
        logger.warning("could not copy report assets for PDF", exc_info=True)
        return None, None


def _pdf_weasyprint(html_path: Path, dest: Path) -> bool:
    try:
        from weasyprint import HTML as WeasyHTML
    except ImportError:
        return False
    try:
        WeasyHTML(string=html_path.read_text(encoding="utf-8"), base_url=str(html_path.parent)).write_pdf(
            str(dest)
        )
    except Exception:
        logger.warning("weasyprint PDF failed", exc_info=True)
        return False
    return dest.is_file() and dest.stat().st_size > 0


def _chromium_exe() -> str | None:
    roots = (
        os.environ.get("PROGRAMFILES", r"C:\Program Files"),
        os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
        os.environ.get("LOCALAPPDATA", ""),
    )
    rels = (
        Path("Microsoft/Edge/Application/msedge.exe"),
        Path("Google/Chrome/Application/chrome.exe"),
    )
    for root in roots:
        if not root:
            continue
        for rel in rels:
            path = Path(root) / rel
            if path.is_file():
                return str(path)
    return shutil.which("msedge") or shutil.which("chrome") or shutil.which("chromium")


def _pdf_chromium(html_path: Path, dest: Path) -> bool:
    exe = _chromium_exe()
    if not exe:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file():
        try:
            dest.unlink()
        except OSError:
            return False
    cmd = [
        exe,
        "--headless",
        "--disable-gpu",
        "--no-pdf-header-footer",
        f"--print-to-pdf={dest}",
        html_path.resolve().as_uri(),
    ]
    try:
        proc = subprocess.run(cmd, check=False, timeout=180, capture_output=True)
    except Exception:
        logger.warning("Chrome/Edge PDF conversion failed", exc_info=True)
        return False
    if proc.returncode != 0 or not dest.is_file() or dest.stat().st_size <= 0:
        err = (proc.stderr or b"").decode("utf-8", errors="replace")[:400]
        logger.warning("Chrome/Edge PDF conversion failed: %s", err or proc.returncode)
        return False
    return True


def submit_form(
    backend_url: str,
    form_id: str,
    values: dict,
    metadata: dict,
    result: str,
    attachments: list[dict] | None,
    *,
    timeout: float,
    verify: bool,
) -> dict:
    payload = {
        "values": values,
        "metadata": metadata,
        "result": result,
        "comments": "",
        "submissionHtml": "",
        "attachments": attachments or None,
    }
    return post_json(
        f"{backend_url.rstrip('/')}/api/forms/{form_id}/submit",
        payload,
        timeout=timeout,
        verify=verify,
    )


def already_imported(case_dir: Path) -> bool:
    return (case_dir / MARKER_NAME).is_file()


def write_marker(case_dir: Path, payload: dict) -> None:
    path = case_dir / MARKER_NAME
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def notify_docuforms_event(
    step: dict | None,
    status: str,
    case_dir: Path,
    machine_cfg: dict | None,
    extra: dict | None = None,
) -> None:
    """Email success or failure lists for this DocuForms2 step."""
    from .emailer import send_list_email

    step = step or {}
    if status in ("ok", "dry-run"):
        to = step.get("email_success_event_to")
    elif status == "failed":
        to = step.get("email_failure_event_to")
    else:
        return
    machine = str((machine_cfg or {}).get("NAME") or "").strip()
    label = f"{machine}/{case_dir.name}" if machine else case_dir.name
    lines = [
        f"status={status}",
        f"machine={machine}",
        f"case={case_dir.name}",
        f"path={case_dir}",
    ]
    for key, value in (extra or {}).items():
        if value is None or str(value).strip() == "":
            continue
        lines.append(f"{key}={value}")
    send_list_email(
        to,
        f"DocuForms2 {status}: {label}",
        "\n".join(lines),
        context="postprocess.docuforms2_mpc",
        subject=f"CTQA-MPC DocuForms2 {status}: {label}"[:180],
        blocking=True,
    )


def upload_case(
    case_dir: str | Path,
    machine_cfg: dict | None,
    step: dict,
) -> str:
    """Upload one case. Returns 'ok', 'skipped', or 'dry-run'. Raises on hard failure."""
    case_dir = Path(case_dir)
    step = step or {}
    form_id = form_id_for_machine(step, machine_cfg)
    backend = str(step.get("backend_url") or "").strip().rstrip("/")
    if not form_id:
        logger.info("DocuForms2 skipped: no form_id for this machine")
        notify_docuforms_event(step, "skipped", case_dir, machine_cfg, {"reason": "no form_id"})
        return "skipped"
    if not backend:
        logger.info("DocuForms2 skipped: empty backend_url")
        notify_docuforms_event(
            step, "skipped", case_dir, machine_cfg, {"reason": "empty backend_url"}
        )
        return "skipped"
    if not analysis_is_done(case_dir):
        logger.info("DocuForms2 skipped: no analysis result in %s", case_dir)
        notify_docuforms_event(
            step, "skipped", case_dir, machine_cfg, {"reason": "no analysis result"}
        )
        return "skipped"
    resubmit = bool(step.get("resubmit", False))
    if already_imported(case_dir) and not resubmit:
        logger.info("DocuForms2 skipped (already imported): %s", case_dir)
        notify_docuforms_event(
            step, "skipped", case_dir, machine_cfg, {"reason": "already imported"}
        )
        return "skipped"

    values = values_from_case(case_dir, machine_cfg)
    if not any(k for k in values if k not in ("performed_at", "performed_by")):
        logger.info("DocuForms2 skipped: no measurement fields in %s", case_dir)
        notify_docuforms_event(
            step, "skipped", case_dir, machine_cfg, {"reason": "no measurement fields"}
        )
        return "skipped"
    metadata = build_metadata(values, machine_cfg)
    result = calculate_overall_result(metadata)
    timeout = float(step.get("timeout_sec") or 300)
    verify = bool(step.get("verify_ssl", False))
    dry_run = bool(step.get("dry_run", False))
    attachments: list[dict] = []
    tmp_zip = None
    tmp_pdf = None
    try:
        if not dry_run and bool(step.get("attach_dcm_zip", True)):
            tmp_zip = Path(tempfile.gettempdir()) / f"mpc_dcm_{case_dir.name}_{uuid.uuid4().hex}.zip"
            if not zip_dicoms(case_dir, tmp_zip):
                raise RuntimeError(f"no input DICOM files to zip in {case_dir}")
            info = upload_file(
                backend, tmp_zip, "input_dcm.zip", timeout=timeout, verify=verify
            )
            if not info:
                raise RuntimeError("input_dcm.zip upload failed")
            attachments.append(info)
        if not dry_run and bool(step.get("attach_pdf", True)):
            report = case_report_html(case_dir)
            if report is None:
                raise RuntimeError(f"report.html missing in {case_dir}; cannot attach report.pdf")
            tmp_pdf = Path(tempfile.gettempdir()) / f"ctqa_pdf_{case_dir.name}_{uuid.uuid4().hex}.pdf"
            if not html_to_pdf(report, tmp_pdf):
                raise RuntimeError("could not convert report.html to report.pdf")
            case_pdf = report.parent / "report.pdf"
            try:
                shutil.copy2(tmp_pdf, case_pdf)
            except OSError:
                logger.warning("could not copy report.pdf into %s", report.parent)
            info = upload_file(backend, tmp_pdf, "report.pdf", timeout=timeout, verify=verify)
            if not info:
                raise RuntimeError("report.pdf upload failed")
            attachments.append(info)
        if dry_run:
            logger.info(
                "DocuForms2 dry-run %s form=%s fields=%s result=%s",
                case_dir.name,
                form_id,
                len(values),
                result,
            )
            notify_docuforms_event(
                step,
                "dry-run",
                case_dir,
                machine_cfg,
                {"form_id": form_id, "backend_url": backend, "result": result},
            )
            return "dry-run"
        response = submit_form(
            backend,
            form_id,
            values,
            metadata,
            result,
            attachments,
            timeout=timeout,
            verify=verify,
        )
        submission_id = str((response or {}).get("_id") or (response or {}).get("id") or "")
        write_marker(
            case_dir,
            {
                "form_id": form_id,
                "backend_url": backend,
                "result": result,
                "performed_at": values.get("performed_at") or "",
                "submission_id": submission_id,
                "submitted_at": datetime.now().isoformat(timespec="seconds"),
                "attachments": [
                    a.get("originalName") for a in attachments if a.get("originalName")
                ],
            },
        )
        logger.info(
            "DocuForms2 submitted %s to %s result=%s performed_at=%s id=%s",
            case_dir.name,
            form_id,
            result,
            values.get("performed_at") or "",
            submission_id,
        )
        notify_docuforms_event(
            step,
            "ok",
            case_dir,
            machine_cfg,
            {
                "form_id": form_id,
                "backend_url": backend,
                "result": result,
                "performed_at": values.get("performed_at") or "",
                "submission_id": submission_id,
                "attachments": ",".join(a.get("originalName") or "" for a in attachments),
            },
        )
        return "ok"
    finally:
        for path in (tmp_zip, tmp_pdf):
            if path is None:
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
