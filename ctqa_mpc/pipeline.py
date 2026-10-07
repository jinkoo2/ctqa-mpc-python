"""One-case MPC pipeline: convert CT, find markers, compare to baseline, report."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from .analysis import analyze, analysis_dir, load_analysis_result, write_case_result
from .app_settings import default_machine, load_settings, machine_by_name
from .dicom_io import ensure_ct_mha
from .image_io import find_image, read_image
from .report import write_report

logger = logging.getLogger(__name__)


def copy_tree_files(src: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for path in src.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(src)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.resolve() == path.resolve():
            continue
        shutil.copy2(path, target)


def ensure_baseline_result(baseline: Path, machine: dict) -> Path:
    dest = analysis_dir(baseline)
    if load_analysis_result(dest).get("points"):
        return dest
    ct_path = find_image(baseline, "CT") or find_image(dest, "CT")
    if ct_path is None:
        ct_path = ensure_ct_mha(baseline)
    out = dest if dest.is_dir() else baseline / "results"
    analyze(read_image(ct_path), out, machine)
    return out


def run_case(
    case_dir: str | Path,
    *,
    machine_name: str = "",
    send_email: bool = False,
    data: dict | None = None,
) -> Path:
    settings = data if data is not None else load_settings()
    machine = machine_by_name(machine_name, settings) if machine_name else default_machine(settings)
    if machine is None:
        raise RuntimeError("no machine in settings.json (MACHINES)")
    case = Path(case_dir)
    baseline = Path(str(machine.get("baseline_dir") or ""))
    if not baseline.is_dir():
        raise FileNotFoundError(f"baseline_dir not found: {baseline}")

    ct_path = ensure_ct_mha(case)
    ensure_baseline_result(baseline, machine)
    result_dir = case / "results"
    analyze(read_image(ct_path), result_dir, machine)
    write_case_result(result_dir, baseline, machine)
    report = write_report(case, baseline, result_dir, machine)
    if send_email:
        from .emailer import send_report_file

        send_report_file(
            report,
            str(machine.get("NAME") or "CTQA-MPC"),
            extra_to=machine.get("new_case_email_to"),
            data=settings,
        )
    try:
        from .postprocess import run_post_processing

        run_post_processing(case, machine, data=settings)
    except Exception:
        logger.exception("post-processing failed for %s", case)
    from .temp_cleanup import start_post_analysis_cleanup

    start_post_analysis_cleanup(settings)
    logger.info("case complete: %s", case)
    return report
