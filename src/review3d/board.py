"""A requirement board whose rows are proven able to fail.

A design review collects requirements in the words of the person who asked for the design. Each
one becomes a `Row` here. A row carries the requirement as text, a measurement, a bar the
measurement has to clear, and a note on which picture shows the property to a person. The
`Board` runs every row against a design object and gives each a verdict of green, red or unknown.

Two rules decide the shape of this module.

1. Unknown is never green. A measurement that raised, returned nothing or returned NaN is
   reported as unknown, with the reason. A row with no measurement at all is a judgement only a
   person can make, and it is reported as unknown too, so a board can never look finished because
   nobody measured something.

2. A green row means nothing until it has been seen to go red. For each row a fault can be
   registered, a function that plants the exact defect the row exists to catch in a copy of the
   design. `Board.prove` plants each fault, checks that its row turns red, and checks that the row
   is green again on the untouched design. A row with no fault is reported as unproven, not as
   fine. Rows that turn red under another row's fault are listed too, because a row that reacts to
   somebody else's defect measures something broader than its name says.

The second half of the module is a set of shape measurements over trimesh meshes that such rows
are commonly built from. Each docstring says what the function measures, its unit, and the trap it
avoids. The traps are the useful part. Every one of them is a measurement that once reported a
plausible number about the wrong thing.

The measurements need only numpy, scipy and trimesh. They do not use trimesh's proximity,
containment or ray queries, because those need optional packages, so the point queries are written
here directly.
"""
from __future__ import annotations

import copy as _copy
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import numpy as np
import trimesh
from scipy.spatial import ConvexHull, cKDTree

from base3d.mesh import SAMPLE_SEED, surface_points
from base3d.show import SHEET_BG, frame_on, render

GREEN, RED, UNKNOWN = "green", "red", "unknown"

# What `Board.prove` can say about one row.
PROVEN = "proven"                  # red under its fault, green again without it
MISSED = "missed"                  # the fault was planted and the row stayed green or unknown
UNPROVEN = "unproven"              # no fault registered, so nobody has seen it go red
BASELINE_NOT_GREEN = "baseline not green"   # cannot prove a row that is not green to begin with
FAULT_RAISED = "fault raised"      # the fault function itself failed
NOT_RESTORED = "not restored"      # the row did not read the same on the original afterwards


# ---------------------------------------------------------------------------------------- bars
class Bar:
    """A pass test with a description, so a table can say what the number had to be."""

    def __init__(self, test: Callable[[Any], bool], text: str):
        self.test = test
        self.text = text

    def __call__(self, value) -> bool:
        return bool(self.test(value))

    def __str__(self) -> str:
        return self.text


def at_most(limit: float) -> Bar:
    """Green when the value is no more than `limit`."""
    return Bar(lambda v: v <= limit, f"<= {limit:g}")


def at_least(limit: float) -> Bar:
    """Green when the value is at least `limit`."""
    return Bar(lambda v: v >= limit, f">= {limit:g}")


def between(low: float, high: float) -> Bar:
    """Green when the value lies in the closed range from `low` to `high`.

    Use a range when the defect can come from either side. A floor alone passes a part that went
    too far the other way, for example a grip that wraps a whole ring when it should wrap part of
    one.
    """
    return Bar(lambda v: low <= v <= high, f"{low:g} to {high:g}")


def _bar_text(bar) -> str:
    if bar is None:
        return ""
    return str(bar) if isinstance(bar, Bar) else getattr(bar, "__name__", "custom test")


# ---------------------------------------------------------------------------------------- rows
@dataclass
class Row:
    """One requirement.

    `measure` takes the design object and returns a value. `bar` takes that value and returns
    True for green. With `measure=None` the row is a judgement only a person can make and it is
    always reported as unknown. `look_at` names the picture or view that shows the property, so a
    person reading a red row knows where to check it. `fault` takes a copy of the design, plants
    the defect this row is meant to catch, and returns the faulted design (or None if it changed
    the copy in place).
    """

    name: str
    requirement: str
    measure: Optional[Callable[[Any], Any]] = None
    bar: Optional[Callable[[Any], bool]] = None
    unit: str = ""
    look_at: str = ""
    fault: Optional[Callable[[Any], Any]] = None
    group: str = ""


@dataclass
class Result:
    """The outcome of one row on one design."""

    name: str
    verdict: str
    value: Any = None
    unit: str = ""
    bar: str = ""
    why: str = ""
    requirement: str = ""
    look_at: str = ""
    group: str = ""


@dataclass
class Proof:
    """What fault injection showed about one row."""

    name: str
    status: str
    healthy: Any = None
    faulted: Any = None
    moved: Optional[bool] = None
    also_red: list = field(default_factory=list)
    why: str = ""


def _is_missing(value) -> bool:
    if value is None:
        return True
    try:
        return bool(np.all(np.isnan(np.asarray(value, dtype=float))))
    except (TypeError, ValueError):
        return False


def _same(a, b) -> bool:
    try:
        return bool(np.allclose(np.asarray(a, float), np.asarray(b, float), rtol=1e-9, atol=1e-12,
                                equal_nan=True))
    except (TypeError, ValueError):
        return a == b


