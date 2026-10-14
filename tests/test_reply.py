"""Reply pictures: drawn with the reviewer's own camera, lens and frame, and the picture shadow."""
import json
import struct
import zlib

import numpy as np
import pytest
import trimesh
from scipy import ndimage
from scipy.spatial import Delaunay

from review3d import stages
from review3d.bench import thread

BG = np.array(thread.BENCH_BG, np.uint8)


def three_pixels(points, eye, at, fov, width, height):
    """Project points the way three.js does for a PerspectiveCamera with up = +Z.

    Written from three.js's own definitions (Matrix4.lookAt and makePerspective), not from the
    renderer under test, so the two are compared rather than one checked against itself.
    """
    eye, at = np.asarray(eye, float), np.asarray(at, float)
    z = eye - at
    z /= np.linalg.norm(z)
    x = np.cross([0.0, 0.0, 1.0], z)
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    rel = np.asarray(points, float) - eye
    cx, cy, cz = rel @ x, rel @ y, rel @ z
    t = np.tan(np.radians(fov) / 2)
    aspect = width / height
    ndc_x = cx / (-cz * t * aspect)
    ndc_y = cy / (-cz * t)
    return np.c_[(ndc_x + 1) / 2 * width, (1 - ndc_y) / 2 * height]


def silhouette(mesh, eye, at, fov, width, height):
    """The pixels whose centres fall inside the projected outline of a convex mesh."""
    p = three_pixels(mesh.vertices, eye, at, fov, width, height)
    gx, gy = np.meshgrid(np.arange(width) + 0.5, np.arange(height) + 0.5)
    inside = Delaunay(p).find_simplex(np.c_[gx.ravel(), gy.ravel()]) >= 0
    return inside.reshape(height, width)


def iou(a, b):
    return float((a & b).sum() / max((a | b).sum(), 1))


def block():
    m = trimesh.creation.box((40, 24, 16))
    m.apply_transform(trimesh.transformations.rotation_matrix(np.radians(25), [0, 0, 1]))
    m.apply_translation((6, -3, 8))
    return m


def submission(subs, sid="s1", stage="assembled", width=900, height=500, fov=32, apart=None,
               eye=(120, -150, 90), at=(4, -2, 9)):
    subs.mkdir(parents=True, exist_ok=True)
    d = {"id": sid, "kind": "mark", "stage": stage, "part": "block", "said": "",
         "camera": {"eye": list(eye), "at": list(at)},
         "view": {"apart": apart, "fov": fov, "width": width, "height": height},
         "thread": []}
    (subs / f"{sid}.json").write_text(json.dumps(d))
    return d


def reply_mask(subs, name):
    return thread.model_mask(thread.read_png(subs / name))


def test_reply_uses_the_reviewers_lens_and_frame(tmp_path):
    stages.write_stages({"assembled": {"block": block()}}, tmp_path)
    subs = tmp_path / "submissions"
    d = submission(subs)
    thread.configure(subs, tmp_path / "stages")

    name = thread.render_reply("s1", shadow_picture=False)
    img = thread.read_png(subs / name)
    assert img.shape == (500, 900, 3)
    assert (img[0, 0] == BG).all() and (img[-1, -1] == BG).all()   # the page's background
    got = thread.model_mask(img)
    want = silhouette(block(), d["camera"]["eye"], d["camera"]["at"], 32, 900, 500)
    assert iou(got, want) > 0.97

    # The renderer's own default lens is not the page's, and the check can tell.
    wrong = reply_mask(subs, thread.render_reply("s1", name="wrong.png", fov=26,
                                                 shadow_picture=False))
    assert iou(wrong, want) < 0.8


def test_a_wide_canvas_is_scaled_down_keeping_its_aspect(tmp_path):
    stages.write_stages({"assembled": {"block": block()}}, tmp_path)
    subs = tmp_path / "submissions"
    d = submission(subs, width=3200, height=1800)
    thread.configure(subs, tmp_path / "stages")
    assert thread.reply_frame(d) == (32.0, (1600, 900))
    got = reply_mask(subs, thread.render_reply("s1", shadow_picture=False))
    assert got.shape == (900, 1600)
    want = silhouette(block(), d["camera"]["eye"], d["camera"]["at"], 32, 1600, 900)
    assert iou(got, want) > 0.97


