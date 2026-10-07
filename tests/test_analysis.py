import math
from pathlib import Path

import numpy as np
import SimpleITK as sitk

from ctqa_mpc.analysis import (
    analyze,
    axes_and_angles,
    find_markers,
    pairwise_distances,
    threshold_bb,
    write_case_result,
)
from ctqa_mpc.app_settings import is_case_folder_name, machine_by_patient_station
from ctqa_mpc.report import _fmt, build_case_report


def _machine():
    return {
        "NAME": "CTSim1",
        "num_of_markers": 4,
        "th": 100,
        "search_box_size_mm": 16,
        "pt0": [0.0, 0.0, 0.0],
        "pt1": [40.0, 0.0, 0.0],
        "pt2": [40.0, 40.0, 0.0],
        "pt3": [0.0, 40.0, 0.0],
        "z_axis_cross_product": ["z0"],
        "z0": ["pt0", "pt1", "pt3"],
        "y_axis_vectors": ["y0"],
        "y0": ["pt0", "pt3"],
        "point_to_point_dist_tol": 2.0,
        "dist_tol": 1.0,
        "axis_tol_deg": 5.0,
    }


def _volume_with_bbs(points, *, value=1000.0) -> sitk.Image:
    arr = np.zeros((21, 81, 81), dtype=np.float32)
    image = sitk.GetImageFromArray(arr)
    image.SetSpacing((1.0, 1.0, 1.0))
    image.SetOrigin((-20.0, -20.0, -10.0))
    arr = sitk.GetArrayFromImage(image)
    for _name, xyz in points:
        idx = image.TransformPhysicalPointToIndex(tuple(xyz))
        x, y, z = idx
        arr[z - 1 : z + 2, y - 1 : y + 2, x - 1 : x + 2] = value
    out = sitk.GetImageFromArray(arr)
    out.CopyInformation(image)
    return out


def test_threshold_bb_int16_high_hu():
    arr = np.zeros((5, 5, 5), dtype=np.int16)
    arr[2, 2, 2] = 24037
    image = sitk.GetImageFromArray(arr)
    out = threshold_bb(image, 5000.0)
    mask = sitk.GetArrayFromImage(out)
    assert mask[2, 2, 2] == 255
    assert int(mask.max()) == 255
    assert int(mask.min()) == 0


def test_find_markers_and_distances():
    machine = _machine()
    seeds = [(f"pt{i}", machine[f"pt{i}"]) for i in range(4)]
    ct = _volume_with_bbs(seeds)
    found = find_markers(ct, machine)
    assert [p["id"] for p in found] == ["pt0", "pt1", "pt2", "pt3"]
    for seed, got in zip(seeds, found):
        assert math.hypot(got["x"] - seed[1][0], got["y"] - seed[1][1]) < 1.5
    dists = {row["id"]: row["value"] for row in pairwise_distances(found)}
    assert abs(dists["dist_pt0_pt1"] - 40.0) < 1.5
    assert abs(dists["dist_pt0_pt2"] - math.hypot(40, 40)) < 2.0


def test_axes_nearly_aligned():
    machine = _machine()
    points = [{"id": f"pt{i}", "x": machine[f"pt{i}"][0], "y": machine[f"pt{i}"][1], "z": machine[f"pt{i}"][2]} for i in range(4)]
    axes = axes_and_angles(points, machine)
    assert axes["angles_to_axis"][0] < 1.0
    assert axes["angles_to_axis"][1] < 1.0
    assert axes["angles_to_axis"][2] < 1.0


def test_analyze_writes_pass_result(tmp_path):
    machine = _machine()
    seeds = [(f"pt{i}", machine[f"pt{i}"]) for i in range(4)]
    ct = _volume_with_bbs(seeds)
    baseline = tmp_path / "baseline"
    case = tmp_path / "case"
    analyze(ct, baseline / "results", machine)
    analyze(ct, case / "results", machine)
    summary = write_case_result(case / "results", baseline, machine)
    data = __import__("json").loads(summary.read_text(encoding="utf-8"))
    assert data["result"] == "pass"
    report = build_case_report(case, machine)
    assert report["result"] == "pass"
    assert [s["key"] for s in report["sections"]] == ["points", "distances", "axes"]


def test_machine_by_patient_station():
    data = {
        "MACHINES": [
            {
                "NAME": "GECTSH",
                "station_names": ["ctsim"],
                "patient_last_names": ["mpc", "jk"],
            }
        ]
    }
    assert machine_by_patient_station("MPC", "CTSIM", data)["NAME"] == "GECTSH"
    assert machine_by_patient_station("jk", "ctsim", data)["NAME"] == "GECTSH"
    assert machine_by_patient_station("dailyqa", "ctsim", data) is None


def test_case_folder_operator_suffix():
    assert is_case_folder_name("20261005_122239_JK")
    assert is_case_folder_name("20261005_122239")
    assert is_case_folder_name("08212019_000000_JK")
    assert not is_case_folder_name("10022026_DailyQA")
    assert not is_case_folder_name("scratch")


def test_report_fmt():
    assert _fmt(1.84, "0.0") == "1.8"
    assert _fmt(1.84, "0.00") == "1.84"
