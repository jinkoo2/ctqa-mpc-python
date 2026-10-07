"""App settings in settings.json next to the executable."""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

SETTINGS_NAME = "settings.json"
ENV_SETTINGS = "CTQA_MPC_SETTINGS"
MACHINES_KEY = "MACHINES"
INSTITUTION_KEY = "Institution"
RUN_MODE_KEY = "RunMode"
RUN_MODE_CLINIC = "Clinic"
RUN_MODE_SIMPLE = "Simple"
RUN_MODES = (RUN_MODE_CLINIC, RUN_MODE_SIMPLE)
NOTIFICATIONS_KEY = "Notifications"
WATCHER_KEY = "Watcher"
ELASTIX_KEY = "Elastix"
POST_PROCESSING_KEY = "PostProcessing"
DOCUFORMS2_CTQA_TYPE = "docuforms2_ctqa"
DEFAULT_CASE_FOLDER_REGEX = r"(?i).*_mpc.*"
CASE_FOLDER_NAME_REGEX_KEY = "CASE_FOLDER_NAME_REGEX"
ERROR_EMAIL_TO_KEY = "error_email_to"
EVENT_EMAIL_TO_KEY = "event_email_to"
NEW_CASE_EMAIL_TO_KEY = "new_case_email_to"
CHAT_CHANNELS = ("google_chat", "slack", "microsoft_teams", "discord")

MASK_GROUPS = (
    ("HU", "num_of_HU_masks"),
    ("UF", "num_of_UF_masks"),
    ("HC", "num_of_HC_masks"),
    ("LC", "num_of_LC_masks"),
    ("geo", "num_of_geo_masks"),
    ("DT", "num_of_DT_masks"),
)


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    argv0 = Path(sys.argv[0]) if sys.argv and sys.argv[0] not in ("", "-c") else Path()
    try:
        argv0 = argv0.expanduser().resolve()
    except OSError:
        argv0 = Path()
    if argv0.suffix.lower() == ".exe" and argv0.is_file():
        return argv0.parent
    pkg = Path(__file__).resolve().parent
    root = pkg.parent
    if root.name.lower() in ("site-packages", "dist-packages"):
        return argv0.parent if argv0.is_file() else Path.cwd()
    return root


def settings_path(*, writing: bool = False) -> Path:
    override = os.environ.get(ENV_SETTINGS, "").strip()
    if override:
        return Path(override)
    return app_dir() / SETTINGS_NAME


def strip_jsonc(text: str) -> str:
    out: list[str] = []
    i = 0
    n = len(text)
    in_string = False
    escape = False
    in_line = False
    in_block = False
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if in_line:
            if ch in "\r\n":
                in_line = False
                out.append(ch)
            i += 1
            continue
        if in_block:
            if ch == "*" and nxt == "/":
                in_block = False
                i += 2
                continue
            i += 1
            continue
        if in_string:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and nxt == "/":
            in_line = True
            i += 2
            continue
        if ch == "/" and nxt == "*":
            in_block = True
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def load_settings(path: str | Path | None = None) -> dict:
    file = Path(path) if path else settings_path()
    if not file.is_file():
        return {}
    text = strip_jsonc(file.read_text(encoding="utf-8"))
    data = json.loads(text) if text.strip() else {}
    return data if isinstance(data, dict) else {}


def save_settings(data: dict, path: str | Path | None = None) -> Path:
    file = Path(path) if path else settings_path(writing=True)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return file


def get_institution(data: dict | None = None) -> str:
    return str((data or load_settings()).get(INSTITUTION_KEY) or "").strip()


def get_run_mode(data: dict | None = None) -> str:
    settings = data if data is not None else load_settings()
    text = str((settings or {}).get(RUN_MODE_KEY) or "").strip()
    if text.lower() == RUN_MODE_SIMPLE.lower():
        return RUN_MODE_SIMPLE
    return RUN_MODE_CLINIC


def is_simple_run_mode(data: dict | None = None) -> bool:
    """True when Open Case should pick a folder instead of a machine list.

    Simple mode if the settings file is missing, MACHINES is absent/empty, or
    ``RunMode`` is ``Simple``.
    """
    path = settings_path()
    if not path.is_file():
        return True
    settings = data if data is not None else load_settings()
    if not settings:
        return True
    if get_run_mode(settings) == RUN_MODE_SIMPLE:
        return True
    return not named_machines(settings)


