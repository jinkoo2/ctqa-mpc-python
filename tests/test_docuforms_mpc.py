from pathlib import Path
import json

from ctqa_mpc.app_settings import (
    form_id_for_machine,
    format_form_ids,
    parse_form_ids_text,
)
from ctqa_mpc.docuforms_mpc import (
    MARKER_NAME,
    calculate_overall_result,
    extract_form_fields,
    mpc_form_html,
    performed_at_from_case,
    upload_case,
    values_from_case,
    zip_dicoms,
)
from ctqa_mpc.postprocess import run_post_processing

MACHINE = {
    "NAME": "GECTSH",
    "num_of_markers": 16,
    "point_to_point_dist_tol": 5.0,
    "dist_tol": 1.0,
    "axis_tol_deg": 1.0,
}


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_case(tmp_path: Path) -> tuple[Path, dict]:
    case = tmp_path / "20260928_075003_JA"
    analysis = case / "3.analysis"
    _write(
        analysis / "comparison_points.csv",
        "Name, x,y,z, x_ref, y_ref, z_ref, Distance[mm], Result\n"
        "pt0,0,0,0,0,0,0,0.4,Pass\n"
        "pt1,1,0,0,1,0,0,6.0,Fail\n",
    )
    _write(
        analysis / "comparison_distances.csv",
        "Name, Distance, Distance_ref, Difference[mm],Result\n"
        "dist_pt0_pt1,50.2,50.0,0.2,Pass\n"
        "dist_pt0_pt2,80.0,81.5,-1.5,Fail\n",
    )
    _write(
        analysis / "angles.csv",
        "Name, x, y, z, Angle to Axis [deg], Result\n"
        "X Axis, 1,0,0, 0.20, Pass\n"
        "Y Axis, 0,1,0, 1.40, Fail\n"
        "Z Axis, 0,0,1, 0.10, Pass\n",
    )
    _write(analysis / "result.json", json.dumps({"result": "fail"}) + "\n")
    _write(analysis / "report.html", "<html>report</html>")
    _write(
        case / "info.txt",
        "PatientName=MPC^JA\nStudyDate=20260928\nStudyTime=075003\n",
    )
    (case / "CT.1.dcm").write_bytes(b"DICM")
    machine = dict(MACHINE)
    machine["baseline_dir"] = str(tmp_path / "baseline")
    return case, machine


def test_form_id_for_machine():
    step = {
        "form_ids": [
            {"machine": "GECTSH", "form_id": "pfcc_gectsh_mpc"},
            {"machine": "CTSim1", "form_id": "ctqa_mpc"},
        ]
    }
    assert form_id_for_machine(step, {"NAME": "GECTSH"}) == "pfcc_gectsh_mpc"
    assert form_id_for_machine(step, {"NAME": "Other"}) == ""
    assert parse_form_ids_text("GECTSH = pfcc_gectsh_mpc\nCTSim1 ctqa_mpc") == [
        {"machine": "GECTSH", "form_id": "pfcc_gectsh_mpc"},
        {"machine": "CTSim1", "form_id": "ctqa_mpc"},
    ]
    assert format_form_ids([{"machine": "GECTSH", "form_id": "pfcc_gectsh_mpc"}]) == (
        "GECTSH = pfcc_gectsh_mpc"
    )


def test_performed_at_and_values(tmp_path):
    case, machine = _make_case(tmp_path)
    stamp = performed_at_from_case(case)
    assert stamp is not None
    assert stamp.isoformat() == "2026-09-28T07:50:03"
    values = values_from_case(case, machine, performed_by="phys")
    assert values["performed_by"] == "phys"
    assert values["performed_at"] == "2026-09-28T07:50:03"
    assert values["pt0"] == "0.4"
    assert values["pt1"] == "6.0"
    assert values["dist_pt0_pt1"] == "50.2"
    assert values["dist_pt0_pt1_baseline"] == "50.0"
    assert values["dist_pt0_pt1_error"] == "0.20"
    assert values["dist_pt0_pt2_error"] == "-1.50"
    assert values["x_axis"] == "0.2"
    assert values["y_axis"] == "1.4"


def test_upload_case_dry_run_and_skip(tmp_path):
    case, machine = _make_case(tmp_path)
    step = {
        "backend_url": "https://example.invalid",
        "dry_run": True,
        "attach_dcm_zip": False,
        "form_ids": [{"machine": "GECTSH", "form_id": "pfcc_gectsh_mpc"}],
    }
    other = dict(machine)
    other["NAME"] = "Other"
    assert upload_case(case, other, step) == "skipped"
    assert upload_case(case, machine, step) == "dry-run"
    (case / MARKER_NAME).write_text("{}", encoding="utf-8")
    step_live = {
        "backend_url": "https://example.invalid",
        "dry_run": False,
        "resubmit": False,
        "form_ids": [{"machine": "GECTSH", "form_id": "pfcc_gectsh_mpc"}],
    }
    assert upload_case(case, machine, step_live) == "skipped"