def _show(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, (bool, np.bool_)):
        return str(bool(value))
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if math.isnan(f):
        return "nan"
    return f"{f:.3g}" if abs(f) < 1e4 else f"{f:.0f}"


class Board:
    """A list of rows, run together against one design."""

    def __init__(self, rows=None):
        self.rows: list[Row] = []
        for r in rows or ():
            self.add(r)

    def add(self, row: Row) -> Row:
        """Add a row. Names must be unique, because faults and reports find rows by name."""
        if any(r.name == row.name for r in self.rows):
            raise ValueError(f"a row named {row.name!r} already exists")
        self.rows.append(row)
        return row

    def row(self, name: str) -> Row:
        for r in self.rows:
            if r.name == name:
                return r
        raise KeyError(f"no row named {name!r}")

    def fault(self, name: str):
        """Decorator that registers a fault for the row called `name`.

        An unknown name raises at once. A fault aimed at a row that does not exist would otherwise
        never run, and the sweep would report one fewer row as unproven without saying why.
        """
        target = self.row(name)

        def register(fn):
            target.fault = fn
            return fn
        return register

    # ---- running
    def evaluate(self, row: Row, design) -> Result:
        """Run one row. Anything that stops a real reading makes the verdict unknown."""
        base = dict(name=row.name, unit=row.unit, bar=_bar_text(row.bar),
                    requirement=row.requirement, look_at=row.look_at, group=row.group)
        if row.measure is None:
            return Result(verdict=UNKNOWN, why="a person has to judge this, no measurement can",
                          **base)
        try:
            value = row.measure(design)
        except Exception as e:  # noqa: BLE001 - a failed measurement is a finding, not a crash
            return Result(verdict=UNKNOWN, why=f"the measurement raised {type(e).__name__}: {e}",
                          **base)
        if _is_missing(value):
            return Result(verdict=UNKNOWN, value=value,
                          why="the measurement returned no value", **base)
        if row.bar is None:
            return Result(verdict=UNKNOWN, value=value, why="no bar to compare the value with",
                          **base)
        try:
            ok = bool(row.bar(value))
        except Exception as e:  # noqa: BLE001
            return Result(verdict=UNKNOWN, value=value,
                          why=f"the bar could not be applied: {type(e).__name__}: {e}", **base)
        return Result(verdict=GREEN if ok else RED, value=value, **base)

    def run(self, design) -> list[Result]:
        """Run every row against `design`, in the order they were added."""
        return [self.evaluate(r, design) for r in self.rows]

    @staticmethod
    def counts(results) -> dict:
        n = {GREEN: 0, RED: 0, UNKNOWN: 0}
        for r in results:
            n[r.verdict] += 1
        return n

    @staticmethod
    def table(results) -> str:
        """A plain-text table, one line per row, grouped when rows carry a group."""
        if not results:
            return "(no rows)"
        w = max(len(r.name) for r in results)
        lines, last = [], None
        for r in results:
            if r.group and r.group != last:
                lines.append(f"\n{r.group}")
                last = r.group
            value = f"{_show(r.value)} {r.unit}".strip()
            bar = f"(needs {r.bar})" if r.bar else ""
            tail = "  ".join(x for x in (value, bar, r.why) if x)
            lines.append(f"  {r.verdict:7s} {r.name:{w}s}  {tail}")
            if r.verdict != GREEN and r.look_at:
                lines.append(f"  {'':7s} {'':{w}s}  see {r.look_at}")
        n = Board.counts(results)
        lines.append(f"\n  {n[GREEN]} green, {n[RED]} red, {n[UNKNOWN]} unknown")
        return "\n".join(lines).lstrip("\n")

    def report(self, design, out=print) -> list[Result]:
        """Run the board, write the table with `out`, and return the results."""
        results = self.run(design)
        out(self.table(results))
        return results

    # ---- fault injection
    def prove(self, design, copy: Callable[[Any], Any] = _copy.deepcopy) -> list[Proof]:
        """Plant each row's fault in a copy of `design` and check that the row turns red.

        For every row with a fault, three things must hold. The row is green on the healthy
        design (a row already red or unknown cannot show that the fault turned it). It is red on
        the faulted copy. And it reads the same value on the original afterwards, which fails when
        the fault changed the design it was handed instead of the copy.

        The healthy and faulted values are both kept. A row that goes red by a hundredth of the
        bar is proven today and will stop being proven after any small change, with nothing to
        say so, so the margin has to be visible. `moved` is False when the faulted value equals
        the healthy one, which means the measurement never read the fault at all.

        The fault receives the whole design so it can change every copy of a part that some row
        reads. A fault that changes one copy while a row reads another proves nothing about that
        row and introduces a second defect that turns some unrelated row red instead.
        """
        healthy = {r.name: r for r in self.run(design)}
        proofs = []
        for row in self.rows:
            base = healthy[row.name]
            if row.fault is None:
                why = ("judged by a person" if row.measure is None
                       else "no fault registered, so this row has never been seen to go red")
                proofs.append(Proof(row.name, UNPROVEN, healthy=base.value, why=why))
                continue
            if base.verdict != GREEN:
                proofs.append(Proof(row.name, BASELINE_NOT_GREEN, healthy=base.value,
                                    why=f"the healthy design reads {base.verdict}"))
                continue
            try:
                planted = copy(design)
                returned = row.fault(planted)
                planted = planted if returned is None else returned
            except Exception as e:  # noqa: BLE001
                proofs.append(Proof(row.name, FAULT_RAISED, healthy=base.value,
                                    why=f"{type(e).__name__}: {e}"))
                continue
            under = {r.name: r for r in self.run(planted)}
            got = under[row.name]
            also = [n for n, r in under.items()
                    if n != row.name and r.verdict == RED and healthy[n].verdict != RED]
            moved = None if _is_missing(got.value) else not _same(got.value, base.value)
            after = self.evaluate(row, design)
            if got.verdict != RED:
                why = f"the faulted design reads {got.verdict}"
                if got.why:
                    why += f" ({got.why})"
                if moved is False:
                    why += ", and the value did not move, so the measurement never saw the fault"
                proofs.append(Proof(row.name, MISSED, base.value, got.value, moved, also, why))
            elif after.verdict != GREEN or not _same(after.value, base.value):
                proofs.append(Proof(row.name, NOT_RESTORED, base.value, got.value, moved, also,
                                    f"the original now reads {after.verdict} at "
                                    f"{_show(after.value)}, so the fault changed the design it "
                                    f"was given instead of the copy"))
            else:
                proofs.append(Proof(row.name, PROVEN, base.value, got.value, moved, also))
        return proofs

    @staticmethod
    def proof_table(proofs) -> str:
        if not proofs:
            return "(no rows)"
        w = max(len(p.name) for p in proofs)
        lines = []
        for p in proofs:
            nums = (f"healthy {_show(p.healthy)}, faulted {_show(p.faulted)}"
                    if p.status in (PROVEN, MISSED, NOT_RESTORED) else "")
            tail = "  ".join(x for x in (nums, p.why) if x)
            lines.append(f"  {p.status:18s} {p.name:{w}s}  {tail}")
            if p.also_red:
                lines.append(f"  {'':18s} {'':{w}s}  also turned red: {', '.join(p.also_red)}")
        done = sum(p.status == PROVEN for p in proofs)
        lines.append(f"\n  {done} of {len(proofs)} rows proven able to go red")
        return "\n".join(lines)


