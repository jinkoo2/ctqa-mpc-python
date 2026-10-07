import os

from ctqa_mpc.cli import prepare_argv, process_argv, wants_help


def test_prepare_argv_defaults_to_gui(monkeypatch):
    monkeypatch.setattr("sys.argv", ["ctqa-mpc"])
    assert prepare_argv([])[0] == "gui"


def test_prepare_argv_mode_service(monkeypatch):
    monkeypatch.delenv("CTQA_MPC_SETTINGS", raising=False)
    out = prepare_argv(["--mode", "service"])
    assert "watch" in out


def test_prepare_argv_settings_env(monkeypatch, tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text("{}", encoding="utf-8")
    prepare_argv(["--settings", str(settings), "analyze", "x"])
    assert os.environ["CTQA_MPC_SETTINGS"].endswith("settings.json")


def test_prepare_argv_users_env(monkeypatch, tmp_path):
    folder = tmp_path / "_users"
    monkeypatch.delenv("CTQA_MPC_USERS_DIR", raising=False)
    assert prepare_argv(["--users", str(folder)]) == ["gui"]
    assert os.environ["CTQA_MPC_USERS_DIR"] == str(folder.resolve())
    monkeypatch.delenv("CTQA_MPC_USERS_DIR", raising=False)
    assert prepare_argv([f"--users-dir={folder}", "--mode", "service"]) == ["watch"]
    assert os.environ["CTQA_MPC_USERS_DIR"] == str(folder.resolve())


def test_prepare_argv_keeps_help():
    assert prepare_argv(["--help"]) == ["--help"]
    assert prepare_argv(["-h"]) == ["-h"]
    assert wants_help(["--help"])
    assert wants_help(["gui", "-h"])
    assert process_argv(["--help"]) == ["--help"]


def test_help_lists_global_options(capsys):
    from ctqa_mpc.cli import main

    try:
        main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0
    else:
        raise AssertionError("expected SystemExit from --help")
    text = capsys.readouterr().out
    assert "--users" in text
    assert "--settings" in text
    assert "--mode" in text
    assert "-h" in text or "--help" in text

