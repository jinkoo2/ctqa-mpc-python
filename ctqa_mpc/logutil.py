"""App file logging next to the executable: ``_logs/ctqa_mpc_YYYY-MM-DD.log``."""

from __future__ import annotations

import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from .app_settings import app_dir

LOGS_DIR_NAME = "_logs"
LOG_NAME_PREFIX = "ctqa_mpc_"
LOG_NAME_SUFFIX = ".log"
KEEP_DAYS = 7
_FILE_HANDLER_MARK = "_ctqa_mpc_daily_log"
_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def logs_dir() -> Path:
    return app_dir() / LOGS_DIR_NAME


def log_path_for(day: date | None = None) -> Path:
    day = day or date.today()
    return logs_dir() / f"{LOG_NAME_PREFIX}{day.isoformat()}{LOG_NAME_SUFFIX}"


def _parse_log_date(path: Path) -> date | None:
    name = path.name
    if not name.startswith(LOG_NAME_PREFIX) or not name.endswith(LOG_NAME_SUFFIX):
        return None
    stamp = name[len(LOG_NAME_PREFIX) : -len(LOG_NAME_SUFFIX)]
    try:
        return date.fromisoformat(stamp)
    except ValueError:
        return None


def prune_old_logs(folder: Path, *, today: date | None = None) -> None:
    today = today or date.today()
    cutoff = today - timedelta(days=KEEP_DAYS)
    try:
        paths = list(folder.glob(f"{LOG_NAME_PREFIX}*{LOG_NAME_SUFFIX}"))
    except OSError:
        return
    for path in paths:
        if not path.is_file():
            continue
        parsed = _parse_log_date(path)
        if parsed is None or parsed >= cutoff:
            continue
        try:
            path.unlink()
        except OSError:
            continue


def _level_from_env() -> int | None:
    text = os.environ.get("CTQA_MPC_LOG_LEVEL", "").strip().upper()
    if not text:
        return None
    mapping = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }
    return mapping.get(text)


def _has_our_file_handler(root: logging.Logger) -> bool:
    return any(getattr(handler, _FILE_HANDLER_MARK, False) for handler in root.handlers)


def _has_stream_handler(root: logging.Logger) -> bool:
    for handler in root.handlers:
        if getattr(handler, _FILE_HANDLER_MARK, False):
            continue
        if isinstance(handler, logging.StreamHandler) and not isinstance(
            handler, logging.FileHandler
        ):
            return True
    return False


def stream_isatty(stream) -> bool:
    check = getattr(stream, "isatty", None)
    if check is None:
        return False
    try:
        return bool(check())
    except Exception:
        return False


def windows_command_line_argv() -> list[str]:
    """Windows process argv from GetCommandLineW (not Python/PyInstaller sys.argv)."""
    if sys.platform != "win32":
        return []
    try:
        import ctypes
        from ctypes import wintypes

        GetCommandLineW = ctypes.windll.kernel32.GetCommandLineW
        CommandLineToArgvW = ctypes.windll.shell32.CommandLineToArgvW
        LocalFree = ctypes.windll.kernel32.LocalFree
        GetCommandLineW.restype = ctypes.c_wchar_p
        CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
        CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
        argc = ctypes.c_int(0)
        argv_p = CommandLineToArgvW(GetCommandLineW(), ctypes.byref(argc))
        if not argv_p:
            return []
        out = [argv_p[i] for i in range(argc.value)]
        LocalFree(argv_p)
        return out
    except Exception:
        return []


def _rebind_stdio_to_console() -> None:
    out = open("CONOUT$", "w", encoding="utf-8", buffering=1, errors="replace")
    err = open("CONOUT$", "w", encoding="utf-8", buffering=1, errors="replace")
    sys.stdout = out
    sys.stderr = err
    sys.__stdout__ = out
    sys.__stderr__ = err


def attach_console_if_needed(*, alloc: bool = False) -> str:
    """Attach stdout to a console so windowed frozen apps can print ``--help``.

    Returns ``existing``, ``parent``, ``alloc``, or ``""``.
    """
    if sys.platform != "win32":
        return "existing" if stream_isatty(sys.stdout) or stream_isatty(sys.stderr) else ""
    if stream_isatty(sys.stdout) or stream_isatty(sys.stderr):
        return "existing"
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.GetConsoleWindow.restype = ctypes.c_void_p
        if kernel32.GetConsoleWindow():
            _rebind_stdio_to_console()
            return "existing"
        if kernel32.AttachConsole(0xFFFFFFFF):
            _rebind_stdio_to_console()
            return "parent"
        if alloc and kernel32.AllocConsole():
            _rebind_stdio_to_console()
            return "alloc"
    except Exception:
        return ""
    return ""


def finish_console_help(kind: str) -> None:
    """Keep an allocated console visible; flush after parent-console help."""
    try:
        if sys.stdout is not None:
            sys.stdout.flush()
        if sys.stderr is not None:
            sys.stderr.flush()
    except Exception:
        pass
    if kind != "alloc":
        return
    try:
        print("Press Enter to close...", flush=True)
        input()
    except Exception:
        pass


def ensure_stdio() -> None:
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8", errors="replace")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8", errors="replace")


def configure_logging(*, verbose: bool = False, console: bool | None = None) -> None:
    ensure_stdio()
    env_level = _level_from_env()
    if verbose:
        level = logging.DEBUG
    elif env_level is not None:
        level = env_level
    else:
        level = logging.INFO

    root = logging.getLogger()
    root.setLevel(min(root.level, level) if root.handlers else level)
    if verbose:
        root.setLevel(logging.DEBUG)

    formatter = logging.Formatter(_FORMAT)

    if console is None:
        frozen = bool(getattr(sys, "frozen", False))
        console = (not frozen) or stream_isatty(sys.stderr) or stream_isatty(sys.stdout)
    stream_obj = sys.stderr or sys.stdout
    if console and stream_obj is not None and not _has_stream_handler(root):
        stream = logging.StreamHandler(stream_obj)
        stream.setFormatter(formatter)
        stream.setLevel(level)
        root.addHandler(stream)

    if not _has_our_file_handler(root):
        try:
            folder = logs_dir()
            folder.mkdir(parents=True, exist_ok=True)
            prune_old_logs(folder)
            path = log_path_for()
            handler = logging.FileHandler(path, encoding="utf-8")
            setattr(handler, _FILE_HANDLER_MARK, True)
            handler.setFormatter(formatter)
            handler.setLevel(level)
            root.addHandler(handler)
        except Exception:
            pass
    try:
        from .emailer import install_error_email_hooks

        install_error_email_hooks()
    except Exception:
        pass