# ================================================================== geometry used by the metrics
def _dot(a, b):
    return np.einsum("ij,ij->i", a, b)


def _closest_on_triangles(p, a, b, c):
    """Closest point to each p on the triangle (a, b, c) with the same index. All (n, 3)."""
    ab, ac = b - a, c - a
    ap, bp, cp = p - a, p - b, p - c
    d1, d2 = _dot(ab, ap), _dot(ac, ap)
    d3, d4 = _dot(ab, bp), _dot(ac, bp)
    d5, d6 = _dot(ab, cp), _dot(ac, cp)
    va, vb, vc = d3 * d6 - d5 * d4, d5 * d2 - d1 * d6, d1 * d4 - d3 * d2
    out = np.empty_like(p)
    done = np.zeros(len(p), bool)

    def put(mask, value):
        m = mask & ~done
        out[m] = value[m]
        done[m] = True

    with np.errstate(divide="ignore", invalid="ignore"):
        put((d1 <= 0) & (d2 <= 0), a)
        put((d3 >= 0) & (d4 <= d3), b)
        put((vc <= 0) & (d1 >= 0) & (d3 <= 0), a + (d1 / (d1 - d3))[:, None] * ab)
        put((d6 >= 0) & (d5 <= d6), c)
        put((vb <= 0) & (d2 >= 0) & (d6 <= 0), a + (d2 / (d2 - d6))[:, None] * ac)
        e = (d4 - d3) + (d5 - d6)
        put((va <= 0) & ((d4 - d3) >= 0) & ((d5 - d6) >= 0), b + ((d4 - d3) / e)[:, None] * (c - b))
        s = va + vb + vc
        s = np.where(np.abs(s) < 1e-30, 1e-30, s)
        put(np.ones(len(p), bool), a + (vb / s)[:, None] * ab + (vc / s)[:, None] * ac)
    return out


def distance_to_surface(mesh, points) -> np.ndarray:
    """Exact distance from each point to the nearest point on the mesh surface, in mm.

    Only triangles that can be nearer than the best found so far are tested, so the cost stays
    close to the number of points on an ordinary mesh.
    """
    pts = np.atleast_2d(np.asarray(points, float))
    tri = np.asarray(mesh.triangles, float)
    cen = tri.mean(axis=1)
    rad = np.linalg.norm(tri - cen[:, None, :], axis=2).max(axis=1)
    tree = cKDTree(cen)
    k = min(8, len(tri))
    _, near = tree.query(pts, k=k)
    near = np.asarray(near).reshape(len(pts), k)
    rep = np.repeat(pts, k, axis=0)
    t = tri[near.ravel()]
    best = np.linalg.norm(_closest_on_triangles(rep, t[:, 0], t[:, 1], t[:, 2]) - rep,
                          axis=1).reshape(len(pts), k).min(axis=1)
    rmax = float(rad.max())
    out = best.copy()
    for i, p in enumerate(pts):
        cand = np.asarray(tree.query_ball_point(p, best[i] + rmax), int)
        if len(cand) <= k:
            continue
        cand = cand[np.linalg.norm(cen[cand] - p, axis=1) - rad[cand] <= best[i]]
        t = tri[cand]
        q = np.repeat(p[None, :], len(cand), axis=0)
        d = np.linalg.norm(_closest_on_triangles(q, t[:, 0], t[:, 1], t[:, 2]) - q, axis=1)
        out[i] = min(out[i], float(d.min()))
    return out