def simple_machine_name(case_folder: str | Path) -> str:
    """Machine name from the parent of the case folder."""
    folder = Path(case_folder)
    parent = folder.parent
    return parent.name if parent.name else ""


_CASE_NAME_FORMATS = (
    "%Y%m%d_%H%M%S",
    "%Y%m%d-%H%M%S",
    "%Y-%m-%d_%H-%M-%S",
    "%y-%m-%d_%H-%M-%S",
    "%Y%m%d",
)
CASE_FOLDER_STAMP_FORMAT = "%Y%m%d_%H%M%S"
_CASE_FOLDER_RE = re.compile(r"^(\d{8})_(\d{6})(?:_[A-Za-z0-9]+)?$")


def parse_case_folder_stamp(name: str) -> datetime | None:
    """``YYYYMMDD_HHmmss`` or ``YYYYMMDD_HHmmss_OP``; also ``MMDDYYYY_HHmmss[_OP]``."""
    match = _CASE_FOLDER_RE.match(str(name or ""))
    if not match:
        return None
    date_s, time_s = match.group(1), match.group(2)
    for fmt in ("%Y%m%d_%H%M%S", "%m%d%Y_%H%M%S"):
        try:
            return datetime.strptime(f"{date_s}_{time_s}", fmt)
        except ValueError:
            continue
    return None


def is_case_folder_name(name: str) -> bool:
    """True for ``YYYYMMDD_HHmmss`` or ``YYYYMMDD_HHmmss_Operator``."""
    return parse_case_folder_stamp(name) is not None


def case_recency_key(folder: str | Path) -> tuple:
    """Newer case folders compare greater (use reverse=True for newest first)."""
    path = Path(folder)
    parsed = parse_case_folder_stamp(path.name)
    if parsed is not None:
        return (1, parsed.timestamp())
    for fmt in _CASE_NAME_FORMATS:
        try:
            return (1, datetime.strptime(path.name, fmt).timestamp())
        except ValueError:
            continue
    try:
        return (0, path.stat().st_mtime)
    except OSError:
        return (0, 0.0)


def list_case_folders(machine: dict | None) -> list[Path]:
    root = Path(str((machine or {}).get("cases_dir") or "")).expanduser()
    folders: list[Path] = []
    try:
        with os.scandir(root) as it:
            for entry in it:
                if entry.name.startswith("."):
                    continue
                if not is_case_folder_name(entry.name):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    folders.append(Path(entry.path))
    except OSError:
        return []
    folders.sort(key=case_recency_key, reverse=True)
    return folders


def get_machines(data: dict | None = None) -> list[dict]:
    machines = (data or load_settings()).get(MACHINES_KEY) or []
    if not isinstance(machines, list):
        return []
    return [m for m in machines if isinstance(m, dict)]


def named_machines(data: dict | None = None) -> list[dict]:
    out = []
    for machine in get_machines(data):
        name = str(machine.get("NAME") or "").strip()
        if name:
            out.append(machine)
    return out


def machine_by_name(name: str, data: dict | None = None) -> dict | None:
    want = (name or "").strip().lower()
    for machine in named_machines(data):
        if str(machine.get("NAME") or "").strip().lower() == want:
            return machine
    return None


def _string_list(value) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(n).strip() for n in value if str(n).strip()]
    text = str(value or "").strip()
    if not text:
        return []
    return [p.strip() for p in text.replace(";", ",").split(",") if p.strip()]


def patient_last_names(machine: dict | None) -> list[str]:
    return _string_list((machine or {}).get("patient_last_names"))


def station_names(machine: dict | None) -> list[str]:
    names = _string_list((machine or {}).get("station_names"))
    name = str((machine or {}).get("NAME") or "").strip()
    if name and name.lower() not in [n.lower() for n in names]:
        names.append(name)
    return names


def machine_by_station(station: str, data: dict | None = None) -> dict | None:
    key = (station or "").strip().lower()
    if not key:
        return None
    for machine in named_machines(data):
        aliases = [n.lower() for n in station_names(machine)]
        if key in aliases:
            return machine
    return None


