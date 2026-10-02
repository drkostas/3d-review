"""A step ledger: which step of a build pipeline moved or rebuilt each watched part.

A pipeline here is a function that builds a dict of named `trimesh.Trimesh` parts and passes it
through a series of step functions, each called as `_step(out, ...)` with the dict first. The
ledger wraps every step so that each one reports, for each watched part, what it did.

The delta is exact, not fitted. A step that moves a part with `apply_translation` or
`apply_transform` keeps the vertex order, so the rigid transform between the part's vertices
before and after the step is recovered exactly by the Kabsch method. A part whose vertex count
changed was rebuilt, and that is recorded as "rebuilt" rather than as a motion.

Code that runs between two steps (inline code in the pipeline body) is caught as well. Each
wrapped step first compares the watched parts against where the previous step left them, and any
change found there is logged as happening between those two steps.

The step list is read from the pipeline's source with `steps_in`, never typed by hand, because a
hand-kept list goes out of date and a step missing from it would be invisible to the ledger.
`missing` reports any step the pipeline calls that is not wrapped.

Typical use:

    names = ledger.install(module, {"lid"}, pipeline="build")
    module.build(...)
    ledger.write(pathlib.Path("ledger.json"))
    assert not ledger.missing(module, "build")
"""
import ast
import inspect
import json
import pathlib

import numpy as np

_LOG = []
_SEEN = {"steps": 0}
_LAST = {}          # part -> {"step": name of the step it last left, "v": its vertices then}
_STEPS = []         # [(step name, n)] in the order the steps completed
_RUN = {"i": 0}     # steps completed in the current run
_WATCH = set()      # the parts being followed, set by `install` and read by every wrapper
_THRESH = {"deg": 0.5, "scale": 0.005, "mm": 0.05}


def _changed(d):
    """True when a Kabsch delta is above the thresholds in `_THRESH`, or the part was rebuilt."""
    return d is None or d[0] > _THRESH["deg"] or abs(d[1] - 1.0) > _THRESH["scale"] \
        or d[2] > _THRESH["mm"]


def _entry(step, n, part, d, **more):
    e = {"step": step, "n": n, "part": part}
    if d is None:
        e["what"] = "rebuilt"
    else:
        e.update({"what": "moved", "deg": round(d[0], 2), "scale": round(d[1], 4),
                  "mm": round(d[2], 3)})
    e.update(more)
    return e


def steps_in(src, pipeline, first_arg="out"):
    """Every `_something(<first_arg>, ...)` call inside the function `pipeline`, in source order.

    Duplicates are kept, because a step that runs more than once is a separate step each time and
    the question "which of the runs moved it" depends on that. Returns [] when `src` has no
    function called `pipeline`.
    """
    tree = ast.parse(src)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == pipeline), None)
    if fn is None:
        return []
    # Sorted by line and column, because `ast.walk` is breadth-first and its order is not the
    # order of the source.
    found = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            nm = node.func.id
            if nm.startswith("_") and node.args and isinstance(node.args[0], ast.Name) \
                    and node.args[0].id == first_arg:
                found.append((node.lineno, node.col_offset, nm))
    return [nm for _, _, nm in sorted(found)]


def delta(a, b):
    """(degrees turned, scale ratio, mm moved) between two vertex arrays, or None if rebuilt.

    The arrays must list the same vertices in the same order. A different shape means the part
    was rebuilt and no rigid transform exists between the two.
    """
    if a.shape != b.shape:
        return None
    ca, cb = a.mean(0), b.mean(0)
    A0, B0 = a - ca, b - cb
    sa = float(np.sqrt((A0 ** 2).sum())) or 1e-9
    sb = float(np.sqrt((B0 ** 2).sum())) or 1e-9
    U, S, Vt = np.linalg.svd((A0 / sa).T @ (B0 / sb))
    d = float(np.sign(np.linalg.det(Vt.T @ U.T)))
    R = Vt.T @ np.diag([1.0, 1.0, d]) @ U.T
    ang = float(np.degrees(np.arccos(np.clip((np.trace(R) - 1.0) / 2.0, -1.0, 1.0))))
    return ang, float(sb / sa), float(np.linalg.norm(cb - ca))


def begin_run():
    """Forget the previous run. The ledger describes one run of the pipeline, not a process."""
    _LOG.clear()
    _LAST.clear()
    _STEPS.clear()
    _RUN["i"] = 0
    _SEEN["steps"] = 0