def inside(mesh, points, chunk_cells=2_000_000) -> np.ndarray:
    """Whether each point is inside a closed mesh, by its winding number.

    The winding number counts how many times the surface wraps the point. It is 1 inside a closed
    shell and 0 outside, and it degrades gently on a mesh with small gaps instead of flipping. A
    point exactly on the surface reads 0.5 and counts as outside.
    """
    pts = np.atleast_2d(np.asarray(points, float))
    tri = np.asarray(mesh.triangles, float)
    step = max(1, chunk_cells // max(len(tri), 1))
    wind = np.empty(len(pts))
    for s in range(0, len(pts), step):
        p = pts[s:s + step]
        a = tri[None, :, 0, :] - p[:, None, :]
        b = tri[None, :, 1, :] - p[:, None, :]
        c = tri[None, :, 2, :] - p[:, None, :]
        la, lb, lc = (np.linalg.norm(x, axis=2) for x in (a, b, c))
        det = np.einsum("ijk,ijk->ij", a, np.cross(b, c))
        div = (la * lb * lc + np.einsum("ijk,ijk->ij", a, b) * lc
               + np.einsum("ijk,ijk->ij", a, c) * lb + np.einsum("ijk,ijk->ij", b, c) * la)
        wind[s:s + step] = (2.0 * np.arctan2(det, div)).sum(axis=1) / (4.0 * np.pi)
    return wind > 0.5


def _blocked_below(mesh, points, eps=1e-6) -> np.ndarray:
    """Whether a vertical ray from each point downward hits the mesh."""
    pts = np.atleast_2d(np.asarray(points, float))
    tri = np.asarray(mesh.triangles, float)
    xy = tri[:, :, :2]
    cen = xy.mean(axis=1)
    rad = np.linalg.norm(xy - cen[:, None, :], axis=2).max(axis=1)
    tree = cKDTree(cen)
    rmax = float(rad.max())
    out = np.zeros(len(pts), bool)
    for i, p in enumerate(pts):
        cand = np.asarray(tree.query_ball_point(p[:2], rmax), int)
        if not len(cand):
            continue
        t = tri[cand]
        a, b, c = t[:, 0], t[:, 1], t[:, 2]
        d = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1])
        ok = np.abs(d) > 1e-12                       # a vertical face cannot stop a vertical ray
        if not ok.any():
            continue
        a, b, c, d = a[ok], b[ok], c[ok], d[ok]
        u = ((b[:, 0] - p[0]) * (c[:, 1] - p[1]) - (c[:, 0] - p[0]) * (b[:, 1] - p[1])) / d
        v = ((c[:, 0] - p[0]) * (a[:, 1] - p[1]) - (a[:, 0] - p[0]) * (c[:, 1] - p[1])) / d
        w = 1.0 - u - v
        hit = (u >= -1e-9) & (v >= -1e-9) & (w >= -1e-9)
        z = u * a[:, 2] + v * b[:, 2] + w * c[:, 2]
        out[i] = bool((hit & (z < p[2] - eps)).any())
    return out


def _points(mesh, n=4000):
    """Every vertex plus seeded surface points, so corners and flat faces are both covered."""
    return np.vstack([np.asarray(mesh.vertices, float), surface_points(mesh, n)])


def principal_axis(mesh) -> np.ndarray:
    """The direction a part is longest in, from its own surface rather than the world axes.

    Unit vector with no meaningful sign. Sampled from the surface, because vertices crowd where a
    mesh is finely tessellated (a socket, a fillet) and would pull the axis toward that detail.
    """
    p = surface_points(mesh, 6000)
    a = np.linalg.svd(p - p.mean(axis=0), full_matrices=False)[2][0]
    return a / np.linalg.norm(a)


def fit_sphere(points):
    """Least-squares sphere through points. Returns (centre, radius)."""
    p = np.asarray(points, float)
    A = np.hstack([2.0 * p, np.ones((len(p), 1))])
    sol, *_ = np.linalg.lstsq(A, (p ** 2).sum(axis=1), rcond=None)
    c = sol[:3]
    return c, float(np.sqrt(max(sol[3] + float(c @ c), 1e-12)))


