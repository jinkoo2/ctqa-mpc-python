import os
from datetime import datetime, timedelta

from ctqa_mpc.app_settings import temp_cleanup_settings
from ctqa_mpc.temp_cleanup import cleanup_scratch, start_post_analysis_cleanup


def test_temp_cleanup_settings_defaults():
    assert temp_cleanup_settings({})["older_than_days"] == 7
    assert temp_cleanup_settings({"Watcher": {"temp_cleanup_older_than_days": 3}})[
        "older_than_days"
    ] == 3


def test_cleanup_scratch_removes_old_prefix_only(tmp_path):
    now = datetime(2026, 10, 6, 21, 0, 0)
    old = tmp_path / "ctqa_sort_20260901_010000"
    recent = tmp_path / "ctqa_sort_20261006_200000"
    other = tmp_path / "keep_me"
    old.mkdir()
    recent.mkdir()
    other.mkdir()
    old_ts = (now - timedelta(days=8)).timestamp()
    recent_ts = (now - timedelta(hours=2)).timestamp()
    os.utime(old, (old_ts, old_ts))
    os.utime(recent, (recent_ts, recent_ts))
    result = cleanup_scratch(tmp_path, older_than_days=7, now=now)
    assert not old.exists()
    assert recent.exists()
    assert other.exists()
    assert any(old.name in p for p in result.removed)


def test_start_post_analysis_cleanup_is_nonblocking(tmp_path):
    old = tmp_path / "ctqa_sort_old"
    old.mkdir()
    old_ts = (datetime.now() - timedelta(days=10)).timestamp()
    os.utime(old, (old_ts, old_ts))
    thread = start_post_analysis_cleanup(
        {"Watcher": {"temp_cleanup_older_than_days": 7}},
        root=tmp_path,
    )
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert not old.exists()
