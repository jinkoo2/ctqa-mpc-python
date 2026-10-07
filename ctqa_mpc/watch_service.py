"""Install CTQA-MPC watch as a Windows service via NSSM."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .app_settings import SETTINGS_NAME, app_dir, settings_path
from .identity import users_dir

DEFAULT_SERVICE_NAME = "CTQAMPC"
DEFAULT_DISPLAY_NAME = "CTQA-MPC"
APP_EXE_STEMS = ("CTQAMPC", "CTQA-MPC", "ctqa-mpc")
SOURCE_APP_PARAMETERS = "-u -m ctqa_mpc watch"
FROZEN_APP_PARAMETERS = "--mode service"
APP_PARAMETERS = SOURCE_APP_PARAMETERS
ENV_SETTINGS = "CTQA_MPC_SETTINGS"
ENV_USERS = "CTQA_MPC_USERS_DIR"


@dataclass
class WatchServicePlan:
    service_name: str = DEFAULT_SERVICE_NAME
    display_name: str = DEFAULT_DISPLAY_NAME
    nssm_exe: str = ""
    program_exe: str = ""
    app_parameters: str = ""
    app_directory: str = ""
    settings_file: str = ""
    users_folder: str = ""
    account: str = ""
    password: str = ""
    start_after: bool = True
    replace_existing: bool = True


def is_windows() -> bool:
    return sys.platform == "win32"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def is_admin() -> bool:
    if not is_windows():
        return False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _exe_suffix() -> str:
    return ".exe" if os.name == "nt" else ""


def program_basename(program_exe: str) -> str:
    text = str(program_exe or "").replace("\\", "/").rstrip("/")
    return Path(text).name.lower()


def is_packaged_exe(program_exe: str) -> bool:
    name = program_basename(program_exe)
    stem = name[:-4] if name.endswith(".exe") else name
    return any(stem.startswith(s.lower()) for s in APP_EXE_STEMS)


def default_app_parameters(program_exe: str = "") -> str:
    name = program_basename(program_exe)
    if name.startswith("python"):
        return SOURCE_APP_PARAMETERS
    if is_frozen() or is_packaged_exe(program_exe):
        return FROZEN_APP_PARAMETERS
    return SOURCE_APP_PARAMETERS


def find_packaged_exe(folder: Path | None = None) -> str:
    folder = folder or app_dir()
    suffix = _exe_suffix()
    exact: list[Path] = []
    for stem in APP_EXE_STEMS:
        exact.extend(
            [
                folder / f"{stem}{suffix}",
                folder / stem,
                folder / f"{stem}.exe",
            ]
        )
    seen: set[Path] = set()
    for path in exact:
        if path in seen:
            continue
        seen.add(path)
        if path.is_file():
            return str(path)
    matches: list[Path] = []
    for stem in APP_EXE_STEMS:
        matches.extend(path for path in folder.glob(f"{stem}-*") if path.is_file())
    if matches:
        return str(max(matches, key=lambda path: path.stat().st_mtime))
    return ""


def default_program_exe() -> str:
    if is_frozen():
        here = Path(sys.executable).resolve()
        if here.is_file():
            return str(here)
        return find_packaged_exe(here.parent)
    exe = Path(sys.executable)
    if exe.is_file():
        return str(exe)
    found = shutil.which("python")
    return found or ""


def default_account() -> str:
    domain = str(os.environ.get("USERDOMAIN") or "").strip()
    user = str(os.environ.get("USERNAME") or "").strip()
    if domain and user and domain.upper() not in ("", user.upper()):
        return f"{domain}\\{user}"
    return user


def find_nssm() -> str:
    found = shutil.which("nssm") or shutil.which("nssm.exe")
    if found:
        return found
    roots = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
        Path(r"C:\nssm"),
        app_dir(),
        app_dir() / "nssm",
    ]
    names = [
        Path("nssm.exe"),
        Path("win64") / "nssm.exe",
        Path("win32") / "nssm.exe",
        Path("nssm") / "win64" / "nssm.exe",
        Path("nssm") / "nssm.exe",
    ]
    for root in roots:
        for name in names:
            path = root / name
            if path.is_file():
                return str(path)
    return ""


def default_plan() -> WatchServicePlan:
    app = app_dir()
    settings = settings_path()
    program = default_program_exe()
    return WatchServicePlan(
        nssm_exe=find_nssm(),
        program_exe=program,
        app_parameters=default_app_parameters(program),
        app_directory=str(app),
        settings_file=str(settings if settings.is_file() else app / SETTINGS_NAME),
        users_folder=str(users_dir()),
        account=default_account(),
    )


def app_environment_extra(plan: WatchServicePlan) -> str:
    lines: list[str] = []
    settings = plan.settings_file.strip()
    if settings:
        lines.append(f"{ENV_SETTINGS}={settings}")
    users = plan.users_folder.strip()
    if users:
        lines.append(f"{ENV_USERS}={users}")
    return "\n".join(lines)


def log_paths(app_directory: str) -> tuple[str, str]:
    logs = Path(app_directory) / "_logs"
    return str(logs / "watch_stdout.log"), str(logs / "watch_stderr.log")


def nssm_commands(plan: WatchServicePlan) -> list[list[str]]:
    name = plan.service_name.strip() or DEFAULT_SERVICE_NAME
    nssm = plan.nssm_exe.strip() or "nssm"
    program_exe = plan.program_exe.strip()
    app_directory = plan.app_directory.strip()
    parameters = plan.app_parameters.strip()
    if not parameters:
        parameters = default_app_parameters(program_exe)
    stdout_log, stderr_log = log_paths(app_directory)
    extra_env = app_environment_extra(plan)
    commands: list[list[str]] = []
    if plan.replace_existing:
        commands.append([nssm, "stop", name])
        commands.append([nssm, "remove", name, "confirm"])
    commands.append([nssm, "install", name, program_exe])
    commands.append([nssm, "set", name, "AppDirectory", app_directory])
    if parameters:
        commands.append([nssm, "set", name, "AppParameters", parameters])
    if extra_env:
        commands.append([nssm, "set", name, "AppEnvironmentExtra", extra_env])
    commands.append(
        [nssm, "set", name, "DisplayName", plan.display_name.strip() or DEFAULT_DISPLAY_NAME]
    )
    commands.append([nssm, "set", name, "Start", "SERVICE_AUTO_START"])
    commands.append([nssm, "set", name, "AppStdout", stdout_log])
    commands.append([nssm, "set", name, "AppStderr", stderr_log])
    commands.append([nssm, "set", name, "AppRotateFiles", "1"])
    account = plan.account.strip()
    if account:
        commands.append([nssm, "set", name, "ObjectName", account, plan.password])
    if plan.start_after:
        commands.append([nssm, "start", name])
    return commands


def format_nssm_commands(plan: WatchServicePlan) -> str:
    lines: list[str] = []
    for args in nssm_commands(plan):
        shown = list(args)
        if len(shown) >= 5 and shown[3].lower() == "objectname":
            shown[-1] = "<password>"
        quoted = []
        for part in shown:
            if any(ch.isspace() for ch in part) or not part:
                quoted.append(f'"{part}"')
            else:
                quoted.append(part)
        lines.append(" ".join(quoted))
    return "\n".join(lines)


def _run(args: list[str], timeout: float = 60) -> subprocess.CompletedProcess:
    kwargs: dict = {
        "capture_output": True,
        "text": True,
        "timeout": timeout,
    }
    if is_windows():
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.run(args, **kwargs)


def install_watch_service(plan: WatchServicePlan) -> tuple[bool, str]:
    nssm = Path(plan.nssm_exe.strip())
    if not nssm.is_file():
        return False, "nssm.exe was not found. Install NSSM from https://nssm.cc and try again."
    program_exe = Path(plan.program_exe.strip())
    if not program_exe.is_file():
        return False, f"Program was not found:\n{program_exe}"
    app_directory = Path(plan.app_directory.strip())
    if not app_directory.is_dir():
        return False, f"App directory was not found:\n{app_directory}"
    settings_file = Path(plan.settings_file.strip())
    if not settings_file.is_file():
        return False, f"Settings file was not found:\n{settings_file}"
    users_text = plan.users_folder.strip()
    if not users_text:
        return False, "Users folder was not set."
    users_folder = Path(users_text)
    try:
        users_folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, f"Could not create users folder {users_folder}: {exc}"
    if not users_folder.is_dir():
        return False, f"Users folder was not found:\n{users_folder}"
    logs = app_directory / "_logs"
    try:
        logs.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, f"Could not create log folder {logs}: {exc}"

    chunks: list[str] = []
    commands = nssm_commands(plan)
    for args in commands:
        display = list(args)
        if len(display) >= 5 and display[3].lower() == "objectname":
            display[-1] = "<password>"
        label = " ".join(display)
        try:
            proc = _run(args)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"{label}\n{exc}"
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        removing = len(args) >= 2 and args[1].lower() == "remove"
        stopping = len(args) >= 2 and args[1].lower() == "stop"
        if proc.returncode != 0 and not (plan.replace_existing and (removing or stopping)):
            detail = err or out or f"exit {proc.returncode}"
            chunks.append(f"{label}\n{detail}")
            return False, "\n\n".join(chunks)
        if out or err:
            chunks.append(f"{label}\n{(out or err)}")
        else:
            chunks.append(label)
    return True, "\n".join(chunks)
