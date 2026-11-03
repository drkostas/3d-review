import time

from review3d.bench.watch import Watcher


def test_a_sheet_that_stays_behind_is_not_rebuilt_every_pass(tmp_path):
    src = tmp_path / "build.py"
    src.write_text("x = 1")
    behind = {"good", "bad"}
    tried = []

    def repair(names):
        tried.append(list(names))
        behind.discard("good")                  # "good" rebuilds, "bad" fails every time

    w = Watcher(stale=lambda: sorted(behind), repair=repair, sources=lambda: [src], quiet_s=0.0)
    assert w.pass_once() == ["bad", "good"]
    assert w.status()["last_rebuilt"] == ["good"]
    assert w.status()["failing"] == ["bad"] and "bad" in w.status()["error"]
    assert w.pass_once() == []                   # nothing changed, so "bad" is not tried again
    assert tried == [["bad", "good"]]
    time.sleep(0.01)
    src.write_text("x = 2")                      # a source changed: "bad" gets another try
    w.pass_once()
    assert tried[-1] == ["bad"]


def test_a_forced_pass_still_tries_a_failing_sheet(tmp_path):
    src = tmp_path / "build.py"
    src.write_text("x = 1")
    tried = []
    w = Watcher(stale=lambda: ["bad"], repair=lambda n: tried.append(list(n)), sources=lambda: [src],
                quiet_s=0.0)
    w.pass_once()
    w.pass_once(force=True)
    assert tried == [["bad"], ["bad"]]
