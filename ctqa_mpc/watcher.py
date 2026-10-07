"""Watch MPC import folders and run the geometry pipeline."""

from __future__ import annotations

import logging
import re
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .app_settings import (
    load_settings,
    machine_by_patient_station,
    machine_by_station,
    watcher_case_folder_regex,
    watcher_settings,
)
from .dicom_io import (
    case_stamp_from_info,
    dicom_series_to_mha,
    parse_dicom_person_name,
    series_dirs,
    sort_files_by_patient_study_series,
)
from .emailer import send_event, send_error_email
from .param import Param
from .pipeline import copy_tree_files, run_case
from .image_io import scratch_parent

logger = logging.getLogger(__name__)

class WatchPathUnavailable(RuntimeError):
    pass


def _file_count(folder: Path) -> int:
    try:
        return sum(1 for p in folder.iterdir() if p.is_file())
    except OSError:
        return 0


def wait_for_files(folder: Path, min_files: int, poll_sec: float, max_cycles: int) -> bool:
    last = -1
    for _cycle in range(max_cycles + 1):
        n = _file_count(folder)
        logger.info("%s has %s files (need %s)", folder, n, min_files)
        if n >= min_files and n == last:
            return True
        last = n
        time.sleep(poll_sec)
    return _file_count(folder) >= min_files


def process_import_dir(
    import_dir: Path,
    settings: dict,
    *,
    send_email: bool = True,
    strict: bool = False,
) -> list[tuple[Path, dict]]:
    """Sort DICOM, analyze, email, and copy finished cases to ``cases_dir``.

    Returns ``(published_case_dir, machine)`` pairs. ``strict=True`` raises instead
    of skipping a series, and raises if nothing is published.
    """
    cfg = watcher_settings(settings)
    sort_base = scratch_parent()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    sort_dir = sort_base / f"ctqa_sort_{stamp}"
    sort_dir.mkdir(parents=True, exist_ok=True)
    published: list[tuple[Path, dict]] = []
    skipped: list[str] = []
    try:
        logger.info("sorting DICOM from %s -> %s", import_dir, sort_dir)
        sort_files_by_patient_study_series(import_dir, sort_dir, delete_source_files=False)
        min_series = int(cfg.get("min_series_dicom_files") or 100)
        series_list = series_dirs(sort_dir)
        if not series_list:
            msg = f"no DICOM series found in {import_dir}"
            if strict:
                raise RuntimeError(msg)
            logger.info(msg)
            return published
        for series in series_list:
            dcms = list(series.glob("*.dcm"))
            if not dcms:
                dcms = [p for p in series.iterdir() if p.is_file()]
            if len(dcms) < min_series:
                msg = f"{series.name} has {len(dcms)} files (need {min_series})"
                logger.info("skip series %s", msg)
                skipped.append(msg)
                continue
            dicom_series_to_mha(series, series)
            info = Param(series / "info.txt")
            station = info.get_value("StationName")
            last_name, _first = parse_dicom_person_name(info.get_value("PatientName"))
            machine = machine_by_patient_station(last_name, station, settings)
            if machine is None:
                msg = f"no machine for PatientLastName={last_name!r} StationName={station!r}"
                if strict:
                    raise RuntimeError(msg)
                logger.error(msg)
                skipped.append(msg)
                continue
            cases_dir = Path(str(machine.get("cases_dir") or ""))
            dest = cases_dir / case_stamp_from_info(series / "info.txt")
            logger.info("running CTQA locally in %s", series)
            run_case(
                series,
                machine_name=str(machine.get("NAME") or ""),
                send_email=send_email,
                data=settings,
            )
            logger.info("copying finished case to %s", dest)
            copy_tree_files(series, dest)
            published.append((dest, machine))
            logger.info("published case %s", dest)
        if not published:
            detail = "; ".join(skipped) if skipped else "no series met min_series_dicom_files"
            msg = f"no case published from {import_dir}: {detail}"
            if strict:
                raise RuntimeError(msg)
            logger.info("%s; keeping local work dir %s", msg, sort_dir)
            return published
        shutil.rmtree(sort_dir, ignore_errors=True)
        return published
    except Exception:
        logger.info("keeping local work dir after failure: %s", sort_dir)
        raise


def case_folder_matches(name: str, regex: str | None) -> bool:
    pattern = str(regex or "").strip()
    if not pattern:
        return True
    try:
        return re.fullmatch(pattern, name) is not None
    except re.error:
        logger.warning("invalid CASE_FOLDER_NAME_REGEX %r", pattern)
        return False


def _should_handle(folder: Path, regex: str) -> bool:
    return case_folder_matches(folder.name, regex)


def watch(watch_path: str = "", data: dict | None = None) -> None:
    settings = data if data is not None else load_settings()
    cfg = watcher_settings(settings)
    path = Path(watch_path or cfg.get("watch_path") or "")
    if not path.is_dir():
        raise WatchPathUnavailable(f"watch_path not found: {path}")
    case_regex = watcher_case_folder_regex(settings)
    min_files = int(cfg.get("min_num_of_files") or 401)
    poll = float(cfg.get("queued_case_poll_sec") or 10.0)
    max_cycles = int(cfg.get("max_wait_cycles") or 180)
    scan_sec = float(cfg.get("disk_scan_for_new_case_detection_sec") or 60.0)
    do_scan = bool(cfg.get("disk_scan_for_new_case_detection", True))
    seen: set[str] = set()
    lock = threading.Lock()

    def handle(folder: Path) -> None:
        key = str(folder.resolve())
        with lock:
            if key in seen:
                return
            seen.add(key)
        try:
            if not _should_handle(folder, case_regex):
                logger.info("not a case folder (%s), skip: %s", case_regex or "any", folder)
                return
            send_event("CTQA-MPC DailyQA folder", str(folder), settings)
            if not wait_for_files(folder, min_files, poll, max_cycles):
                send_error_email("waited enough for DailyQA files", str(folder), context="watcher")
                return
            process_import_dir(folder, settings)
            send_event("CTQA-MPC case done", str(folder), settings)
        except Exception:
            logger.exception("failed processing %s", folder)

    class Handler(FileSystemEventHandler):
        def on_created(self, event):
            folder = Path(event.src_path)
            if folder.is_dir() or event.is_directory:
                threading.Thread(target=handle, args=(Path(event.src_path),), daemon=True).start()

        def on_moved(self, event):
            dest = Path(getattr(event, "dest_path", event.src_path))
            if dest.is_dir() or event.is_directory:
                threading.Thread(target=handle, args=(dest,), daemon=True).start()

    send_event("CTQA-MPC watcher starting", str(path), settings)
    try:
        for child in path.iterdir():
            if child.is_dir():
                seen.add(str(child.resolve()))
    except OSError:
        pass
    observer = Observer()
    observer.schedule(Handler(), str(path), recursive=False)
    observer.start()
    logger.info("watching %s", path)
    try:
        while True:
            if do_scan:
                try:
                    for child in path.iterdir():
                        if child.is_dir() and _should_handle(child, case_regex):
                            threading.Thread(target=handle, args=(child,), daemon=True).start()
                except OSError:
                    logger.exception("disk scan failed for %s", path)
            time.sleep(max(scan_sec, poll))
    except KeyboardInterrupt:
        send_event("CTQA-MPC watcher stopping", str(path), settings)
        observer.stop()
    observer.join()