def test_node_transforms_are_applied_like_the_page(tmp_path):
    box = trimesh.creation.box((30, 20, 12))
    T = trimesh.transformations.rotation_matrix(np.radians(40), [0, 0, 1])
    T[:3, 3] = (14, 9, 6)
    sc = trimesh.Scene()
    sc.add_geometry(box, geom_name="block", node_name="block", transform=T)
    (tmp_path / "stages").mkdir()
    (tmp_path / "stages" / "assembled.glb").write_bytes(sc.export(file_type="glb"))
    subs = tmp_path / "submissions"
    d = submission(subs)
    thread.configure(subs, tmp_path / "stages")
    got = reply_mask(subs, thread.render_reply("s1", shadow_picture=False))
    want = silhouette(box.copy().apply_transform(T), d["camera"]["eye"], d["camera"]["at"], 32,
                      900, 500)
    assert iou(got, want) > 0.97


@pytest.mark.parametrize("apart, shift", [(1, 0.0), (0, -30.0), (0.5, -15.0)])
def test_the_apart_slider_is_reproduced(tmp_path, apart, shift):
    stages.write_stages({"parts": {"block": block()}}, tmp_path, offsets={"block": (0, 0, 30)})
    subs = tmp_path / "submissions"
    d = submission(subs, stage="parts", apart=apart)
    thread.configure(subs, tmp_path / "stages")
    got = reply_mask(subs, thread.render_reply("s1", shadow_picture=False))
    want = silhouette(block().apply_translation((0, 0, shift)), d["camera"]["eye"],
                      d["camera"]["at"], 32, 900, 500)
    assert iou(got, want) > 0.97


def test_a_shift_moves_the_camera_to_a_moved_stage_frame(tmp_path):
    # The stage file was rebuilt with its origin somewhere else: every point moved by `move`.
    move = np.array([8.0, -6.5, 8.0])
    stages.write_stages({"assembled": {"block": block().apply_translation(move)}}, tmp_path)
    subs = tmp_path / "submissions"
    d = submission(subs)
    # in open space above the block, so no part of the circle is hidden and its centre can be read
    d["mark"] = {"part": "block", "at": [6.0, -3.0, 34.0], "r_mm": 5.0}
    (subs / "s1.json").write_text(json.dumps(d))
    thread.configure(subs, tmp_path / "stages")
    want = silhouette(block(), d["camera"]["eye"], d["camera"]["at"], 32, 900, 500)

    plain = reply_mask(subs, thread.render_reply("s1", name="plain.png", mark=False,
                                                 shadow_picture=False))
    assert iou(plain, want) < 0.8            # without the shift the block is off the old picture
    moved = reply_mask(subs, thread.render_reply("s1", name="moved.png", mark=False,
                                                 shadow_picture=False, shift=move))
    assert iou(moved, want) > 0.97

    # The circle moves with the camera, so it stays on the point the reviewer marked.
    with_mark = thread.read_png(subs / thread.render_reply("s1", name="mark.png", shift=move,
                                                           shadow_picture=False))
    r, g, b = (with_mark[..., i].astype(int) for i in range(3))
    ys, xs = np.nonzero((r > 200) & (r - b > 120) & (g > 110) & (g < 190))
    px = three_pixels([d["mark"]["at"]], d["camera"]["eye"], d["camera"]["at"], 32, 900, 500)[0]
    assert len(xs) > 50 and abs(xs.mean() - px[0]) < 3 and abs(ys.mean() - px[1]) < 3


def square(img, y0, y1, x0, x1, colour=(200, 200, 196)):
    img[y0:y1, x0:x1] = colour


def test_picture_shadow_paints_what_was_removed():
    before = np.tile(BG, (60, 90, 1))
    square(before, 10, 40, 10, 30)                       # stays
    square(before, 10, 40, 50, 75)                       # removed
    before[52, :] = (42, 49, 54)                         # a floor grid line, not the model
    after = np.tile(BG, (60, 90, 1))
    square(after, 10, 40, 11, 31)                        # the kept square, one pixel off
    out = thread.picture_shadow(before, after)
    orange = np.array(thread.SHADOW_COLOUR, float)
    fill = np.rint(BG * 0.55 + orange * 0.45).astype(np.uint8)
    assert out.shape == (60, 90, 3) and out.dtype == np.uint8
    assert (out[25, 62] == fill).all()                   # inside the removed square
    assert (out[10, 62] == orange).all() and (out[25, 50] == orange).all()   # its outline
    assert (out[25, 74] == orange).all() and (out[11, 51] == orange).all()
    assert (out[25, 10] == BG).all()                     # the one-pixel seam is not drawn
    assert (out[25, 20] == after[25, 20]).all()          # the kept square is untouched
    assert (out[52, 5] == BG).all() and (out[5, 5] == BG).all()
    with pytest.raises(ValueError):
        thread.picture_shadow(before, after[:, :40])


