"""Write a project's stages in the format the review bench reads.

A stage is one view of the model (for example the parts laid out, or the model assembled), made of
named parts. `write_stages` takes {stage name: {part name: trimesh.Trimesh}} and writes

    <out_dir>/stages.json
    <out_dir>/stages/<stage name>.glb

Each GLB holds one node per part, named after the part, with per-face colours and per-vertex
normals (from `base3d.show.display_mesh`), so the page can light it with smooth shading.

`stages.json` has one entry per stage, in the order given, plus a `meta` entry. The page lists the
stages in that order and opens the first one. A stage entry holds

- `file`, the GLB path relative to `stages.json`
- `pieces`, the number of parts
- `size_mm`, the extent of the whole stage on x, y and z
- `faces`, an optional unit vector for the direction the model faces, which sets the starting
  camera, or null
- `frames`, each part's centre and principal axes (longest first, right-handed), which the page
  uses when the reviewer turns a part about its own axes

Two names have a meaning on the page. A stage named "parts" gets an "apart" slider when `meta`
holds `offsets` ({part name: [x, y, z]}, the offset of each part from its assembled place). Parts
named "base", or "no" followed by digits (number labels), are drawn but cannot be picked, and they
get no frame.
"""
import datetime as _dt
import json
import pathlib

import numpy as np
import trimesh

from base3d import show

PART_COLOUR = (236, 236, 231, 255)


def _not_pickable(name):
    return name == "base" or (name.startswith("no") and name[2:].isdigit())


def _scene(parts, colours):
    sc = trimesh.Scene()
    for name, mesh in parts.items():
        g = show.display_mesh(mesh)
        colour = colours.get(name, PART_COLOUR)
        g.visual = trimesh.visual.ColorVisuals(g, face_colors=np.tile(colour, (len(g.faces), 1)))
        sc.add_geometry(g, geom_name=name, node_name=name)
    return sc


def _frame(mesh):
    """The part's centre, principal axes (longest first, right-handed) and spread along each."""
    pv = np.asarray(mesh.vertices, float)
    pc = pv.mean(axis=0)
    _u, sv, vt = np.linalg.svd(pv - pc, full_matrices=False)
    ax = vt[:3].copy()
    if np.linalg.det(ax) < 0:
        ax[2] = -ax[2]
    return {
        "centre": [round(float(v), 3) for v in mesh.bounds.mean(axis=0)],
        "axes": [[round(float(v), 5) for v in a] for a in ax],
        "spread": [round(float(v) / max(len(pv), 1) ** 0.5, 3) for v in sv[:3]],
    }


def write_stages(stages, out_dir, faces=None, offsets=None, colours=None, meta=None,
                 labels=None, order=None, default=None):
    """Write `stages` ({stage: {part: Trimesh}}) for the bench and return the stages.json dict.

    `faces` is the direction the model faces (three numbers) or None. `offsets` is
    {part name: [x, y, z]} for the "parts" stage's slider. `colours` is {part name: RGBA}, and any
    part not named there is drawn in `PART_COLOUR`. `meta` is a dict of extra keys for the `meta`
    entry.
    """
    for name in list(order or []) + ([default] if default else []) + list(labels or {}):
        if name not in stages:
            raise ValueError(f"{name!r} is named in labels, order or default but is not a stage")
    out_dir = pathlib.Path(out_dir)
    folder = out_dir / "stages"
    folder.mkdir(parents=True, exist_ok=True)
    colours = colours or {}
    if faces is not None:
        f = np.asarray(faces, float)
        faces = [round(float(v), 4) for v in f / max(float(np.linalg.norm(f)), 1e-9)]
    info = {}
    names = set()
    for stage, parts in stages.items():
        if stage == "meta" or not stage or "/" in stage or "\\" in stage or stage.startswith("."):
            raise ValueError(f"{stage!r} cannot be used as a stage name")
        if not parts:
            raise ValueError(f"stage {stage!r} has no parts")
        sc = _scene(parts, colours)
        (folder / f"{stage}.glb").write_bytes(trimesh.exchange.gltf.export_glb(sc))
        whole = trimesh.util.concatenate([trimesh.Trimesh(m.vertices, m.faces, process=False)
                                          for m in parts.values()])
        extent = whole.bounds[1] - whole.bounds[0]
        frames = {}
        for name, mesh in parts.items():
            names.add(name)
            if _not_pickable(name):
                continue
            try:
                frames[name] = _frame(mesh)
            except Exception:
                continue
        info[stage] = {"file": f"stages/{stage}.glb",
                       "pieces": len(parts),
                       "size_mm": [round(float(v), 1) for v in extent],
                       "faces": faces,
                       "frames": frames}
    info["meta"] = {"part_order": sorted(names),
                    "built_at": _dt.datetime.now(_dt.timezone.utc).isoformat()}
    if offsets:
        info["meta"]["offsets"] = {n: [round(float(v), 3) for v in off]
                                   for n, off in offsets.items()}
    for key, value in (("stage_labels", labels), ("stage_order", order), ("default_stage", default)):
        if value:
            info["meta"][key] = dict(value) if key == "stage_labels" else (list(value) if key == "stage_order" else value)
    if meta:
        info["meta"].update(meta)
    (out_dir / "stages.json").write_text(json.dumps(info, indent=2))
    return info


def stages_from_files(paths, stage="parts"):
    """One stage built from mesh files, each part named after its file name without the suffix."""
    parts = {}
    for p in paths:
        p = pathlib.Path(p)
        mesh = trimesh.load(p, force="mesh")
        name = p.stem
        if name in parts:
            raise ValueError(f"two files give the part name {name!r}")
        parts[name] = mesh
    return {stage: parts}
