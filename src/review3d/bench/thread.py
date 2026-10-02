"""Comment threads on submissions.

A submission is the start of a conversation between the reviewer and the assistant. Each thread
holds four things.

  1. What the reviewer circled or moved. This is the mark or the exact transform, already stored
     in the submission.
  2. What the assistant replied, one entry per turn of work.
  3. What the assistant did, as a picture rendered from the reviewer's own camera, with the old
     position drawn behind it as an orange shadow when something moved.
  4. The reviewer's follow-ups, on the same thread, so a disagreement stays attached to the thing
     it is about.

The thread is stored inside the submission file itself (`submissions/<id>.json` gains `thread` and
`state`), not in a second store, so there is one record per conversation. Other tools may read the
same `submissions/*.json` files, so reply pictures are written as `<id>-reply-N.png` and never as
JSON files of their own.
"""
import datetime as _dt
import json
import pathlib

import numpy as np
import trimesh

# The folders are set by the project with `configure()`. Until then they are `./submissions` and
# `./stages` under the working directory.
SUBS = pathlib.Path.cwd() / "submissions"
STAGES = pathlib.Path.cwd() / "stages"

#: The two authors a thread entry can have.
REVIEWER = "reviewer"
ASSISTANT = "assistant"


def configure(subs, stages):
    """Use a project's submissions folder and stage files."""
    global SUBS, STAGES
    SUBS, STAGES = pathlib.Path(subs), pathlib.Path(stages)


#: The states a thread can be in. `new` is unanswered and waiting on the assistant. `answered`
#: waits on the reviewer. `fixed` is done, with a measured change behind it. `wont` is declined,
#: with the reason written in the thread.
STATES = ("new", "answered", "fixed", "wont")


def path(sub_id):
    return SUBS / f"{sub_id}.json"


def _normalise(d):
    """Give a submission an empty thread if it has none, and map unknown states to `new`.

    Some writers store `state: "published"` to mean that the reviewer sent the submission. For a
    thread that is the same as `new` (unanswered), so any value outside `STATES` reads as `new`.
    Without this the submission would fall out of every filter on the threads page.
    """
    d.setdefault("thread", [])
    if d.get("state") not in STATES:
        d["state"] = "new"
    return d


def read(sub_id):
    return _normalise(json.loads(path(sub_id).read_text()))


def write(d):
    path(d["id"]).write_text(json.dumps(d, indent=1, ensure_ascii=False))
    return d


def every(newest_first=True):
    """Every submission on disk, as full dicts."""
    out = []
    for f in sorted(SUBS.glob("*.json"), reverse=newest_first):
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        out.append(_normalise(d))
    return out


def append(sub_id, by, text, shot=None, did=None, state=None):
    """Add one turn to the thread. `by` is `REVIEWER` or `ASSISTANT` and nothing else.

    The author is checked so that the history of a thread can be trusted to say who wrote what.
    `shot` is the file name of a reply picture, `did` describes what changed (in numbers, when
    something changed), and `state` moves the thread to one of `STATES`. A reviewer entry without
    an explicit state reopens the thread as `new`.
    """
    if by not in (REVIEWER, ASSISTANT):
        raise ValueError(f"an entry is by {REVIEWER!r} or by {ASSISTANT!r}, not {by!r}")
    d = read(sub_id)
    entry = {"by": by, "at": _dt.datetime.now().astimezone().isoformat(),
             "text": (text or "").strip()}
    if shot:
        entry["shot"] = shot
    if did:
        entry["did"] = did
    d["thread"].append(entry)
    if state:
        if state not in STATES:
            raise ValueError(f"{state!r} is not one of {STATES}")
        d["state"] = state
    elif by == REVIEWER:
        d["state"] = "new"
    write(d)
    return d


def _stage_mesh(stage, drop_labels=True):
    """The parts of one stage file, as {part name: geometry}.

    Meshes named `no` followed by digits are number labels drawn in the viewer, and they are left
    out unless `drop_labels` is False.
    """
    sc = trimesh.load(STAGES / f"{stage}.glb", force="scene")
    keep = {}
    for name, g in sc.geometry.items():
        if drop_labels and (name.startswith("no") and name[2:].isdigit()):
            continue
        keep[name] = g
    return keep


