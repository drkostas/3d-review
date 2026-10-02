"""The bench server, run for real on a free port, driven the way its pages drive it."""
import base64
import json
import threading
import urllib.error
import urllib.request

import pytest
import trimesh

from review3d import stages
from review3d.bench import server, thread
from review3d.bench.notify import FileNotifier

# A 1x1 PNG, sent as a data URL the way the page sends canvas.toDataURL('image/png').
PNG = ("data:image/png;base64," + base64.b64encode(bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")).decode())


@pytest.fixture
def bench(tmp_path):
    a = trimesh.creation.box((20, 10, 6))
    b = trimesh.creation.box((8, 8, 8))
    b.apply_translation((25, 0, 0))
    stages.write_stages({"parts": {"lid": a, "peg": b}}, tmp_path)
    subs = tmp_path / "submissions"
    logs = []
    b = server.Bench([tmp_path], subs, tmp_path / "stages",
                     notifier=FileNotifier(subs / "published" / "notifications.log"),
                     log=logs.append)
    srv = server._Server(("127.0.0.1", 0), server.make_handler(b))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    b.url = f"http://127.0.0.1:{srv.server_address[1]}"
    yield b
    srv.shutdown()
    srv.server_close()


def get(b, route):
    with urllib.request.urlopen(b.url + route) as r:
        return r.status, r.headers.get("Content-Type"), r.read()


def post(b, route, body):
    req = urllib.request.Request(b.url + route, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def submit_move(b, said="the lid sits too high"):
    # The same body twist.html builds in submit().
    body = {"kind": "move", "stage": "parts", "part": "lid", "said": said,
            "change": {"move_mm": [0, 0, -2], "move_len_mm": 2, "turn_deg": 0,
                       "turn_axis": [0, 0, 1], "turn_axis_named": "world z", "space": "world",
                       "scale": 1, "centre_before": [0, 0, 0], "centre_after": [0, 0, -2]},
            "mark": None,
            "view": {"apart": 1, "fov": 32, "width": 800, "height": 600, "space": "world",
                     "snap": True, "mode": "translate"},
            "camera": {"eye": [80, -90, 60], "at": [5, 0, 0]},
            "before_png": PNG, "after_png": PNG}
    return post(b, "/api/submit", body)


def test_pages_and_stage_files_are_served(bench):
    for page in ("/twist.html", "/threads.html"):
        status, ctype, body = get(bench, page)
        assert status == 200 and "text/html" in ctype and b"<script" in body
    status, _, body = get(bench, "/stages.json")
    info = json.loads(body)
    assert list(info) == ["parts", "meta"]
    assert info["parts"]["pieces"] == 2 and set(info["parts"]["frames"]) == {"lid", "peg"}
    status, _, glb = get(bench, "/" + info["parts"]["file"])
    assert status == 200 and glb[:4] == b"glTF"
    status, _, js = get(bench, "/vendor/three.module.js")
    assert status == 200 and len(js) > 100_000


def test_stage_glb_has_named_parts_colours_and_normals(tmp_path):
    a = trimesh.creation.box((20, 10, 6))
    stages.write_stages({"one": {"lid": a}}, tmp_path)
    sc = trimesh.load(tmp_path / "stages" / "one.glb", force="scene")
    assert list(sc.geometry) == ["lid"]
    g = sc.geometry["lid"]
    assert g.visual.kind in ("face", "vertex")
    # The box has 8 corners, and normals split them at every sharp edge.
    assert len(g.vertices) == 24
    gltf = json.loads(trimesh.exchange.gltf.export_gltf(sc)["model.gltf"])
    attrs = gltf["meshes"][0]["primitives"][0]["attributes"]
    assert "NORMAL" in attrs and "COLOR_0" in attrs


def test_submit_thread_reply_and_publish_once(bench):
    got = submit_move(bench)
    assert got["ok"] and set(got["shots"]) == {"before", "after"}
    sid = got["id"]
    assert (bench.subs / f"{sid}-before.png").read_bytes()[:4] == b"\x89PNG"

    listing = json.loads(get(bench, "/api/threads")[2])["threads"]
    assert [t["id"] for t in listing] == [sid] and listing[0]["state"] == "new"
    th = json.loads(get(bench, f"/api/thread/{sid}")[2])
    assert th["part"] == "lid" and th["thread"] == [] and th["camera"]["eye"] == [80, -90, 60]
    status, ctype, png = get(bench, f"/api/sub/{sid}-after.png")
    assert ctype == "image/png" and png[:4] == b"\x89PNG"

    # The assistant replies with a picture from the reviewer's camera.
    thread.say(sid, "Lowered by 2 mm.", state="answered")
    th = json.loads(get(bench, f"/api/thread/{sid}")[2])
    assert th["state"] == "answered" and th["thread"][0]["by"] == thread.ASSISTANT
    shot = th["thread"][0]["shot"]
    assert shot == f"{sid}-reply-1.png"
    status, _, png = get(bench, f"/api/sub/{shot}")
    assert png[:4] == b"\x89PNG" and len(png) > 1000

    # A follow-up from the page reopens the thread.
    got = post(bench, "/api/comment", {"id": sid, "text": "Still a little high."})
    assert got == {"ok": True, "turns": 2, "state": "new"}
    listing = json.loads(get(bench, "/api/threads")[2])["threads"]
    assert listing[0]["replies"] == 1 and listing[0]["last_by"] == thread.REVIEWER

    got = post(bench, "/api/publish", {})
    assert got["count"] == 1 and got["pinged"] is True
    manifest = json.loads((bench.published / got["manifest"]).read_text())
    assert manifest["ids"] == [sid] and manifest["count"] == 1
    log = (bench.published / "notifications.log").read_text().splitlines()
    assert len(log) == 1 and "1 submission(s)" in log[0] and got["manifest"] in log[0]
    sub = json.loads(get(bench, "/api/submissions")[2])["submissions"][0]
    assert sub["published"]

    again = post(bench, "/api/publish", {})
    assert again["count"] == 0 and again["pinged"] is False
    assert len((bench.published / "notifications.log").read_text().splitlines()) == 1
    assert len(list(bench.published.glob("publish-*.json"))) == 1


def test_bad_requests_are_refused(bench):
    with pytest.raises(urllib.error.HTTPError) as e:
        get(bench, "/api/thread/..%2Fx")
    assert e.value.code == 404
    with pytest.raises(urllib.error.HTTPError) as e:
        post(bench, "/api/comment", {"id": "nope", "text": "hi"})
    assert e.value.code == 404
    with pytest.raises(urllib.error.HTTPError) as e:
        post(bench, "/api/rebuild", {})
    assert e.value.code == 404


def test_thread_authors_are_checked(bench):
    sid = submit_move(bench)["id"]
    with pytest.raises(ValueError):
        thread.append(sid, "someone", "text")
    with pytest.raises(ValueError):
        thread.append(sid, thread.ASSISTANT, "text", state="published")


def test_stage_labels_order_and_default_are_written(tmp_path):
    import json
    import trimesh
    from review3d.stages import write_stages
    box = trimesh.creation.box()
    info = write_stages({"printed": {"a": box}, "assembled": {"a": box.copy()}}, tmp_path,
                        labels={"assembled": "2 · Assembled", "printed": "1 · As printed"},
                        order=["assembled", "printed"], default="assembled")
    meta = json.loads((tmp_path / "stages.json").read_text())["meta"]
    assert meta["stage_order"] == ["assembled", "printed"] and meta["default_stage"] == "assembled"
    assert meta["stage_labels"]["assembled"] == "2 · Assembled"
    import pytest
    with pytest.raises(ValueError):
        write_stages({"printed": {"a": box}}, tmp_path, default="nope")