def machine_by_patient_station(
    last_name: str,
    station: str,
    data: dict | None = None,
) -> dict | None:
    """C# ``{PatientLastName}_{StationName}`` → machine (e.g. ``mpc_ctsim``)."""
    last = (last_name or "").strip().lower()
    stat = (station or "").strip().lower()
    if not last or not stat:
        return None
    key = f"{last}_{stat}"
    for machine in named_machines(data):
        lasts = [n.lower() for n in patient_last_names(machine)] or [""]
        stations = [n.lower() for n in station_names(machine)]
        for ln in lasts:
            for st in stations:
                if ln and f"{ln}_{st}" == key:
                    return machine
        extras = _string_list(machine.get("patient_station_keys"))
        if key in [e.lower() for e in extras]:
            return machine
    return None


def is_under_directory(path: str | Path, root: str | Path, *, allow_root: bool = False) -> bool:
    """True when *path* is a subdirectory of *root* (optionally the root itself)."""
    try:
        child = Path(path).expanduser().resolve()
        parent = Path(root).expanduser().resolve()
    except OSError:
        return False
    if child == parent:
        return allow_root
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        pass
    child_s = os.path.normcase(str(child))
    parent_s = os.path.normcase(str(parent))
    sep = os.sep
    if not parent_s.endswith(sep):
        parent_s += sep
    return child_s.startswith(parent_s)


def default_machine(data: dict | None = None) -> dict | None:
    machines = named_machines(data)
    return machines[0] if machines else None


def machine_name(machine: dict | None) -> str:
    return str((machine or {}).get("NAME") or "").strip()


def machine_phantom(machine: dict | None) -> dict:
    """Optional ``phantom: {id, name}`` on a MACHINES entry (display / later logic)."""
    block = (machine or {}).get("phantom")
    return dict(block) if isinstance(block, dict) else {}


def phantom_id(machine: dict | None) -> str:
    return str(machine_phantom(machine).get("id") or "").strip()


def phantom_name(machine: dict | None) -> str:
    return str(machine_phantom(machine).get("name") or "").strip()


def machine_display_name(machine: dict | None) -> str:
    """Machine NAME, with phantom name when set: ``CTSim1 — Catphan 604``."""
    name = machine_name(machine)
    phantom = phantom_name(machine)
    if name and phantom:
        return f"{name} — {phantom}"
    return name or phantom


def watcher_settings(data: dict | None = None) -> dict:
    block = (data or load_settings()).get(WATCHER_KEY) or {}
    return block if isinstance(block, dict) else {}


def watcher_case_folder_regex(data: dict | None = None) -> str:
    """Watcher case-folder regex. Missing → ``MMDDYYYY_DailyQA``; empty → any name."""
    if data is None or (isinstance(data, dict) and WATCHER_KEY in data):
        block = watcher_settings(data)
    else:
        block = data if isinstance(data, dict) else {}
    if CASE_FOLDER_NAME_REGEX_KEY in block:
        return str(block.get(CASE_FOLDER_NAME_REGEX_KEY) or "")
    if "case_folder_name_regex" in block:
        return str(block.get("case_folder_name_regex") or "")
    return DEFAULT_CASE_FOLDER_REGEX


def elastix_settings(data: dict | None = None) -> dict:
    settings = data if data is not None else load_settings()
    block = settings.get(ELASTIX_KEY) or {}
    return block if isinstance(block, dict) else {}


def elastix_dir_setting(data: dict | None = None) -> str:
    settings = data if data is not None else load_settings()
    folder = str(elastix_settings(settings).get("elastix_dir") or "").strip()
    if folder:
        return folder
    return str(settings.get("elastix_dir") or "").strip()


def notifications(data: dict | None = None) -> dict:
    block = (data or load_settings()).get(NOTIFICATIONS_KEY) or {}
    return block if isinstance(block, dict) else {}


def email_settings(data: dict | None = None) -> dict:
    block = notifications(data).get("email") or {}
    return block if isinstance(block, dict) else {}


def chat_webhook_urls(data: dict | None = None) -> dict[str, str]:
    notes = notifications(data)
    out: dict[str, str] = {}
    for name in CHAT_CHANNELS:
        block = notes.get(name) or {}
        url = ""
        if isinstance(block, dict):
            url = str(block.get("webhook_url") or "").strip()
        elif isinstance(block, str):
            url = block.strip()
        out[name] = url
    return out


def mask_count(machine: dict, key: str) -> int:
    field = f"num_of_{key}_masks"
    try:
        return int(machine.get(field) or 0)
    except (TypeError, ValueError):
        return 0


def mask_stems(machine: dict) -> list[str]:
    stems: list[str] = []
    for key, _field in MASK_GROUPS:
        n = mask_count(machine, key)
        for i in range(1, n + 1):
            stems.append(f"{key}{i}")
    return stems


