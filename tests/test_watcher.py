import pytest

from ctqa_mpc.app_settings import DEFAULT_CASE_FOLDER_REGEX, watcher_case_folder_regex
from ctqa_mpc.watcher import WatchPathUnavailable, case_folder_matches, process_import_dir, watch


def test_case_folder_matches_mpc():
    assert case_folder_matches("10022026_MPC", DEFAULT_CASE_FOLDER_REGEX)
    assert case_folder_matches("01012026_MPC", DEFAULT_CASE_FOLDER_REGEX)
    assert not case_folder_matches("foo_mpc_bar", DEFAULT_CASE_FOLDER_REGEX)
    assert not case_folder_matches("10022026_DailyQA", DEFAULT_CASE_FOLDER_REGEX)
    assert not case_folder_matches("10022026_mpc", DEFAULT_CASE_FOLDER_REGEX)
    assert not case_folder_matches("scratch", DEFAULT_CASE_FOLDER_REGEX)
    assert case_folder_matches("anything", "")


def test_watcher_case_folder_regex_default_and_empty():
    assert watcher_case_folder_regex({}) == DEFAULT_CASE_FOLDER_REGEX
    assert watcher_case_folder_regex({"Watcher": {}}) == DEFAULT_CASE_FOLDER_REGEX
    assert watcher_case_folder_regex({"Watcher": {"CASE_FOLDER_NAME_REGEX": ""}}) == ""
    assert (
        watcher_case_folder_regex({"Watcher": {"CASE_FOLDER_NAME_REGEX": r"^\d{8}_DailyQA$"}})
        == r"^\d{8}_DailyQA$"
    )
    assert watcher_case_folder_regex({"case_folder_name_regex": r"^x$"}) == r"^x$"


def _stub_sort(monkeypatch, tmp_path, series_list):
    monkeypatch.setattr("ctqa_mpc.watcher.scratch_parent", lambda: tmp_path / "tmp")
    (tmp_path / "tmp").mkdir(exist_ok=True)
    monkeypatch.setattr("ctqa_mpc.watcher.sort_files_by_patient_study_series", lambda *a, **k: None)
    monkeypatch.setattr("ctqa_mpc.watcher.series_dirs", lambda d: series_list)
    monkeypatch.setattr("ctqa_mpc.watcher.dicom_series_to_mha", lambda *a, **k: None)


def test_process_import_dir_strict_no_series(tmp_path, monkeypatch):
    import_dir = tmp_path / "10022026_DailyQA"
    import_dir.mkdir()
    _stub_sort(monkeypatch, tmp_path, [])
    assert process_import_dir(import_dir, {}, send_email=False, strict=False) == []
    with pytest.raises(RuntimeError, match="no DICOM series"):
        process_import_dir(import_dir, {}, send_email=False, strict=True)


def test_process_import_dir_strict_short_series(tmp_path, monkeypatch):
    import_dir = tmp_path / "src"
    import_dir.mkdir()
    series = tmp_path / "s1"
    series.mkdir()
    (series / "a.dcm").write_bytes(b"x")
    _stub_sort(monkeypatch, tmp_path, [series])
    settings = {"Watcher": {"min_series_dicom_files": 100}}
    assert process_import_dir(import_dir, settings, send_email=False, strict=False) == []
    with pytest.raises(RuntimeError, match="no case published"):
        process_import_dir(import_dir, settings, send_email=False, strict=True)


def test_process_import_dir_strict_unknown_station(tmp_path, monkeypatch):
    import_dir = tmp_path / "src"
    import_dir.mkdir()
    series = tmp_path / "s1"
    series.mkdir()
    (series / "a.dcm").write_bytes(b"x")
    (series / "info.txt").write_text("StationName=UNKNOWN\n", encoding="utf-8")
    _stub_sort(monkeypatch, tmp_path, [series])
    settings = {"Watcher": {"min_series_dicom_files": 1}, "MACHINES": []}
    with pytest.raises(RuntimeError, match="no machine"):
        process_import_dir(import_dir, settings, send_email=False, strict=True)


def test_process_import_dir_publishes_and_emails(tmp_path, monkeypatch):
    import_dir = tmp_path / "src"
    import_dir.mkdir()
    series = tmp_path / "s1"
    series.mkdir()
    (series / "a.dcm").write_bytes(b"x")
    (series / "info.txt").write_text(
        "PatientName=MPC^JK\nStationName=CTSIM\nSeriesDate=20261006\nStudyTime=080000\n",
        encoding="utf-8",
    )
    cases = tmp_path / "cases"
    cases.mkdir()
    _stub_sort(monkeypatch, tmp_path, [series])
    called = {}

    def fake_run(case_dir, machine_name="", send_email=True, data=None):
        called["run"] = (str(case_dir), machine_name, send_email)

    monkeypatch.setattr("ctqa_mpc.watcher.run_case", fake_run)
    monkeypatch.setattr(
        "ctqa_mpc.watcher.copy_tree_files",
        lambda src, dest: dest.mkdir(parents=True, exist_ok=True),
    )
    settings = {
        "Watcher": {"min_series_dicom_files": 1},
        "MACHINES": [
            {
                "NAME": "CTSim1",
                "station_names": ["ctsim"],
                "patient_last_names": ["mpc", "jk"],
                "cases_dir": str(cases),
            },
        ],
    }
    published = process_import_dir(import_dir, settings, send_email=True, strict=True)
    assert len(published) == 1
    dest, machine = published[0]
    assert dest == cases / "20261006_080000_JK"
    assert machine["NAME"] == "CTSim1"
    assert called["run"] == (str(series), "CTSim1", True)


def test_watch_checks_baseline_at_start(tmp_path):
    watch_dir = tmp_path / "import"
    watch_dir.mkdir()
    settings = {
        "RunMode": "Clinic",
        "Watcher": {"watch_path": str(watch_dir), "data_root": str(tmp_path / "root")},
        "MACHINES": [
            {
                "NAME": "CTSim1",
                "machine_dir": str(tmp_path / "machine"),
                "baseline_dir": str(tmp_path / "missing-baseline"),
                "cases_dir": str(tmp_path / "cases"),
            }
        ],
    }
    with pytest.raises(WatchPathUnavailable, match="baseline_dir"):
        watch(str(watch_dir), data=settings)
