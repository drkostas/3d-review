"""The step ledger, on a small pipeline written to a real module file."""
import importlib.util
import sys
import textwrap

import pytest

from review3d import ledger

PIPELINE = textwrap.dedent('''
    import numpy as np
    import trimesh


    def _turn(out):
        out["lid"].apply_transform(trimesh.transformations.rotation_matrix(np.radians(30), [0, 0, 1]))


    def _remake(out):
        out["lid"] = trimesh.creation.cylinder(radius=4, height=3)


    def _check(out):
        pass


    def _forgotten(out):
        pass


    def build():
        out = {"lid": trimesh.creation.box((10, 6, 2)), "peg": trimesh.creation.box((2, 2, 2))}
        _turn(out)
        _remake(out)
        out["lid"].apply_translation((0, 0, 5))
        _check(out)
        _forgotten(out)
        return out
''')


@pytest.fixture
def mod(tmp_path):
    path = tmp_path / "toy_pipeline.py"
    path.write_text(PIPELINE)
    spec = importlib.util.spec_from_file_location("toy_pipeline", path)
    m = importlib.util.module_from_spec(spec)
    sys.modules["toy_pipeline"] = m
    spec.loader.exec_module(m)
    yield m
    sys.modules.pop("toy_pipeline", None)


def test_steps_are_read_from_source_in_order(mod):
    assert ledger.steps_in(PIPELINE, "build") == ["_turn", "_remake", "_check", "_forgotten"]
    assert ledger.steps_in(PIPELINE, "no_such_function") == []


def test_each_step_and_inline_code_is_reported(mod, tmp_path):
    ledger.install(mod, {"lid"}, pipeline="build")
    mod.build()
    by_step = {e["step"]: e for e in ledger._LOG}
    assert set(by_step) == {"_turn", "_remake", "between _remake and _check"}

    turn = by_step["_turn"]
    assert turn["what"] == "moved" and turn["deg"] == pytest.approx(30, abs=0.01)
    assert turn["scale"] == pytest.approx(1.0) and turn["n"] == 1

    assert by_step["_remake"]["what"] == "rebuilt" and by_step["_remake"]["n"] == 2

    inline = by_step["between _remake and _check"]
    assert inline["inline"] is True and inline["what"] == "moved"
    assert inline["mm"] == pytest.approx(5.0) and inline["deg"] == pytest.approx(0, abs=0.01)

    # The unwatched part is never logged, and every step ran once in order.
    assert all(e["part"] == "lid" for e in ledger._LOG)
    assert ledger._STEPS == [("_turn", 1), ("_remake", 2), ("_check", 3), ("_forgotten", 4)]

    out = ledger.write(tmp_path / "ledger.json")
    body = ledger.read(out)
    assert body["steps_run"] == 4 and len(body["entries"]) == 3
    assert [e["step"] for e in ledger.after_seam("lid", "_remake")] == [
        "between _remake and _check"]


def test_a_second_run_starts_clean(mod):
    ledger.install(mod, {"lid"}, pipeline="build")
    mod.build()
    mod.build()
    assert len(ledger._LOG) == 3 and ledger._SEEN["steps"] == 4


def test_a_step_left_out_of_the_wrapped_list_is_reported(mod):
    ledger.install(mod, {"lid"}, names=["_turn", "_remake", "_check"], pipeline="build")
    assert ledger.missing(mod, "build") == ["_forgotten"]
    ledger.install(mod, {"lid"}, pipeline="build")
    assert ledger.missing(mod, "build") == []


def test_mark_records_labelled_inline_code(mod):
    ledger.install(mod, {"lid"}, pipeline="build")
    out = {"lid": __import__("trimesh").creation.box((4, 4, 4))}
    mod._check(out)
    out["lid"].apply_translation((1, 0, 0))
    ledger.mark(out, "nudge")
    mod._forgotten(out)
    steps = [(e["step"], e["what"]) for e in ledger._LOG]
    assert steps == [("nudge", "asserted")]


def test_install_needs_the_pipeline_name(mod):
    with pytest.raises(TypeError):
        ledger.install(mod, {"lid"})


def test_delta_is_exact():
    import numpy as np
    a = np.random.default_rng(0).normal(size=(50, 3))
    a -= a.mean(0)                     # about the origin, so the turn does not move the centroid
    R = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]], float)
    deg, scale, mm = ledger.delta(a, a @ R.T + [3, 4, 0])
    assert deg == pytest.approx(90) and scale == pytest.approx(1) and mm == pytest.approx(5)
    assert ledger.delta(a, a[:-1]) is None


def test_write_and_read_take_a_plain_string(tmp_path):
    from review3d import ledger
    f = str(tmp_path / "sub" / "ledger.json")
    ledger.write(f)
    assert ledger.read(f)["entries"] == ledger._LOG
    assert ledger.read(str(tmp_path / "missing.json")) is None
