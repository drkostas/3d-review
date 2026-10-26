import os
import pathlib
import time

import pytest

from review3d import fresh
from review3d.bench import watch


def _project(root):
    """A tiny project: build.py imports helper lazily and a package, report.py imports build."""
    (root / "proj").mkdir(parents=True)
    (root / "lib" / "mylib").mkdir(parents=True)
    (root / "proj" / "build.py").write_text(
        "import json\nfrom mylib.shapes import ball\n\ndef make():\n    import helper\n    return helper.x\n")
    (root / "proj" / "helper.py").write_text("x = 1\n")
    (root / "proj" / "report.py").write_text("import build\n")
    (root / "proj" / "unused.py").write_text("y = 2\n")
    (root / "lib" / "mylib" / "__init__.py").write_text("")
    (root / "lib" / "mylib" / "shapes.py").write_text("from .core import radius\n\ndef ball():\n    return radius\n")
    (root / "lib" / "mylib" / "core.py").write_text("radius = 3\n")
    return root / "proj", {"mylib": root / "lib" / "mylib"}


def test_the_closure_follows_lazy_and_package_imports(tmp_path):
    proj, pkgs = _project(tmp_path)
    names = {f.name for f in fresh.import_closure("build", proj, pkgs)}
    assert names == {"build.py", "helper.py", "shapes.py", "core.py", "__init__.py"}


def test_a_report_does_not_date_what_it_reports_on(tmp_path):
    proj, pkgs = _project(tmp_path)
    names = {f.name for f in fresh.import_closure("report", proj, pkgs, skip={"build"})}
    assert names == {"report.py"}


def test_the_fingerprint_is_about_content_not_place_or_time(tmp_path):
    a, _ = _project(tmp_path / "a")
    b, _ = _project(tmp_path / "b")
    fa = fresh.fingerprint(sorted(a.glob("*.py")))
    fb = fresh.fingerprint(sorted(b.glob("*.py"), reverse=True))
    assert fa == fb                                           # same files elsewhere, any order
    os.utime(a / "helper.py", (1, 1))
    assert fresh.fingerprint(a.glob("*.py")) == fa            # a touch changes nothing
    (a / "helper.py").write_text("x = 2\n")
    assert fresh.fingerprint(a.glob("*.py")) != fa


def test_the_content_check_outranks_the_clock_both_ways(tmp_path):
    proj, pkgs = _project(tmp_path)
    stamps = fresh.Stamps(tmp_path / "out" / ".fingerprints.json")
    fp = fresh.fingerprint(fresh.import_closure("build", proj, pkgs))
    stamps.put("front", fp)
    # the code was touched after the picture: still current, because the content is the same
    assert fresh.verdict(stamps.matches("front", fp), minutes=12.0) is None
    # the code changed and was copied back with an old time: stale, whatever the clock says
    (proj / "helper.py").write_text("x = 5\n")
    os.utime(proj / "helper.py", (1, 1))
    now = fresh.fingerprint(fresh.import_closure("build", proj, pkgs))
    why, _m = fresh.verdict(stamps.matches("front", now), minutes=-30.0)
    assert why == "the code that drew it has changed"
    # never stamped: the clock decides
    assert fresh.verdict(None, minutes=3.0)[0] == "older than the code that draws it"
    assert fresh.verdict(None, minutes=-3.0) is None


def test_the_lock_is_reentrant_and_frees_a_dead_holder(tmp_path):
    path = tmp_path / ".rebuilding.lock"
    with fresh.lock(path) as held:
        assert held
        with fresh.lock(path) as again:
            assert again                                      # the same process may take it again
    assert not path.exists()
    path.write_text("999999")                                 # a holder that no longer exists
    with fresh.lock(path) as held:
        assert held
    path.write_text(str(os.getppid()))                        # a live holder that is not us
    with fresh.lock(path) as held:
        assert not held


def test_the_watcher_repairs_what_is_behind():
    done = []
    w = watch.Watcher(stale=lambda: [("card", 4.0)], repair=done.append)
    assert w.pass_once() == ["card"] and done == [["card"]]
    assert w.status()["stale"] == [{"sheet": "card", "minutes": 4.0}]


def test_the_watcher_waits_while_the_code_is_edited_or_held(tmp_path):
    src = tmp_path / "build.py"
    src.write_text("x = 1\n")
    hold = tmp_path / ".hold"
    done = []
    w = watch.Watcher(stale=lambda: ["card"], repair=done.append, sources=lambda: [src], hold=hold)
    assert w.editing() and w.pass_once() == []                # just edited
    os.utime(src, (time.time() - 600, time.time() - 600))
    hold.write_text("")
    assert w.held() and w.pass_once() == [] and w.status()["held"]
    os.utime(hold, (time.time() - 7200, time.time() - 7200))  # a hold someone forgot
    assert not w.held() and w.pass_once() == ["card"]


def test_run_step_reports_a_failed_step_and_ends_a_slow_one(tmp_path):
    assert watch.run_step("print('ok')", "a step", log=lambda s: None) == 0
    with pytest.raises(RuntimeError):
        watch.run_step("raise SystemExit(3)", "a failing step", log=lambda s: None)
    t = time.time()
    with pytest.raises(RuntimeError):
        watch.run_step("import time; time.sleep(30)", "a slow step", timeout=1, log=lambda s: None)
    assert time.time() - t < 10
