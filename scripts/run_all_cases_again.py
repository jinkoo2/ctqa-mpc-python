#!/usr/bin/env python3
"""Reprocess existing MPC case folders with the current ctqa_mpc pipeline.

For each case folder under a machine's ``cases_dir`` (cases that were, say, only
ever processed by the legacy C# app and so have no ``results/`` folder yet --
or that you simply want to rerun with the current code), this:

  1. Converts/locates the case's CT image (same as ``python -m ctqa_mpc analyze``).
  2. Runs the full analysis pipeline, writing ``<case>/results/``
     (report.html, result.json, csvs, etc.).
  3. Runs the configured PostProcessing steps (e.g. pushes the report to
     DocuForms2), exactly as a normal single-case run would.

It is the same ``run_case()`` used by ``python -m ctqa_mpc analyze`` and the
folder watcher, just looped over every case folder found, with a few batch-run
conveniences (resume, dry-run, skip-upload, limit).

Usage:
    python scripts/run_all_cases_again.py
    python scripts/run_all_cases_again.py --list
    python scripts/run_all_cases_again.py --machine GECTSH --limit 3 --no-upload
    python scripts/run_all_cases_again.py --cases-dir "D:\\...\\cases" --resubmit
    python scripts/run_all_cases_again.py --skip-existing   # resume an interrupted run

By default this uses the machine and ``cases_dir`` from the app's own
settings.json (same file the GUI/watcher use) and pushes to DocuForms2
exactly as configured there (PostProcessing / resubmit / dry_run). Use
--settings/--machine/--cases-dir to point elsewhere, e.g. when running this
from a different PC than usual.
"""

from __future__ import annotations

import argparse
import copy
import logging
import sys
import time
from pathlib import Path

# Allow running this straight from a source checkout (``python scripts/run_all_cases_again.py``)
# without the package being pip-installed first.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from ctqa_mpc.app_settings import (  # noqa: E402
    DOCUFORMS2_CTQA_TYPE,
    DOCUFORMS2_MPC_TYPE,
    POST_PROCESSING_KEY,
    default_machine,
    list_case_folders,
    load_settings,
    machine_by_name,
)
from ctqa_mpc.logutil import configure_logging  # noqa: E402

logger = logging.getLogger("run_all_cases_again")

_DOCUFORMS2_STEP_TYPES = {DOCUFORMS2_MPC_TYPE, DOCUFORMS2_CTQA_TYPE}


def _prepare_settings(raw_settings: dict, *, resubmit: bool, dry_run: bool, no_upload: bool) -> dict:
    """Deep-copy settings and apply this run's CLI overrides to the DocuForms2
    PostProcessing step(s) only -- everything else in settings.json is untouched."""
    data = copy.deepcopy(raw_settings)
    if no_upload:
        data[POST_PROCESSING_KEY] = []
        return data

    steps = data.get(POST_PROCESSING_KEY)
    if isinstance(steps, dict):
        steps = [steps]
    if not isinstance(steps, list):
        return data

    for step in steps:
        if not isinstance(step, dict):
            continue
        if str(step.get("type") or "").strip() not in _DOCUFORMS2_STEP_TYPES:
            continue
        if resubmit:
            step["resubmit"] = True
        if dry_run:
            step["dry_run"] = True
    data[POST_PROCESSING_KEY] = steps
    return data


def _has_results(case_dir: Path) -> bool:
    return (case_dir / "results" / "report.html").is_file()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Rerun the current ctqa_mpc analysis pipeline on existing case folders "
            "(e.g. cases only processed so far by the legacy C# app), writing a "
            "results/ folder in each and running PostProcessing (DocuForms2 upload)."
        )
    )
    parser.add_argument(
        "--settings", "-s", metavar="FILE",
        help="path to settings.json (default: the app's own, next to the executable)",
    )
    parser.add_argument(
        "--machine", default="",
        help="machine NAME from settings.json MACHINES (default: the first configured machine)",
    )
    parser.add_argument(
        "--cases-dir", default="",
        help="override the machine's cases_dir (useful when running on a different PC)",
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="only process the first N case folders found (0 = all)",
    )
    parser.add_argument(
        "--skip-existing", action="store_true",
        help="skip cases that already have results/report.html (resume an interrupted run)",
    )
    parser.add_argument(
        "--resubmit", action="store_true",
        help="push to DocuForms2 even if a case was already imported before",
    )
    parser.add_argument(
        "--no-upload", action="store_true",
        help="recompute results/ locally only; skip all PostProcessing (no DocuForms2 upload)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="recompute results/, but have the DocuForms2 step log what it would do without submitting",
    )
    parser.add_argument(
        "--email", action="store_true",
        help="send the normal per-case notification email (off by default to avoid one email per case)",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="just list the case folders that would be processed, then exit",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging(verbose=args.verbose, console=True)

    settings = load_settings(args.settings) if args.settings else load_settings()
    machine = machine_by_name(args.machine, settings) if args.machine else default_machine(settings)
    if machine is None:
        logger.error(
            "No machine found in settings.json (MACHINES). Pass --machine NAME or check --settings."
        )
        return 1
    machine = dict(machine)
    machine_name = str(machine.get("NAME") or "")
    if args.cases_dir:
        machine["cases_dir"] = args.cases_dir

    cases = list_case_folders(machine)
    if args.limit > 0:
        cases = cases[: args.limit]

    logger.info("Machine: %s", machine_name or "(unnamed)")
    logger.info("cases_dir: %s", machine.get("cases_dir"))
    logger.info("Found %d case folder(s).", len(cases))

    if not cases:
        logger.error("No case folders found under cases_dir=%s", machine.get("cases_dir"))
        return 1

    if args.list:
        for case in cases:
            print(case)
        return 0

    run_settings = _prepare_settings(
        settings,
        resubmit=args.resubmit,
        dry_run=args.dry_run,
        no_upload=args.no_upload,
    )

    from ctqa_mpc.pipeline import run_case

    ok = 0
    skipped = 0
    failed: list[str] = []
    started = time.time()
    for i, case in enumerate(cases, start=1):
        if args.skip_existing and _has_results(case):
            logger.info("[%d/%d] skip (already has results/): %s", i, len(cases), case.name)
            skipped += 1
            continue
        logger.info("[%d/%d] processing: %s", i, len(cases), case.name)
        try:
            run_case(
                case,
                machine_name=machine_name,
                send_email=args.email,
                data=run_settings,
            )
            ok += 1
        except Exception:
            logger.exception("FAILED: %s", case)
            failed.append(str(case))

    elapsed = time.time() - started
    logger.info(
        "Done in %.1fs: %d ok, %d skipped, %d failed (of %d total).",
        elapsed, ok, skipped, len(failed), len(cases),
    )
    if failed:
        logger.warning("Failed cases:\n%s", "\n".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
