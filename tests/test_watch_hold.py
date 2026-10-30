import datetime as dt
import os
import time

from review3d.bench import watch


def _w(tmp_path, hold_s=3600.0):
    return watch.Watcher(stale=lambda: [], repair=lambda names: None, hold=tmp_path / ".hold", hold_s=hold_s)


def _iso(seconds_from_now):
    return dt.datetime.fromtimestamp(time.time() + seconds_from_now, dt.timezone.utc).isoformat()


def test_no_file_is_no_hold(tmp_path):
    assert not _w(tmp_path).held()


def test_an_empty_hold_lasts_hold_s(tmp_path):
    f = tmp_path / ".hold"
    f.write_text("")
    assert _w(tmp_path).held()
    old = time.time() - 4000
    os.utime(f, (old, old))
    assert not _w(tmp_path).held()


def test_a_hold_that_names_its_end_lasts_past_hold_s_until_then(tmp_path):
    f = tmp_path / ".hold"
    f.write_text(_iso(5 * 3600))
    old = time.time() - 2 * 3600                  # written two hours ago, past an hour's hold_s
    os.utime(f, (old, old))
    assert _w(tmp_path).held()
    f.write_text(_iso(-60))                        # its end has passed
    assert not _w(tmp_path).held()


def test_a_named_end_never_holds_past_a_day_from_writing(tmp_path):
    f = tmp_path / ".hold"
    f.write_text(_iso(10 * 24 * 3600))
    old = time.time() - watch.HOLD_MAX_S - 60
    os.utime(f, (old, old))
    assert not _w(tmp_path).held()


def test_only_an_absolute_time_with_its_zone_names_an_end():
    assert watch.hold_until("2026-10-31T09:00:00+02:00") is not None
    assert watch.hold_until("2026-10-31T09:00:00") is None
    assert watch.hold_until("12 hours") is None
    assert watch.hold_until("  ") is None