# ======================================================================================= metrics
def mirror_gap(a, b, normal, origin=(0.0, 0.0, 0.0), n=4000) -> float:
    """How far mesh `b`, reflected across a plane, is from mesh `a`, in mm. The worst point.

    Reflect `b` across the plane through `origin` with `normal`, then take the largest distance
    from either surface to the other. Zero means `b` is the mirror image of `a`. Pass the same
    mesh twice to measure the symmetry of one part (see `mirror_asymmetry`), or two parts of a
    mirrored pair to check they match.

    Traps avoided. The comparison is surface to surface, not vertex to vertex, because the two
    halves of a symmetric part are rarely tessellated alike. The worst point decides and not the
    mean or the median, because a defect such as a bump or a letter covers a small share of the
    surface and an average hides it. And the plane is an argument, because a plane derived from
    the part itself (its centroid, its bounding box) moves with the very defect being measured.
    Take it from the design's own definition of its middle.
    """
    nrm = np.asarray(normal, float)
    nrm = nrm / np.linalg.norm(nrm)
    o = np.asarray(origin, float)
    R = np.eye(4)
    R[:3, :3] = np.eye(3) - 2.0 * np.outer(nrm, nrm)
    R[:3, 3] = 2.0 * float(o @ nrm) * nrm
    bm = b.copy()
    bm.apply_transform(R)
    there = distance_to_surface(a, _points(bm, n)).max()
    back = distance_to_surface(bm, _points(a, n)).max()
    return float(max(there, back))


def mirror_asymmetry(mesh, normal, origin=(0.0, 0.0, 0.0), n=4000) -> float:
    """How far one part is from being symmetric about a plane, in mm. See `mirror_gap`."""
    return mirror_gap(mesh, mesh, normal, origin, n)


def sphericity(mesh, trim=0.02, hull_slack=1.10, n=40000) -> float:
    """How far a part is from a sphere: spread of its radius about its centre over the mean.

    A fraction (0.03 is 3%). A sphere reads close to 0.

    Traps avoided. The surface is sampled evenly instead of reading the vertex list, because a
    bored socket packs hundreds of vertices into a few square millimetres and a vertex statistic
    measures where the mesh is dense rather than where it is round. A bore or socket inside the
    part is not the outside failing to be round, so the convex hull is measured instead, which
    fills holes. The hull is only used when it is at most `hull_slack` times the part's volume,
    because a hull would also fill a real dent or flat, which is the fault this exists to catch.
    The extreme `trim` share of radii at each end is dropped so one small post does not decide.
    A post that protrudes from the sphere still counts, so measure the part before such
    hardware is attached to it.
    """
    look = mesh
    try:
        hull = mesh.convex_hull
        if abs(hull.volume) <= abs(mesh.volume) * hull_slack:
            look = hull
    except Exception:  # noqa: BLE001 - a part with no hull is measured as it is
        pass
    v = surface_points(look, n)
    r = np.linalg.norm(v - v.mean(axis=0), axis=1)
    lo, hi = np.quantile(r, [trim, 1.0 - trim])
    keep = r[(r >= lo) & (r <= hi)]
    return float(keep.std() / max(keep.mean(), 1e-12))


def roughness(mesh, skip=(), percentile=95.0, sliver_share=0.10, floor=0.35) -> float:
    """How sharply the surface turns from one face to the next, in degrees.

    The `percentile` of the angle between neighbouring faces. A smooth surface reads a few
    degrees, a box reads 90.

    Traps avoided. Vertices are welded first, or texture seams split the surface and hide the
    angles across them. Slivers left by boolean operations are dropped (any edge touching a face
    smaller than `sliver_share` of the median face area), because they are mesh debris that
    cannot be seen or printed. Weighting the percentile by area is not the fix for slivers,
    because a real crease is one ring of edges with almost no area either, and area weighting
    hides it too. `skip` is a list of (point, radius) spheres to leave out, for features that are
    sharp on purpose such as the rim of a socket. No exclusion may remove more than (1 - `floor`)
    of the edges, otherwise the measurement reads only the remaining scraps, and the full part is
    measured instead. This reads surface texture. It does not see one bend in an otherwise smooth
    part, because a single crease is a fraction of a percent of the edges. Use `bow` for that.
    """
    w = trimesh.Trimesh(np.asarray(mesh.vertices, float).copy(), np.asarray(mesh.faces).copy(),
                        process=True)
    w.merge_vertices()
    pairs = w.face_adjacency
    ang = np.degrees(w.face_adjacency_angles)
    if not len(ang):
        return float("nan")
    least = max(20, int(len(ang) * floor))
    area = w.area_faces
    tiny = area < max(float(np.median(area)) * sliver_share, 1e-12)
    fine = ~tiny[pairs].any(axis=1)
    if fine.sum() >= least:
        pairs, ang = pairs[fine], ang[fine]
    if len(skip):
        mid = w.triangles_center[pairs].mean(axis=1)
        keep = np.ones(len(ang), bool)
        for at, r in skip:
            keep &= np.linalg.norm(mid - np.asarray(at, float), axis=1) > float(r)
        if keep.sum() >= least:
            ang = ang[keep]
    return float(np.percentile(ang, percentile))


