"""Remove old CTQA-MPC scratch folders from the system temp directory."""

from __future__ import annotations

import logging
import shutil
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from .app_settings import temp_cleanup_settings
from .image_io import scratch_parent

logger = logging.getLogger(__name__)

DIR_PREFIXES = ("ctqa_sort_",)
FILE_PREFIXES: tuple[str, ...] = ()


@dataclass
class CleanupResult:
    removed: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)


def cleanup_scratch(
    root: str | Path | None = None,
    *,
    older_than_days: int = 7,
    now: datetime | None = None,
    dir_prefixes: tuple[str, ...] = DIR_PREFIXES,
    file_prefixes: tuple[str, ...] = FILE_PREFIXES,
) -> CleanupResult:
    now = now or datetime.now()
    cutoff = now - timedelta(days=max(1, int(older_than_days)))
    folder = Path(root) if root is not None else scratch_parent()
    result = CleanupResult()
    try:
        entries = list(folder.iterdir())
    except OSError as exc:
        logger.warning("temp cleanup cannot list %s: %s", folder, exc)
        return result
    for path in entries:
        name = path.name
        try:
            is_dir = path.is_dir()
        except OSError as exc:
            result.errors.append((str(path), str(exc)))
            continue
        prefixes = dir_prefixes if is_dir else file_prefixes
        if not any(name.startswith(prefix) for prefix in prefixes):
            continue
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime)
        except OSError as exc:
            result.errors.append((str(path), str(exc)))
            continue
        if mtime > cutoff:
            result.skipped.append((str(path), "newer than cutoff"))
            continue
        try:
            if is_dir:
                shutil.rmtree(path)
            else:
                path.unlink()
            result.removed.append(str(path))
            logger.info("removed old temp %s", path)
        except OSError as exc:
            result.errors.append((str(path), str(exc)))
            logger.warning("temp cleanup failed %s: %s", path, exc)
    return result


def run_temp_cleanup(
    data: dict | None = None,
    *,
    root: str | Path | None = None,
    now: datetime | None = None,
) -> CleanupResult:
    cfg = temp_cleanup_settings(data)
    return cleanup_scratch(root, older_than_days=cfg["older_than_days"], now=now)


def start_post_analysis_cleanup(data: dict | None = None, *, root: str | Path | None = None) -> threading.Thread:
    """Delete old scratch folders after analysis; does not block the caller."""

    def work() -> None:
        try:
            result = run_temp_cleanup(data, root=root)
            logger.info(
                "post-analysis temp cleanup removed=%s skipped=%s errors=%s",
                len(result.removed),
                len(result.skipped),
                len(result.errors),
            )
        except Exception:
            logger.exception("post-analysis temp cleanup failed")

    thread = threading.Thread(target=work, name="ctqa-temp-cleanup", daemon=True)
    thread.start()
    return thread