def install(mod, watch, names=None, pipeline=None):
    """Wrap every step that the function `pipeline` in module `mod` calls.

    `watch` is the set of part names to follow. `pipeline` is the name of the pipeline function
    and is required. `names` is the list of steps to wrap. By default it is read from the
    module's own source with `steps_in`. Returns the list of step names.

    The pipeline function itself is also wrapped, so that every call to it starts a new run.
    Callers reach it through the module attribute, so they get the wrapper.
    """
    if not pipeline:
        raise TypeError("install() needs the name of the pipeline function as pipeline=")
    begin_run()
    _WATCH.clear()
    _WATCH.update(watch)
    names = list(names) if names is not None else steps_in(inspect.getsource(mod), pipeline)
    order = _RUN
    watch = _WATCH
    top = getattr(mod, pipeline, None)
    if callable(top) and not getattr(top, "_ledgered", False):
        def _pipeline(*a, _f=top, **k):
            begin_run()
            return _f(*a, **k)
        _pipeline.__name__ = pipeline
        _pipeline._ledgered = True
        _pipeline.__wrapped__ = top
        setattr(mod, pipeline, _pipeline)
    for nm in dict.fromkeys(names):                      # wrap each function once
        f = getattr(mod, nm, None)
        if not callable(f) or getattr(f, "_ledgered", False):
            continue

        def make(nm=nm, f=f):
            def g(*a, **k):
                out = a[0] if a and isinstance(a[0], dict) else None
                if out is not None:
                    # On entry, log anything that changed since the previous step left. This is
                    # the check that sees inline code; the per-step check below cannot.
                    for p, last in list(_LAST.items()):
                        if p not in watch:
                            continue
                        m = out.get(p)
                        gap = f"between {last['step']} and {nm}"
                        if m is None:
                            _LOG.append({"step": gap, "n": order["i"], "part": p,
                                         "what": "removed", "inline": True})
                            del _LAST[p]
                            continue
                        d = delta(last["v"], np.asarray(m.vertices, float))
                        if _changed(d):
                            _LOG.append(_entry(gap, order["i"], p, d, inline=True))
                before = ({p: np.asarray(out[p].vertices, float).copy()
                           for p in watch if out.get(p) is not None} if out is not None else {})
                r = f(*a, **k)
                order["i"] += 1
                _SEEN["steps"] = order["i"]
                _STEPS.append((nm, order["i"]))
                if out is not None:
                    for p, va in before.items():
                        m = out.get(p)
                        if m is None:
                            _LOG.append({"step": nm, "n": order["i"], "part": p, "what": "removed"})
                            continue
                        d = delta(va, np.asarray(m.vertices, float))
                        if _changed(d):
                            _LOG.append(_entry(nm, order["i"], p, d))
                    # On exit, remember where every watched part was left, for the next entry check.
                    for p in watch:
                        m = out.get(p)
                        if m is not None:
                            _LAST[p] = {"step": nm, "v": np.asarray(m.vertices, float).copy()}
                        else:
                            _LAST.pop(p, None)
                return r
            g.__name__ = nm
            g._ledgered = True
            return g
        setattr(mod, nm, make())
    return names


def mark(out, label, parts=None):
    """Register what a labelled piece of inline code did, so it is not reported as unexplained.

    Compares each named part against where the last step left it, logs any change under `label`
    with `what` set to "asserted", and moves the exit record on so the next step's entry check
    starts from here. Parts the ledger is not following are ignored.
    """
    n = _SEEN["steps"]
    for p in (parts if parts is not None else list(_LAST)):
        last = _LAST.get(p)
        m = out.get(p) if isinstance(out, dict) else None
        if last is None or m is None:
            continue
        now = np.asarray(m.vertices, float)
        d = delta(last["v"], now)
        if _changed(d):
            e = _entry(label, n, p, d, inline=True)
            e["what"] = "asserted"
            _LOG.append(e)
        _LAST[p] = {"step": label, "v": now.copy()}


def missing(mod, pipeline):
    """Steps that `pipeline` calls but that are not wrapped, which the ledger cannot see."""
    return sorted({nm for nm in steps_in(inspect.getsource(mod), pipeline)
                   if not getattr(getattr(mod, nm, None), "_ledgered", False)})


def write(path, extra=None):
    """Write the current run to `path` as JSON, with any `extra` keys added at the top level."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"steps_run": _SEEN["steps"], "steps": [list(t) for t in _STEPS], "entries": _LOG}
    if extra:
        body.update(extra)
    path.write_text(json.dumps(body, indent=1))
    return path


def read(path):
    """The ledger JSON at `path`, or None when it is missing or unreadable."""
    try:
        return json.loads(pathlib.Path(path).read_text())
    except Exception:
        return None


def after_seam(part, seam_step):
    """Every entry that touched `part` after the step `seam_step`, which was meant to be final.

    "After" is by run order: an entry from a step with n past the seam's n, or an inline entry at
    or past it, since inline code right after the seam returns has the same n as the seam. If the
    seam step never ran, nothing protected the part and every entry for it counts.
    """
    n_seam = next((n for nm, n in _STEPS if nm == seam_step), None)
    if n_seam is None:
        return [e for e in _LOG if e["part"] == part]
    return [e for e in _LOG if e["part"] == part
            and (e["n"] > n_seam or (e.get("inline") and e["n"] >= n_seam))]