def relief_depth(mesh, facing=None, n=20000, rounds=3) -> float:
    """How deep the deepest recess in a roughly spherical surface goes, in mm.

    Fits a sphere to the outer surface and returns how far the lowest point lies inside it. Use
    it to check that engraved detail (a face, a panel seam) is still there and deep enough to
    paint or to read. A plain sphere reads close to 0. With `facing`, only the half of the part
    whose direction from the centre is within 90 degrees of `facing` is searched.

    Traps avoided. The sphere is fitted again after dropping the innermost and outermost points,
    because a fit to every point (or a radius taken from a vertex median) is pulled inward by
    the recesses being measured, and then reports them shallower than they are. Fit on the part
    without hardware that protrudes from it, for the same reason.
    """
    p = surface_points(mesh, n)
    c, R = fit_sphere(p)
    for _ in range(rounds):
        r = np.linalg.norm(p - c, axis=1) - R
        lo, hi = np.quantile(r, [0.15, 0.95])
        c, R = fit_sphere(p[(r >= lo) & (r <= hi)])
    v = np.vstack([np.asarray(mesh.vertices, float), p])
    d = v - c
    if facing is not None:
        f = np.asarray(facing, float)
        d = d[d @ f > 0]
    depth = R - np.linalg.norm(d, axis=1)
    return float(max(0.0, depth.max())) if len(depth) else float("nan")


def penetration(a, b, n=4000) -> float:
    """How deep one part's material goes inside the other, in mm. 0 when they do not overlap.

    The largest distance from the surface of one part to a point of the other part that lies
    inside it, taken both ways. Use it to check that a peg is seated (buried by about its
    intended depth) and not driven through.

    Traps avoided. Depth, not overlap volume. A correctly seated rod displaces a volume that grows
    with its thickness, so a volume bar either fails good joints on a big part or passes bad ones
    on a small part, while depth means the same thing for a rod, a ball or a flat sole. Both parts
    are tested against the solid, never a convex hull, because a hull fills the gaps and sockets
    whose presence is the question.
    """
    worst = 0.0
    for host, guest in ((a, b), (b, a)):
        p = _points(guest, n)
        lo, hi = host.bounds
        p = p[np.all((p >= lo) & (p <= hi), axis=1)]
        if not len(p):
            continue
        p = p[inside(host, p)]
        if len(p):
            worst = max(worst, float(distance_to_surface(host, p).max()))
    return worst


