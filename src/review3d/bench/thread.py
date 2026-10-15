"""Comment threads on submissions.

A submission is the start of a conversation between the reviewer and the assistant. Each thread
holds four things.

  1. What the reviewer circled or moved. This is the mark or the exact transform, already stored
     in the submission.
  2. What the assistant replied, one entry per turn of work.
  3. What the assistant did, as a picture rendered from the reviewer's own camera, lens and frame,
     so it lines up with the reviewer's picture. Where something was before the change, an orange
     shadow shows it: either the old mesh, drawn in 3D, or the parts of the reviewer's own picture
     that are no longer covered by the model.
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
import struct
import zlib

import numpy as np
import trimesh

# The folders are set by the project with `configure()`. Until then they are `./submissions` and
# `./stages` under the working directory.
SUBS = pathlib.Path.cwd() / "submissions"
STAGES = pathlib.Path.cwd() / "stages"

#: The two authors a thread entry can have.
REVIEWER = "reviewer"
ASSISTANT = "assistant"

#: The bench page's background colour, as 8-bit RGB (`scene.background` in twist.html). In the
#: reviewer's picture a pixel of this colour is empty space.
BENCH_BG = (16, 19, 22)
#: The bench page's lens: the vertical field of view of its camera, in degrees. A submission stores
#: its own as `view.fov`, and this is used only when it does not.
BENCH_FOV = 32.0
#: The widest reply picture, in pixels. A reply keeps the reviewer's aspect and is scaled down to
#: this width when the reviewer's canvas was wider.
MAX_WIDTH = 1600
#: The reply size for a submission that does not store the reviewer's frame.
DEFAULT_SIZE = (1400, 900)
#: How far a pixel may be from BENCH_BG, in any channel, and still count as empty space. The floor
#: grid the page draws is at most 32 away from the background, and lit faces of the model are
#: further away than this.
MODEL_TOL = 40
#: The colour of every shadow and mark in a reply picture.
SHADOW_COLOUR = (232, 150, 40)
# The background a reply is first drawn on, a colour shading does not produce, so that the pixels
# the model covers can be told from empty space exactly. It is replaced by BENCH_BG before saving.
_UNDRAWN = (255, 0, 254)


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
        except (OSError, ValueError):        # unreadable, or not JSON (a file still being written)
            continue
        out.append(_normalise(d))
    return out


def append(sub_id, by, text, shot=None, did=None, state=None, caption=None):
    """Add one turn to the thread. `by` is `REVIEWER` or `ASSISTANT` and nothing else.

    The author is checked so that the history of a thread can be trusted to say who wrote what.
    `shot` is the file name of a reply picture, `did` describes what changed (in numbers, when
    something changed), and `state` moves the thread to one of `STATES`. A reviewer entry without
    an explicit state reopens the thread as `new`.

    `caption` is the line the page shows under the picture. Without it the page says the picture
    is from the reviewer's camera with the old position in orange, which is what `say` draws. A
    picture drawn another way (a close-up, a cut-away, a view from the side) needs its own caption,
    or the page describes it wrongly.
    """
    if by not in (REVIEWER, ASSISTANT):
        raise ValueError(f"an entry is by {REVIEWER!r} or by {ASSISTANT!r}, not {by!r}")
    d = read(sub_id)
    entry = {"by": by, "at": _dt.datetime.now().astimezone().isoformat(),
             "text": (text or "").strip()}
    if shot:
        entry["shot"] = shot
        if caption:
            entry["caption"] = str(caption).strip()
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


def _is_label(name):
    return name.startswith("no") and name[2:].isdigit()


def _stage_mesh(stage, drop_labels=True):
    """The parts of one stage file, as {part name: mesh}, placed the way the bench page places them.

    The page bakes each node's world transform into its geometry and names the part after the
    node, so the same is done here. A GLB whose nodes carry a translation or a turn is then drawn
    where the reviewer saw it. Only meshes are kept, as on the page. Meshes named `no` followed by
    digits are number labels, and they are left out unless `drop_labels` is False.
    """
    sc = trimesh.load(STAGES / f"{stage}.glb", force="scene")
    keep = {}
    for node in sc.graph.nodes_geometry:
        T, gname = sc.graph[node]
        g = sc.geometry.get(gname)
        if not isinstance(g, trimesh.Trimesh):
            continue
        pname = node or gname
        if drop_labels and _is_label(pname):
            continue
        m = g.copy()
        m.apply_transform(np.asarray(T, float))
        keep[pname] = m
    return keep


def _explode(parts, stage, apart):
    """Move the parts as far apart as the reviewer had them on the page.

    On the stage named "parts" the page has an "apart" slider when `stages.json` (beside the
    stages folder) holds `meta.offsets`. The stage file holds the parts fully apart, and the
    slider moves each part back toward its assembled place by (1 - apart) times its offset. The
    submission stores the slider as `view.apart`.
    """
    if stage != "parts" or apart is None or float(apart) >= 1:
        return parts
    info = STAGES.parent / "stages.json"
    if not info.exists():
        return parts
    offsets = (json.loads(info.read_text()).get("meta") or {}).get("offsets") or {}
    k = -(1.0 - float(apart))
    for pname, m in parts.items():
        off = offsets.get(pname)
        if off:
            m.apply_translation(np.asarray(off, float) * k)
    return parts


def reply_frame(d, max_width=MAX_WIDTH):
    """The lens and picture size that reproduce the reviewer's view of submission `d`.

    Returns (fov, (width, height)). `fov` is the page camera's vertical field of view in degrees
    (`view.fov`), which is also what `base3d.show.render` takes. The size is the reviewer's canvas
    (`view.width` by `view.height`, in device pixels), scaled down so the width is at most
    `max_width`, so the reply has the reviewer's aspect. Without a stored frame it is DEFAULT_SIZE.
    """
    view = d.get("view") or {}
    fov = float(view.get("fov") or BENCH_FOV)
    w, h = view.get("width"), view.get("height")
    if not (w and h):
        return fov, DEFAULT_SIZE
    w, h = float(w), float(h)
    k = min(1.0, float(max_width) / w)
    return fov, (max(1, round(w * k)), max(1, round(h * k)))


def _unfilter_slow(line, prior, bpp, kind):
    """Undo the PNG Average (3) or Paeth (4) filter on one row. Both depend on the pixel to the left,
    so they are done byte by byte. Browsers rarely use them for canvas pictures."""
    line, prior = bytes(line), bytes(prior)
    cur = bytearray(len(line))
    for i in range(len(line)):
        a = cur[i - bpp] if i >= bpp else 0
        b = prior[i]
        if kind == 3:
            pred = (a + b) >> 1
        else:
            c = prior[i - bpp] if i >= bpp else 0
            p = a + b - c
            pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
            pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
        cur[i] = (line[i] + pred) & 0xFF
    return np.frombuffer(bytes(cur), np.uint8)


def read_png(path):
    """Read an 8-bit PNG as an (H, W, C) uint8 array, with the standard library and numpy only.

    Grey, grey with alpha, RGB and RGBA are read (C is 1, 2, 3 or 4), without interlacing. That
    covers what a browser writes for a canvas picture, so the package needs no imaging library.
    """
    data = pathlib.Path(path).read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path} is not a PNG file")
    pos, idat, hdr = 8, [], None
    while pos + 8 <= len(data):
        n, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        pos += 12 + n
        if kind == b"IHDR":
            hdr = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
    if hdr is None:
        raise ValueError(f"{path} has no IHDR chunk")
    w, h, depth, ctype, _compression, _filter, interlace = hdr
    bpp = {0: 1, 2: 3, 4: 2, 6: 4}.get(ctype)
    if depth != 8 or bpp is None or interlace:
        raise ValueError(f"{path}: only 8-bit grey, RGB or RGBA PNGs without interlacing are read "
                         f"(this one has bit depth {depth}, colour type {ctype}, "
                         f"interlace {interlace})")
    stride = w * bpp
    try:
        raw = np.frombuffer(zlib.decompress(b"".join(idat)), np.uint8)
    except zlib.error as e:
        raise ValueError(f"{path}: the image data is damaged ({e})") from e
    rows = raw[:h * (stride + 1)].reshape(h, stride + 1)
    out = np.empty((h, stride), np.uint8)
    prior = np.zeros(stride, np.uint8)
    for y in range(h):
        kind, line = int(rows[y, 0]), rows[y, 1:]
        if kind == 0:
            cur = line
        elif kind == 1:      # Sub: a running sum of each channel along the row, modulo 256
            cur = np.cumsum(line.reshape(w, bpp), axis=0, dtype=np.uint8).ravel()
        elif kind == 2:      # Up
            cur = line + prior
        elif kind in (3, 4):
            cur = _unfilter_slow(line, prior, bpp, kind)
        else:
            raise ValueError(f"{path}: row {y} has unknown filter {kind}")
        out[y] = cur
        prior = out[y]
    return out.reshape(h, w, bpp)


def png_size(path):
    """(width, height) of a PNG file, read from its header without decoding the picture."""
    head = pathlib.Path(path).read_bytes()[:24]
    if head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        raise ValueError(f"{path} is not a PNG file")
    return struct.unpack(">II", head[16:24])


def _resize(img, width, height):
    """Resample a picture to width x height, taking the source pixel under each pixel's centre."""
    h0, w0 = img.shape[:2]
    if (w0, h0) == (width, height):
        return img
    ys = np.minimum(((np.arange(height) + 0.5) * h0 / height).astype(int), h0 - 1)
    xs = np.minimum(((np.arange(width) + 0.5) * w0 / width).astype(int), w0 - 1)
    return img[ys][:, xs]


