"""The bench server, run for real on a free port, driven the way its pages drive it."""
import base64
import json
import pathlib
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


def test_a_reply_picture_can_carry_its_own_caption(bench):
    sid = submit_move(bench)["id"]
    thread.append(sid, thread.ASSISTANT, "a close look", shot="close.png", caption="a closer view")
    thread.append(sid, thread.ASSISTANT, "from the camera", shot="reply.png")
    turns = thread.read(sid)["thread"]
    assert turns[-2]["caption"] == "a closer view"
    assert "caption" not in turns[-1]
    page = (pathlib.Path(thread.__file__).parent / "static" / "threads.html").read_text()
    assert "esc(t.caption ||" in page


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


def test_pivots_follow_each_stage_and_carries_are_written(tmp_path):
    import trimesh
    from review3d import stages as st
    hand = trimesh.creation.box(extents=[4, 4, 4])
    cup = trimesh.creation.cylinder(radius=1.0, height=3.0)
    cup.apply_translation([4.0, 0.0, 0.0])
    apart_hand = hand.copy()
    apart_hand.apply_translation([10.0, 0.0, 5.0])
    apart_cup = cup.copy()
    apart_cup.apply_translation([20.0, 0.0, 5.0])
    info = st.write_stages({"assembled": {"hand": hand, "cup": cup},
                            "parts": {"hand": apart_hand, "cup": apart_cup}}, tmp_path,
                           pivots={"hand": [-2.0, 0.0, 0.0]}, carries={"hand": ["cup"]})
    assert info["assembled"]["pivots"] == {"hand": [-2.0, 0.0, 0.0]}
    assert info["parts"]["pivots"] == {"hand": [8.0, 0.0, 5.0]}      # moved with the hand's box
    assert info["meta"]["carries"] == {"hand": ["cup"]}
    assert "pivots" not in info["parts"] or "cup" not in info["parts"]["pivots"]


def test_the_host_setting_names_one_address_or_several():
    assert server.addresses("") == [""]
    assert server.addresses("127.0.0.1") == ["127.0.0.1"]
    assert server.addresses(["127.0.0.1", "192.0.2.7"]) == ["127.0.0.1", "192.0.2.7"]


def _free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_serve_answers_on_its_address_and_keeps_trying_one_not_here_yet(tmp_path):
    # 192.0.2.7 is a documentation address no machine has, like a VPN address before the VPN is up:
    # the bench answers on the address it can bind and keeps trying the other in the background.
    import time
    stages.write_stages({"parts": {"lid": trimesh.creation.box((10, 10, 10))}}, tmp_path)
    logs = []
    b = server.Bench([tmp_path], tmp_path / "submissions", tmp_path / "stages", log=logs.append)
    port = _free_port()
    t = threading.Thread(target=server.serve, args=(b, port, ["192.0.2.7", "127.0.0.1"]),
                         kwargs={"retry_s": 0.05}, daemon=True)
    t.start()
    body = None
    for _ in range(100):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/stages.json") as r:
                body = json.loads(r.read())
            break
        except OSError:
            time.sleep(0.05)
    assert body is not None and "parts" in body
    assert any("192.0.2.7" in m and "not on this machine yet" in m for m in logs)
    time.sleep(0.2)
    assert t.is_alive()


def test_serve_raises_when_its_port_is_taken(tmp_path):
    import socket
    stages.write_stages({"parts": {"lid": trimesh.creation.box((10, 10, 10))}}, tmp_path)
    b = server.Bench([tmp_path], tmp_path / "submissions", tmp_path / "stages", log=lambda _m: None)
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        with pytest.raises(OSError):
            server.serve(b, taken.getsockname()[1], ["127.0.0.1"])


def test_a_post_the_check_refuses_changes_nothing(tmp_path):
    stages.write_stages({"parts": {"lid": trimesh.creation.box((10, 10, 10))}}, tmp_path)
    subs = tmp_path / "submissions"
    logs = []
    b = server.Bench([tmp_path], subs, tmp_path / "stages", log=logs.append,
                     may_post=lambda ip, headers: "not you")
    srv = server._Server(("127.0.0.1", 0), server.make_handler(b))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    b.url = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        with pytest.raises(urllib.error.HTTPError) as e:
            submit_move(b)
        assert e.value.code == 403 and "not you" in json.loads(e.value.read())["error"]
        assert not subs.exists() or not list(subs.glob("*.json"))
        assert any("refused a post to /api/submit" in m for m in logs)
        assert get(b, "/stages.json")[0] == 200          # reading stays open
    finally:
        srv.shutdown()
        srv.server_close()


def test_settings_turn_the_tailnet_check_on(tmp_path):
    from review3d.bench import settings
    (tmp_path / "bench.toml").write_text('post_from = ["alice@example.com"]\n')
    may = settings.may_post(settings.load(tmp_path / "bench.toml"))
    assert may is not None and may("127.0.0.1", {}) is None
    assert "neither this machine" in may("192.0.2.4", {})
    assert settings.may_post(settings.load(tmp_path / "none.toml")) is None