def gap(a, b, n=4000) -> float:
    """The clearance between two parts, in mm. 0 when they touch or overlap.

    The smallest distance from the surface of one to the surface of the other, measured from
    every vertex and `n` seeded surface points of each. Exact at those points, so on a coarse
    sample it can read a little high, never low by more than the sample spacing allows.

    Traps avoided. Clearance is measured to the other part's solid surface, not to its convex
    hull or bounding box, because a hull bridges sockets and gaps and reports a part in open
    air as seated. When the parts overlap the answer is 0 and `penetration` says by how much.
    """
    if penetration(a, b, n=max(500, n // 4)) > 1e-9:
        return 0.0
    there = distance_to_surface(a, _points(b, n)).min()
    back = distance_to_surface(b, _points(a, n)).min()
    return float(min(there, back))


def tip_ratio(parts, contact_tol=0.6) -> float:
    """Where the centre of mass sits between the footprint's centre and its tipping line.

    0 means right over the middle of the footprint, 1 means on the edge where the design starts
    to tip, and more than 1 means it falls over. `parts` is one mesh or a list of meshes resting
    on the plane of the lowest point. The footprint is the convex hull of every vertex within
    `contact_tol` mm of that plane.

    Traps avoided. Standing is a static question, the centre of mass over the support, not
    whether the design survives a push (anything on wheels rolls when pushed). Mass needs closed
    solids, so a mesh that is not watertight raises instead of returning a centre of mass that
    means nothing. A footprint that is a point or a line has no inside and reads infinity.
    """
    meshes = [parts] if isinstance(parts, trimesh.Trimesh) else list(parts)
    vol, mom = 0.0, np.zeros(3)
    for m in meshes:
        if not m.is_watertight:
            raise ValueError("a part is not watertight, so it has no centre of mass")
        vol += float(m.volume)
        mom += float(m.volume) * np.asarray(m.center_mass, float)
    com = mom / vol
    floor = min(float(m.bounds[0][2]) for m in meshes)
    touch = np.vstack([np.asarray(m.vertices, float) for m in meshes])
    touch = touch[touch[:, 2] <= floor + contact_tol][:, :2]
    try:
        hull = ConvexHull(touch)
    except Exception:  # noqa: BLE001 - qhull refuses a point or a line
        return float("inf")
    c = touch[hull.vertices].mean(axis=0)
    nrm, off = hull.equations[:, :2], hull.equations[:, 2]
    reach = -(nrm @ c + off)                # distance from the centre to each edge, inward
    return float(max(0.0, float(((nrm @ (com[:2] - c)) / reach).max())))


def first_layer_area(mesh, layer=0.2) -> float:
    """Area of the part where it meets the build plate, in square mm.

    The cross-section half a layer above the lowest point, so the first printed layer. A part
    with a small first layer depends on its neighbours or a brim to stay down.

    Traps avoided. A section exactly at the lowest point is degenerate (the bottom faces lie in
    the plane), so it is cut half a layer up. Holes in the section count as negative area,
    because each piece of the outline is oriented by its face's outward normal.
    """
    h = float(mesh.bounds[0][2]) + layer / 2.0 + 1e-9
    tri = np.asarray(mesh.triangles, float)
    nrm = np.asarray(mesh.face_normals, float)
    s = tri[:, :, 2] - h
    cross = (s.min(axis=1) < 0) & (s.max(axis=1) > 0)
    total = 0.0
    for t, sv, nv in zip(tri[cross], s[cross], nrm[cross]):
        pts = []
        for i, j in ((0, 1), (1, 2), (2, 0)):
            if (sv[i] < 0) != (sv[j] < 0):
                k = sv[i] / (sv[i] - sv[j])
                pts.append(t[i] + k * (t[j] - t[i]))
        if len(pts) != 2:
            continue
        p, q = pts[0][:2], pts[1][:2]
        if float((q - p) @ np.array([-nv[1], nv[0]])) < 0:
            p, q = q, p
        total += 0.5 * float(p[0] * q[1] - q[0] * p[1])
    return float(total)


def overhang_share(mesh, angle=45.0, min_height=4.0, n=40000, seed=SAMPLE_SEED) -> float:
    """Share of the surface that would need support to print, in percent.

    A surface point needs support when its face points downward more steeply than `angle`
    degrees from horizontal, nothing of the part lies directly below it, and it sits more than
    `min_height` mm above the plate (the lowest point).

    Traps avoided. Steep downward faces alone flag the underside of every curve resting on
    something, and those print. A straight ray down from each point separates a face over open
    air from one over material. The height floor matters too. A surface a millimetre or two
    above the plate bridges or rests on the brim, so only overhangs high enough to sag count.
    Tilt a flat part and check this goes up before trusting a low reading.
    """
    pts, fid = trimesh.sample.sample_surface(mesh, n, seed=seed)
    nz = np.asarray(mesh.face_normals, float)[fid][:, 2]
    floor = float(mesh.bounds[0][2])
    steep = (nz < -math.cos(math.radians(angle))) & (pts[:, 2] - floor > min_height)
    if not steep.any():
        return 0.0
    open_air = ~_blocked_below(mesh, pts[steep] - np.array([0.0, 0.0, 1e-4]))
    return float(100.0 * open_air.sum() / len(pts))


def length_along_axis(mesh, axis=None) -> float:
    """How long a part is along its own long axis (or along `axis`), in mm.

    Trap avoided. A bounding box on world axes misreads any part that is turned, by up to 40% for
    a part at 45 degrees. On a bent part the long axis averages the two arms of the bend, so
    compare like with like.
    """
    ax = principal_axis(mesh) if axis is None else np.asarray(axis, float) / np.linalg.norm(axis)
    return float(np.ptp(np.asarray(mesh.vertices, float) @ ax))


def size_ratio(a, b, by="length") -> float:
    """Size of `a` over size of `b`, by "length" (along each part's own axis) or "volume".

    A ratio with no unit. Use it for "the arm is long enough next to the body" or for "this is the
    same part as that one" (a volume ratio close to 1).

    Trap avoided. Measure the parts that a requirement is about. A part with its connectors or a
    grip attached is a different size from the part itself, and a ratio of the attached parts
    reports the attachments.
    """
    if by == "length":
        return length_along_axis(a) / length_along_axis(b)
    if by == "volume":
        return float(abs(a.volume) / abs(b.volume))
    raise ValueError(f"by must be 'length' or 'volume', not {by!r}")


def axis_angle(u, v) -> float:
    """The angle between two lines, in degrees from 0 to 90. The sign of either is ignored.

    Use with `principal_axis` for "turned across", "stands upright" or "sticks out".

    Trap avoided. This is about lines, not directions. An axis from a principal component has no
    sign, so a part turned half a turn reads the same angle as before. A question about which way
    a thing points (toes forward, a cup upright and not upside down) needs a signed marker on the
    part, not this.
    """
    u = np.asarray(u, float) / np.linalg.norm(u)
    v = np.asarray(v, float) / np.linalg.norm(v)
    return float(np.degrees(np.arccos(min(1.0, abs(float(u @ v))))))


def bow(mesh, slices=16, least=20, n=20000) -> float:
    """How far a long part's centreline strays from the straight line joining its ends, in mm.

    The part is cut into `slices` bands along its long axis and the centre of the surface in each
    band is taken. The answer is the largest distance of a band centre from the line through the
    first and last. A straight rod reads close to 0 and a bent one reads how far the bend stands
    off.

    Traps avoided. The bands are of surface points, not vertices, because a capsule carries all
    its vertices in its rounded ends and none along the shaft, so vertex bands find nothing in the
    middle. This tells straight from bent. It cannot tell one bend from two, or a corner from a
    curve, because the band centres jump wherever the cross-section changes and that jump can be
    larger than the bend.
    """
    p = surface_points(mesh, n)
    c = p.mean(axis=0)
    ax = np.linalg.svd(p - c, full_matrices=False)[2][0]
    t = (p - c) @ ax
    edges = np.linspace(float(t.min()), float(t.max()), slices + 1)
    mids = [p[(t >= a) & (t < b)].mean(axis=0) for a, b in zip(edges[:-1], edges[1:])
            if int(((t >= a) & (t < b)).sum()) >= least]
    if len(mids) < 4:
        return float("nan")
    m = np.asarray(mids)
    d = m[-1] - m[0]
    d = d / max(float(np.linalg.norm(d)), 1e-12)
    off = (m - m[0]) - np.outer((m - m[0]) @ d, d)
    return float(np.linalg.norm(off, axis=1).max())


def out_of_round(mesh, axis=None, band=(0.25, 0.75), slices=16, bins=36, n=60000) -> float:
    """How far a part that should be round about an axis is from round, at its worst height.

    A fraction (0.03 is 3%). For each slice across the middle `band` of the part, the outer radius
    is taken in each of `bins` angular sectors, and the slice scores the spread of those radii
    over their mean. The worst slice is returned. A plain cylinder or cup reads close to 0, raised
    lettering or a dent reads more.

    Traps avoided. One height at a time, because a part that tapers on purpose varies in radius
    along its length whether or not it is round. The outer radius per sector, because a hollow
    part has an inner wall and mixing both measures the wall thickness. The worst slice and not
    the average, because lettering sits at a few heights and an average hides it.
    """
    p = surface_points(mesh, n)
    ax = principal_axis(mesh) if axis is None else np.asarray(axis, float) / np.linalg.norm(axis)
    c = p.mean(axis=0)
    t = (p - c) @ ax
    lo, hi = np.quantile(t, band)
    p, t = p[(t >= lo) & (t <= hi)], t[(t >= lo) & (t <= hi)]
    u = np.eye(3)[int(np.argmin(np.abs(ax)))]
    u = u - float(u @ ax) * ax
    u = u / np.linalg.norm(u)
    w = np.cross(ax, u)
    edges = np.linspace(lo, hi, slices + 1)
    worst = float("nan")
    for k in range(slices):
        sl = (t >= edges[k]) & (t < edges[k + 1])
        if sl.sum() < bins * 4:
            continue
        d = p[sl] - c
        d = d - np.outer(d @ ax, ax)
        r = np.linalg.norm(d, axis=1)
        idx = np.clip(((np.arctan2(d @ w, d @ u) + np.pi) / (2 * np.pi) * bins).astype(int),
                      0, bins - 1)
        rim = np.array([r[idx == b].max() for b in range(bins) if (idx == b).any()])
        if len(rim) < bins * 0.8:
            continue
        score = float(rim.std() / max(rim.mean(), 1e-12))
        worst = score if math.isnan(worst) else max(worst, score)
    return worst


def tunnel_count(mesh) -> int:
    """How many holes pass right through a closed part (its genus, summed over its bodies).

    A plain solid reads 0, a ring reads 1. Holes that are part of the design (an axle bore, a
    socket that opens on both sides) count too, so compare against the expected number.

    Trap avoided. A mesh that is not closed has no genus, and its Euler number then describes the
    open edges instead, so it raises rather than return a count.
    """
    if not mesh.is_watertight:
        raise ValueError("the mesh is not closed, so it has no genus")
    return int(round((2 * int(mesh.body_count) - int(mesh.euler_number)) / 2))


def brightness(image, background, tol=14.0, least=150) -> float:
    """How bright the model is in a rendered picture, from 0 (black) to 1 (white).

    The mean over the pixels that differ from `background` by more than `tol` (on a 0 to 255
    scale). A view where this is low is too dark to judge the model from.

    Traps avoided. The background is excluded, because a dark background is a choice and a dark
    model is a fault, and a whole-picture mean mixes the two. Fewer than `least` model pixels
    raises, because a view with no model in it is not a bright or dark view of the model.
    """
    img = np.asarray(image, float)[..., :3]
    bg = np.asarray(background, float)
    if bg.max() <= 1.0:
        bg = bg * 255.0
    on = np.linalg.norm(img - bg, axis=-1) > tol
    if int(on.sum()) < least:
        raise ValueError(f"only {int(on.sum())} model pixels in view")
    return float(img[on].mean() / 255.0)


def view_brightness(mesh, azim, elev, size=(320, 240), bg=SHEET_BG, **render_kw) -> float:
    """`brightness` of the model rendered by base3d from a direction, in degrees.

    `azim` turns about +Z from +X and `elev` is the angle above the horizontal, as in
    base3d.frame_on.

    Trap avoided. Where the pictures a person is asked to judge already exist, measure those
    files with `brightness` instead. A private render can pass on a picture nobody looks at.
    """
    eye, target = frame_on(mesh, azim, elev)
    img = render(mesh, eye, target, size=size, bg=bg, **render_kw)
    return brightness(img, bg)


__all__ = [
    "GREEN", "RED", "UNKNOWN", "PROVEN", "MISSED", "UNPROVEN", "BASELINE_NOT_GREEN",
    "FAULT_RAISED", "NOT_RESTORED", "Bar", "at_most", "at_least", "between", "Row", "Result",
    "Proof", "Board", "distance_to_surface", "inside", "principal_axis", "fit_sphere",
    "mirror_gap", "mirror_asymmetry", "sphericity", "roughness", "relief_depth", "penetration",
    "gap", "tip_ratio", "first_layer_area", "overhang_share", "length_along_axis", "size_ratio",
    "axis_angle", "bow", "out_of_round", "tunnel_count", "brightness", "view_brightness",
]