def _rgb(img):
    a = np.asarray(img)
    if a.ndim == 2:
        a = a[..., None]
    if a.shape[2] in (1, 2):                # grey, or grey with alpha
        a = np.repeat(a[..., :1], 3, axis=2)
    return a[..., :3]


def model_mask(img, bg=BENCH_BG, tol=MODEL_TOL):
    """Which pixels of a bench picture show the model: those further than `tol` from `bg`."""
    a = _rgb(img).astype(np.int16)
    return np.abs(a - np.asarray(bg, np.int16)).max(axis=2) > tol


def picture_shadow(before, after, bg=BENCH_BG, tol=MODEL_TOL, colour=SHADOW_COLOUR, alpha=0.45,
                   outline=2, clean=1, after_mask=None):
    """Paint in orange where the model was in `before` and is not in `after`.

    `before` is the reviewer's own picture and `after` a new render from the same camera, as
    arrays of the same height and width (RGB or RGBA). A pixel is model when it is further than
    `tol` from the background colour `bg`. When the renderer knows which pixels it covered, pass
    them as `after_mask` (an (H, W) bool array) and they are used for `after` instead of the
    colour test. The pixels that were model before and are background now are filled with
    `colour` at opacity `alpha` and outlined in solid `colour`, `outline` pixels wide, so removed
    material shows where it was. Everything else is `after` unchanged.

    `clean` opens the removed area by that many pixels first, so the one-pixel seams that small
    differences in anti-aliasing leave along every edge are not drawn. A removal narrower than
    2 * clean + 1 pixels is not drawn either. Returns an (H, W, 3) uint8 array.
    """
    from scipy import ndimage

    b, a = _rgb(before), _rgb(after)
    if b.shape[:2] != a.shape[:2]:
        raise ValueError(f"the pictures differ in size: {b.shape[1]}x{b.shape[0]} and "
                         f"{a.shape[1]}x{a.shape[0]}")
    now = model_mask(a, bg, tol) if after_mask is None else np.asarray(after_mask, bool)
    if now.shape != a.shape[:2]:
        raise ValueError("after_mask is not the size of the pictures")
    gone = model_mask(b, bg, tol) & ~now
    if clean:
        gone = ndimage.binary_opening(gone, iterations=int(clean))
    out = a.astype(float)
    c = np.asarray(colour, float)
    out[gone] = out[gone] * (1.0 - alpha) + c * alpha
    if outline:
        edge = gone & ~ndimage.binary_erosion(gone, iterations=int(outline))
        out[edge] = c
    return np.clip(np.rint(out), 0, 255).astype(np.uint8)