def test_run_post_processing_disabled(tmp_path, monkeypatch):
    called = []

    def boom(*_args, **_kwargs):
        called.append(True)
        raise AssertionError("should not run")

    monkeypatch.setattr("ctqa_mpc.docuforms_mpc.upload_case", boom)
    run_post_processing(
        tmp_path,
        {"NAME": "GECTSH"},
        data={
            "PostProcessing": [
                {
                    "type": "docuforms2_mpc",
                    "enabled": False,
                    "backend_url": "https://example.invalid",
                }
            ]
        },
    )
    assert called == []


def test_zip_dicoms_packs_input_dcm(tmp_path):
    case, _machine = _make_case(tmp_path)
    (case / "3.analysis" / "ignore.dcm").write_bytes(b"DICM")
    dest = tmp_path / "input_dcm.zip"
    assert zip_dicoms(case, dest) is True
    import zipfile

    with zipfile.ZipFile(dest) as zf:
        names = zf.namelist()
    assert "CT.1.dcm" in names
    assert all("3.analysis" not in name for name in names)


def test_upload_case_posts(tmp_path, monkeypatch):
    case, machine = _make_case(tmp_path)
    posted = []

    def fake_post(url, payload, *, timeout, verify):
        posted.append((url, payload))
        return {"ok": True, "_id": "sub1"}

    def fake_upload(*_a, **_k):
        return {"url": "/uploads/x.zip", "originalName": "input_dcm.zip"}

    monkeypatch.setattr("ctqa_mpc.docuforms_mpc.post_json", fake_post)
    monkeypatch.setattr("ctqa_mpc.docuforms_mpc.upload_file", fake_upload)
    status = upload_case(
        case,
        machine,
        {
            "backend_url": "https://docuforms.example.edu:9001",
            "form_ids": [{"machine": "GECTSH", "form_id": "pfcc_gectsh_mpc"}],
            "attach_dcm_zip": True,
            "attach_pdf": False,
            "verify_ssl": False,
        },
    )
    assert status == "ok"
    assert (case / MARKER_NAME).is_file()
    assert posted
    url, payload = posted[0]
    assert url.endswith("/api/forms/pfcc_gectsh_mpc/submit")
    assert payload["values"]["pt0"] == "0.4"
    assert payload["attachments"][0]["originalName"] == "input_dcm.zip"
    meta = payload["metadata"]
    assert meta["pt0"]["result"] == "PASS"
    assert meta["pt1"]["result"] == "FAIL"
    assert meta["dist_pt0_pt1_error"]["result"] == "PASS"
    assert meta["dist_pt0_pt2_error"]["result"] == "FAIL"
    assert meta["y_axis"]["result"] == "FAIL"
    assert calculate_overall_result(meta) == "FAIL"


def test_upload_case_attaches_full_report_pdf(tmp_path, monkeypatch):
    case, machine = _make_case(tmp_path)
    uploaded = []

    def fake_post(_url, payload, *, timeout, verify):
        uploaded.append(("submit", [a.get("originalName") for a in payload.get("attachments") or []]))
        return {"_id": "abc"}

    def fake_upload(_backend, path, name, **_k):
        uploaded.append(name)
        return {"url": f"/uploads/{name}", "originalName": name}

    def fake_html_to_pdf(html_path, dest):
        assert Path(html_path).name == "report.html"
        Path(dest).write_bytes(b"%PDF-1.4 dummy")
        return True

    monkeypatch.setattr("ctqa_mpc.docuforms_mpc.post_json", fake_post)
    monkeypatch.setattr("ctqa_mpc.docuforms_mpc.upload_file", fake_upload)
    monkeypatch.setattr("ctqa_mpc.docuforms_mpc.html_to_pdf", fake_html_to_pdf)
    status = upload_case(
        case,
        machine,
        {
            "backend_url": "https://docuforms.example.edu:9001",
            "form_ids": [{"machine": "GECTSH", "form_id": "pfcc_gectsh_mpc"}],
            "attach_dcm_zip": False,
            "attach_pdf": True,
        },
    )
    assert status == "ok"
    assert "report.pdf" in uploaded
    assert (case / "3.analysis" / "report.pdf").is_file()


def test_mpc_form_html_fields():
    html = mpc_form_html(n_markers=16)
    names = {row["name"] for row in extract_form_fields(html)}
    assert "performed_at" in names
    assert "performed_by" in names
    assert "pt0" in names
    assert "pt15" in names
    assert "dist_pt0_pt1" in names
    assert "dist_pt0_pt1_error" in names
    assert "dist_pt14_pt15" in names
    assert "x_axis" in names
    assert "z_axis" in names
    assert len(names) > 100