def _disc(at, r, normal, colour=(232, 150, 40, 255), segments=48):
    """An orange ring at `at`, facing `normal`, matching the circle drawn on the page."""
    ring = trimesh.creation.annulus(r_min=r * 0.86, r_max=r, height=max(r * 0.05, 0.25),
                                    sections=segments)
    n = np.asarray(normal, float)
    if np.linalg.norm(n) < 1e-9:
        n = np.array([0.0, 0.0, 1.0])
    n = n / np.linalg.norm(n)
    ring.apply_transform(trimesh.geometry.align_vectors([0, 0, 1], n))
    ring.apply_translation(np.asarray(at, float))
    ring.visual.face_colors = colour
    return ring


def render_reply(sub_id, name=None, shadow=None, highlight=None, size=(1400, 900),
                 stage=None, mark=True):
    """Render the current state of the stage from the reviewer's camera and save it as a PNG.

    The camera is the one stored with the submission, so the reply picture and the reviewer's
    picture line up and the difference between them is the answer.

    `shadow` is {part name: mesh}, drawn in orange together with the current state, to show where
    something was before it moved. `highlight` names one part to tint. `mark` draws the reviewer's
    circle when the submission has one. Returns the file name, which is stored in `SUBS`.
    """
    from base3d import show

    d = read(sub_id)
    stage = stage or d.get("stage") or "assembled"
    cam = d.get("camera") or {}
    eye = np.asarray(cam.get("eye") or (200, 200, 120), float)
    at = np.asarray(cam.get("at") or (0, 0, 60), float)

    parts = _stage_mesh(stage)
    pieces, colours = [], []
    PART = (238, 238, 233)
    GHOST = (232, 150, 40)
    HOT = (120, 205, 180)
    for pname, g in parts.items():
        m = trimesh.Trimesh(np.asarray(g.vertices, float), np.asarray(g.faces), process=False)
        c = HOT if (highlight and pname == highlight) else PART
        pieces.append(m)
        colours.append(np.tile(c, (len(m.faces), 1)))
    for pname, g in (shadow or {}).items():
        m = trimesh.Trimesh(np.asarray(g.vertices, float), np.asarray(g.faces), process=False)
        pieces.append(m)
        colours.append(np.tile(GHOST, (len(m.faces), 1)))
    mk = d.get("mark") if mark else None
    if mk and mk.get("at"):
        ring = _disc(mk["at"], float(mk.get("r_mm") or 3.0), eye - np.asarray(mk["at"], float))
        pieces.append(ring)
        colours.append(np.tile(GHOST, (len(ring.faces), 1)))

    whole = trimesh.util.concatenate(pieces)
    face_colors = np.vstack(colours) / 255.0
    img = show.render(whole, eye=eye, target=at, size=size, face_colors=face_colors,
                      bg=(0.055, 0.075, 0.09))
    # `show.render` returns an array, so it is converted to 8-bit before it is written.
    arr = np.asarray(img)
    if arr.dtype != np.uint8:
        arr = np.clip(arr * 255.0, 0, 255).astype(np.uint8)
    n = name or f"{sub_id}-reply-{len([e for e in d['thread'] if e.get('shot')]) + 1}.png"
    out = SUBS / n
    show.save_png(arr[..., :3], out)
    return out.name


def say(sub_id, text, state=None, shadow=None, highlight=None, did=None, picture=True):
    """Reply on a thread as the assistant, with a picture from the reviewer's camera."""
    shot = render_reply(sub_id, shadow=shadow, highlight=highlight) if picture else None
    return append(sub_id, ASSISTANT, text, shot=shot, did=did, state=state)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--list":
        for d in every():
            n = len(d["thread"])
            print(f"{d['state']:9s} {d['id']:28s} {n:2d} repl  "
                  f"{(d.get('said') or '')[:70]}")
    else:
        print(__doc__.strip().splitlines()[0])
        print(f"{len(every())} submissions in {SUBS}")
