import pytest

from ctqa_mpc.app_settings import (
    SettingsPathUnavailable,
    case_recency_key,
    SETTINGS_NAME,
    check_settings_paths,
    collect_settings_path_checks,
    elastix_dir_setting,
    is_case_folder_name,
    is_under_directory,
    list_case_folders,
    machine_by_station,
    machine_display_name,
    missing_settings_paths,
    phantom_id,
    phantom_name,
    save_settings,
    simple_machine_name,
    strip_jsonc,
)


def test_strip_jsonc():
    text = '{ "a": 1, // comment\n "b": "http://x" }'
    assert '"a"' in strip_jsonc(text)
    data = __import__("json").loads(strip_jsonc(text))
    assert data["a"] == 1
    assert data["b"] == "http://x"


def test_machine_by_station():
    data = {
        "MACHINES": [
            {"NAME": "CTSim1", "station_names": ["ctsim", "daily qa_ctsim"]}
        ]
    }
    m = machine_by_station("CTSIM", data)
    assert m["NAME"] == "CTSim1"
    assert machine_by_station("unknown", data) is None


def test_machine_phantom_display():
    machine = {
        "NAME": "CTSim1",
        "phantom": {"id": "mpc", "name": "MPC"},
    }
    assert phantom_id(machine) == "mpc"
    assert phantom_name(machine) == "MPC"
    assert machine_display_name(machine) == "CTSim1 — MPC"
    assert machine_display_name({"NAME": "CTSim1"}) == "CTSim1"
    assert phantom_id({}) == ""
    assert phantom_name({"phantom": "mpc"}) == ""


def test_is_simple_run_mode(tmp_path, monkeypatch):
    from ctqa_mpc.app_settings import is_simple_run_mode

    cfg = tmp_path / SETTINGS_NAME
    monkeypatch.setenv("CTQA_MPC_SETTINGS", str(cfg))
    assert is_simple_run_mode() is True
    save_settings({}, cfg)
    assert is_simple_run_mode() is True
    save_settings({"Institution": "Test", "MACHINES": []}, cfg)
    assert is_simple_run_mode() is True
    save_settings({"MACHINES": [{"baseline_dir": "x"}]}, cfg)
    assert is_simple_run_mode() is True
    save_settings({"MACHINES": [{"NAME": "CTSim1"}]}, cfg)
    assert is_simple_run_mode() is False
    save_settings({"RunMode": "Simple", "MACHINES": [{"NAME": "CTSim1"}]}, cfg)
    assert is_simple_run_mode() is True
    save_settings({"RunMode": "Clinic", "MACHINES": [{"NAME": "CTSim1"}]}, cfg)
    assert is_simple_run_mode() is False

    case = tmp_path / "CTSim1" / "20260928_075003"
    case.mkdir(parents=True)
    assert simple_machine_name(case) == "CTSim1"


def test_elastix_dir_setting():
    assert elastix_dir_setting({"Elastix": {"elastix_dir": r"C:\elastix"}}) == r"C:\elastix"
    assert elastix_dir_setting({"elastix_dir": r"D:\apps\elastix"}) == r"D:\apps\elastix"
    assert elastix_dir_setting({}) == ""


def test_list_case_folders(tmp_path):
    cases = tmp_path / "cases"
    (cases / "20260928_075003").mkdir(parents=True)
    (cases / "20260929_080000_JK").mkdir()
    (cases / "20200101_000000_SL").mkdir()
    (cases / ".skip").mkdir()
    (cases / "scratch").mkdir()
    (cases / "10022026_DailyQA").mkdir()
    (cases / "20260928").mkdir()
    folders = list_case_folders({"cases_dir": str(cases)})
    assert [p.name for p in folders] == ["20260929_080000_JK", "20260928_075003", "20200101_000000_SL"]
    assert case_recency_key(cases / "20260929_080000_JK") > case_recency_key(cases / "20200101_000000_SL")
    assert list_case_folders({"cases_dir": str(tmp_path / "missing")}) == []
    assert is_case_folder_name("20260928_075003")
    assert is_case_folder_name("20261005_122239_JK")
    assert not is_case_folder_name("10022026_DailyQA")
    assert not is_case_folder_name("20261301_000000")


def test_is_under_directory(tmp_path):
    root = tmp_path / "watch"
    child = root / "10022026_DailyQA"
    nested = child / "sub"
    sibling = tmp_path / "watch_other" / "case"
    nested.mkdir(parents=True)
    sibling.mkdir(parents=True)
    assert is_under_directory(child, root)
    assert is_under_directory(nested, root)
    assert not is_under_directory(root, root)
    assert is_under_directory(root, root, allow_root=True)
    assert not is_under_directory(sibling, root)
    assert not is_under_directory(tmp_path, root)


def test_collect_settings_path_checks(tmp_path, monkeypatch):
    cfg = tmp_path / SETTINGS_NAME
    monkeypatch.setenv("CTQA_MPC_SETTINGS", str(cfg))
    watch = tmp_path / "import"
    data_root = tmp_path / "root"
    machine_dir = data_root / "CTSim1"
    baseline = machine_dir / "baseline"
    cases = machine_dir / "cases"
    template = machine_dir / "report.html"
    labeler = tmp_path / "ImageLabeler3D.exe"
    for folder in (watch, data_root, machine_dir, baseline, cases):
        folder.mkdir(parents=True, exist_ok=True)
    template.write_text("<html></html>", encoding="utf-8")
    labeler.write_bytes(b"mz")
    save_settings(
        {
            "RunMode": "Clinic",
            "MACHINES": [{"NAME": "CTSim1"}],
            "Watcher": {"watch_path": str(watch), "data_root": str(data_root)},
            "Viewer": {"vtk_image_labeler_3d": str(labeler)},
        },
        cfg,
    )
    data = {
        "RunMode": "Clinic",
        "Watcher": {"watch_path": str(watch), "data_root": str(data_root)},
        "Viewer": {"vtk_image_labeler_3d": str(labeler)},
        "MACHINES": [
            {
                "NAME": "CTSim1",
                "machine_dir": str(machine_dir),
                "baseline_dir": str(baseline),
                "cases_dir": str(cases),
                "html_report_template": str(template),
            }
        ],
    }
    checks = collect_settings_path_checks(data, role="watch")
    assert missing_settings_paths(checks) == []
    labels = [c.label for c in checks]
    assert "Watcher.watch_path" in labels
    assert "MACHINES[CTSim1].baseline_dir" in labels
    assert "Viewer.vtk_image_labeler_3d" in labels

    data["MACHINES"][0]["baseline_dir"] = str(tmp_path / "missing-baseline")
    data["MACHINES"][0]["html_report_template"] = str(tmp_path / "missing.html")
    checks = check_settings_paths(data, role="watch", require=False)
    missing = {c.label: c for c in missing_settings_paths(checks)}
    assert "MACHINES[CTSim1].baseline_dir" in missing
    assert missing["MACHINES[CTSim1].baseline_dir"].required
    assert "MACHINES[CTSim1].html_report_template" in missing
    assert not missing["MACHINES[CTSim1].html_report_template"].required
    with pytest.raises(SettingsPathUnavailable, match="baseline_dir"):
        check_settings_paths(data, role="watch", require=True)