def test_a_dark_face_the_renderer_drew_is_not_taken_for_empty_space():
    before = np.tile(BG, (40, 40, 1))
    square(before, 5, 35, 5, 35)
    after = np.tile(BG, (40, 40, 1))
    square(after, 5, 35, 5, 35, colour=(34, 35, 33))     # a face turned away from every light
    covered = np.zeros((40, 40), bool)
    covered[5:35, 5:35] = True
    assert (thread.picture_shadow(before, after, after_mask=covered) == after).all()
    # By colour alone the dark face reads as empty space, which is why the coverage is passed.
    assert (thread.picture_shadow(before, after)[20, 20] != after[20, 20]).any()


def encode_png(img, filters):
    """A PNG writer for the test, with a chosen filter on each row."""
    h, w, c = img.shape
    ctype = {1: 0, 2: 4, 3: 2, 4: 6}[c]
    stride = w * c
    prior = bytes(stride)
    raw = b""
    for y in range(h):
        row = img[y].tobytes()
        f = filters[y % len(filters)]
        enc = bytearray(stride)
        for i in range(stride):
            a = row[i - c] if i >= c else 0
            b = prior[i]
            cc = prior[i - c] if i >= c else 0
            if f == 0:
                pred = 0
            elif f == 1:
                pred = a
            elif f == 2:
                pred = b
            elif f == 3:
                pred = (a + b) >> 1
            else:
                p = a + b - cc
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - cc)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else cc)
            enc[i] = (row[i] - pred) & 0xFF
        raw += bytes([f]) + bytes(enc)
        prior = row

    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, ctype, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


@pytest.mark.parametrize("channels", [1, 2, 3, 4])
def test_png_reader_undoes_every_filter(tmp_path, channels):
    rng = np.random.default_rng(7)
    img = rng.integers(0, 256, (13, 11, channels), dtype=np.uint8)
    p = tmp_path / "x.png"
    p.write_bytes(encode_png(img, [0, 1, 2, 3, 4]))
    got = thread.read_png(p)
    assert got.shape == img.shape and (got == img).all()


def test_say_shows_removed_material_from_the_reviewers_picture(tmp_path):
    peg = trimesh.creation.box((10, 10, 10))
    peg.apply_translation((40, 32, 5))
    stages.write_stages({"assembled": {"block": block(), "peg": peg}}, tmp_path)
    subs = tmp_path / "submissions"
    d = submission(subs, width=800, height=480)
    thread.configure(subs, tmp_path / "stages")
    # The reviewer's picture, standing in for the page's: the model as it was.
    thread.render_reply("s1", name="s1-before.png", shadow_picture=False)
    # The peg is taken away and the stages are written again.
    stages.write_stages({"assembled": {"block": block()}}, tmp_path)

    entry = thread.say("s1", "Removed the peg.", state="answered")["thread"][-1]
    out = thread.read_png(subs / entry["shot"])
    plain = thread.read_png(subs / thread.render_reply("s1", name="plain.png",
                                                       shadow_picture=False))
    eye, at = d["camera"]["eye"], d["camera"]["at"]
    peg_px = silhouette(peg, eye, at, 32, 800, 480)
    block_px = silhouette(block(), eye, at, 32, 800, 480)
    cx, cy = (int(v) for v in three_pixels([peg.bounds.mean(axis=0)], eye, at, 32, 800, 480)[0])
    fill = np.rint(BG * 0.55 + np.array(thread.SHADOW_COLOUR) * 0.45)
    assert np.abs(out[cy, cx].astype(int) - fill).max() <= 1
    orange = (out[..., 0].astype(int) - out[..., 2] > 60)
    assert orange[peg_px].mean() > 0.9                    # the peg's place is orange
    assert (out[block_px] == plain[block_px]).all()       # the kept block is drawn as it is
    near_peg = ndimage.binary_dilation(peg_px, iterations=2)
    assert not orange[~near_peg].any()                     # and nothing else is painted