def label_map(machine: dict) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for index, stem in enumerate(mask_stems(machine), start=1):
        mapping[index] = stem
    return mapping


def default_docuforms2_ctqa_step() -> dict:
    """Clinic DocuForms2 upload (upload_ctqa input.json)."""
    return {
        "type": DOCUFORMS2_CTQA_TYPE,
        "enabled": True,
        "backend_url": "https://docuforms.example.edu:9001",
        "verify_ssl": False,
        "dry_run": False,
        "attach_dcm_zip": True,
        "attach_pdf": True,
        "resubmit": False,
        "timeout_sec": 300,
        "form_ids": [],
        "email_success_event_to": [],
        "email_failure_event_to": [],
    }


def normalize_form_ids(value) -> list[dict]:
    """Normalize a machine→form_id list to ``[{"machine": name, "form_id": id}, ...]``."""
    items: list = []
    if isinstance(value, dict):
        if "machine" in value or "form_id" in value or "NAME" in value:
            items = [value]
        else:
            items = [{"machine": key, "form_id": val} for key, val in value.items()]
    elif isinstance(value, list):
        items = value
    out: list[dict] = []
    for item in items:
        machine = ""
        form_id = ""
        if isinstance(item, dict):
            machine = str(item.get("machine") or item.get("NAME") or item.get("name") or "").strip()
            form_id = str(item.get("form_id") or item.get("docuforms2_form_id") or "").strip()
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            machine = str(item[0] or "").strip()
            form_id = str(item[1] or "").strip()
        if not machine or not form_id:
            continue
        key = machine.lower()
        out = [row for row in out if row["machine"].lower() != key]
        out.append({"machine": machine, "form_id": form_id})
    return out


def form_ids_from_step(step: dict | None) -> list[dict]:
    step = step or {}
    return normalize_form_ids(step.get("form_ids") or step.get("form_id_map"))


def form_ids_from_machines(machines) -> list[dict]:
    rows: list[dict] = []
    if not isinstance(machines, list):
        return rows
    for machine in machines:
        if not isinstance(machine, dict):
            continue
        name = str(machine.get("NAME") or "").strip()
        form_id = str(machine.get("docuforms2_form_id") or "").strip()
        if name and form_id:
            rows.append({"machine": name, "form_id": form_id})
    return normalize_form_ids(rows)


def form_id_for_machine(step: dict | None, machine_cfg: dict | None) -> str:
    """DocuForms2 form id for this machine from PostProcessing.form_ids, else legacy machine key."""
    name = str((machine_cfg or {}).get("NAME") or "").strip()
    for row in form_ids_from_step(step):
        if row["machine"].lower() == name.lower():
            return row["form_id"]
    return str((machine_cfg or {}).get("docuforms2_form_id") or "").strip()


def format_form_ids(value) -> str:
    return "\n".join(
        f"{row['machine']} = {row['form_id']}" for row in normalize_form_ids(value)
    )


def parse_form_ids_text(text) -> list[dict]:
    rows: list[dict] = []
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            left, right = line.split("=", 1)
        elif ":" in line:
            left, right = line.split(":", 1)
        else:
            parts = line.split(None, 1)
            if len(parts) < 2:
                continue
            left, right = parts
        machine = left.strip()
        form_id = right.strip()
        if machine and form_id:
            rows.append({"machine": machine, "form_id": form_id})
    return normalize_form_ids(rows)


def post_processing_steps(data: dict | None = None) -> list[dict]:
    settings = data if data is not None else load_settings()
    raw = (settings or {}).get(POST_PROCESSING_KEY)
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return [step for step in raw if isinstance(step, dict) and str(step.get("type") or "").strip()]


def find_post_step(type_name: str, data: dict | None = None) -> dict:
    wanted = str(type_name or "").strip()
    for step in post_processing_steps(data):
        if str(step.get("type") or "").strip() == wanted:
            return dict(step)
    return {}


def upsert_post_step(steps: list, step: dict) -> list[dict]:
    kind = str((step or {}).get("type") or "").strip()
    out: list[dict] = []
    found = False
    for existing in steps or []:
        if not isinstance(existing, dict):
            continue
        if str(existing.get("type") or "").strip() == kind:
            out.append(dict(step))
            found = True
        else:
            out.append(dict(existing))
    if not found and kind:
        out.append(dict(step))
    return out