def _before_picture(d):
    name = (d.get("shots") or {}).get("before") or f"{d['id']}-before.png"
    p = SUBS / name
    return p if p.exists() else None


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


def _mean_colour(m, default):
    try:
        fc = np.asarray(m.visual.face_colors)
    except Exception:  # noqa: BLE001  (a visual without colours)
        return default
    if fc.ndim != 2 or len(fc) == 0:
        return default
    return tuple(int(v) for v in fc[:, :3].mean(axis=0))


def render_reply(sub_id, name=None, shadow=None, highlight=None, size=None, stage=None,
                 mark=True, fov=None, labels=True, shadow_picture=True, max_width=MAX_WIDTH,
                 shift=None):
    """Render the current state of the stage from the reviewer's camera and save it as a PNG.

    The reply is drawn the way the bench page drew the reviewer's picture: the camera stored with
    the submission, the page's lens (`view.fov`), the reviewer's aspect (`view.width` by
    `view.height`, scaled to at most `max_width` pixels wide), each node's own transform, and on
    the "parts" stage the reviewer's "apart" setting. So the reply and the reviewer's picture line
    up, and the difference between them is the answer. `size` and `fov` override the stored frame.

    `shadow` is {part name: mesh}, drawn in orange together with the current state, to show where
    something was before it moved. When no `shadow` is given and `shadow_picture` is true, the
    reviewer's own before picture is compared with the new render instead, and what was model in
    it but is empty space now is painted in orange (see `picture_shadow`). That needs the before
    picture to have the reply's aspect, and the reply to be of the submission's own stage.

    `highlight` names one part to tint. `mark` draws the reviewer's circle when the submission has
    one. `labels` draws the number labels the page draws. Returns the file name, which is stored
    in `SUBS`.

    `shift` is for a stage file whose coordinates moved since the submission was made, for example
    a build that re-centres the model each time. It is the translation (x, y, z) in the stage's
    units from where a point was in the reviewer's frame to where the same point is in the stage
    file now. The stored camera and the circle are moved by it, so parts that did not change line
    up with the reviewer's picture again. A stage frame that stays fixed across rebuilds needs none.
    """
    from base3d import show

    d = read(sub_id)
    own_stage = d.get("stage") or "assembled"
    stage = stage or own_stage
    cam = d.get("camera") or {}
    eye = np.asarray(cam.get("eye") or (200, 200, 120), float)
    at = np.asarray(cam.get("at") or (0, 0, 60), float)
    move = np.zeros(3) if shift is None else np.asarray(shift, float).reshape(3)
    eye, at = eye + move, at + move
    lens, frame = reply_frame(d, max_width)
    fov = float(fov or lens)
    width, height = size or frame

    parts = _explode(_stage_mesh(stage, drop_labels=not labels), stage,
                     (d.get("view") or {}).get("apart"))
    pieces, colours = [], []
    PART = (238, 238, 233)
    GHOST = SHADOW_COLOUR
    HOT = (120, 205, 180)
    for pname, g in parts.items():
        m = trimesh.Trimesh(np.asarray(g.vertices, float), np.asarray(g.faces), process=False)
        if highlight and pname == highlight:
            c = HOT
        elif _is_label(pname):
            c = _mean_colour(g, PART)
        else:
            c = PART
        pieces.append(m)
        colours.append(np.tile(c, (len(m.faces), 1)))
    for g in (shadow or {}).values():
        m = trimesh.Trimesh(np.asarray(g.vertices, float), np.asarray(g.faces), process=False)
        pieces.append(m)
        colours.append(np.tile(GHOST, (len(m.faces), 1)))
    mk = d.get("mark") if mark else None
    if mk and mk.get("at"):
        # The page stands the circle and its dot off the surface by 0.7 of the dot's radius, so
        # the same is done here, toward the camera.
        r = float(mk.get("r_mm") or 3.0)
        mark_at = np.asarray(mk["at"], float) + move
        to_eye = eye - mark_at
        to_eye = to_eye / max(float(np.linalg.norm(to_eye)), 1e-9)
        dot = max(r * 0.12, 0.5)
        centre = mark_at + to_eye * dot * 0.7
        ball = trimesh.creation.icosphere(subdivisions=2, radius=dot)
        ball.apply_translation(centre)
        for m in (_disc(centre, r, to_eye), ball):
            pieces.append(m)
            colours.append(np.tile(GHOST, (len(m.faces), 1)))

    whole = trimesh.util.concatenate(pieces)
    face_colors = np.vstack(colours) / 255.0
    # Drawn on a background colour that shading never produces, so the pixels the model covers are
    # known exactly. A face turned away from every light is drawn close to the bench background,
    # and telling the two apart by colour would count it as empty space.
    img = show.render(whole, eye=eye, target=at, size=(int(width), int(height)), fov=fov,
                      face_colors=face_colors, bg=tuple(c / 255.0 for c in _UNDRAWN))
    # `show.render` returns an array, so it is converted to 8-bit before it is written.
    arr = np.asarray(img)
    if arr.dtype != np.uint8:
        arr = np.clip(arr * 255.0, 0, 255).astype(np.uint8)
    arr = np.array(arr[..., :3])
    empty = (arr == np.asarray(_UNDRAWN, np.uint8)).all(axis=2)
    arr[empty] = BENCH_BG
    if shadow_picture and not shadow and stage == own_stage:
        bp = _before_picture(d)
        if bp is not None:
            try:
                bw, bh = png_size(bp)
                same = min(bw, bh) >= 8 and abs(bw / bh - width / height) <= 0.01 * width / height
                before = read_png(bp) if same else None
            except ValueError as e:
                raise ValueError(f"{e}. Pass shadow_picture=False to reply without the "
                                 f"picture shadow.") from e
            # A before picture of another aspect cannot be laid over the reply, so it is not used.
            if before is not None:
                arr = picture_shadow(_resize(before, int(width), int(height)), arr,
                                     after_mask=~empty)
    n = name or f"{sub_id}-reply-{len([e for e in d['thread'] if e.get('shot')]) + 1}.png"
    out = SUBS / n
    show.save_png(arr, out)
    return out.name


def say(sub_id, text, state=None, shadow=None, highlight=None, did=None, picture=True,
        shadow_picture=True, shift=None):
    """Reply on a thread as the assistant, with a picture from the reviewer's camera.

    `shadow` ({part name: old mesh}) draws where something was in 3D. Without it, and with
    `shadow_picture` true, the reply shows in orange what the reviewer's own picture had and the
    new render does not. `shift` moves the stored camera for a stage frame that moved since the
    submission. See `render_reply`.
    """
    shot = (render_reply(sub_id, shadow=shadow, highlight=highlight, shadow_picture=shadow_picture,
                         shift=shift)
            if picture else None)
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
