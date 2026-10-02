"""Tests for review3d.penpot.

Most tests run against a small fake Penpot server started in a thread. It
records every request and answers the calls the module uses in the way the
real backend does: transit JSON in, plain JSON out, path content returned as
SVG path data, and a revision number that must match on every change.

The last test talks to a real Penpot. It is skipped unless PENPOT_URL and a
credential (PENPOT_ACCESS_TOKEN, PENPOT_SESSION_TOKEN, or PENPOT_EMAIL with
PENPOT_PASSWORD) are set in the environment.
"""
from __future__ import annotations

import json
import math
import os
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from review3d import penpot as P

ROOT = P.ROOT_ID
EMAIL = "reviewer-account"
PASSWORD = "not-a-real-password"
SESSION = "session-0001"
ACCESS = "access-0001"


# --------------------------------------------------------------------------- fake server

def decode_transit(value):
    """Decode the verbose transit JSON the client writes (no key cache)."""
    if isinstance(value, list):
        if value[:1] == ["^ "]:
            items = value[1:]
            return {decode_transit(k): decode_transit(v) for k, v in zip(items[::2], items[1::2])}
        return [decode_transit(v) for v in value]
    if isinstance(value, str):
        if value.startswith("~:"):
            return value[2:]
        if value.startswith("~u"):
            return value[2:]
        if value[:2] in ("~~", "~^", "~`"):
            return value[1:]
    return value


def camel(key):
    head, *rest = key.split("-")
    return head + "".join(r.capitalize() for r in rest)


def to_json_shape(value):
    if isinstance(value, dict):
        return {camel(k): to_json_shape(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_json_shape(v) for v in value]
    return value


def content_to_d(content):
    parts = []
    for seg in content:
        p = seg["params"]
        if seg["command"] == "move-to":
            parts.append(f"M{float(p['x'])},{float(p['y'])}")
        elif seg["command"] == "line-to":
            parts.append(f"L{float(p['x'])},{float(p['y'])}")
        elif seg["command"] == "close-path":
            parts.append("Z")
    return "".join(parts)


class FakePenpot:
    def __init__(self):
        self.requests = []
        self.team_id = str(uuid.uuid4())
        self.project_id = str(uuid.uuid4())
        self.profile_id = str(uuid.uuid4())
        self.files = {}
        self.svg = None
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                fake.record(self, None)
                if self.path.startswith("/assets/") and fake.svg is not None:
                    self.reply(200, fake.svg, ctype="image/svg+xml")
                else:
                    self.reply(404, {"type": "not-found", "code": "object-not-found"})

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                body = decode_transit(json.loads(raw)) if raw else {}
                fake.record(self, body)
                status, answer, extra = fake.handle(self.path, self.headers, body)
                self.reply(status, answer, extra)

            def reply(self, status, answer, extra=None, ctype="application/json"):
                data = answer.encode() if isinstance(answer, str) else json.dumps(answer).encode()
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def record(self, handler, body):
        self.requests.append({"path": handler.path, "headers": dict(handler.headers), "body": body})

    def calls(self, command):
        return [r for r in self.requests if r["path"].endswith("/" + command)]

    def profile(self):
        return {"id": self.profile_id, "email": EMAIL, "defaultTeamId": self.team_id,
                "defaultProjectId": self.project_id}

    def add_file(self, name="file"):
        fid, pid = str(uuid.uuid4()), str(uuid.uuid4())
        self.files[fid] = {"id": fid, "name": name, "revn": 0, "vern": 0, "projectId": self.project_id,
                           "data": {"pages": [pid], "pagesIndex": {pid: {"objects": {
                               ROOT: {"id": ROOT, "type": "frame", "name": "Root Frame",
                                      "parentId": ROOT, "frameId": ROOT, "x": 0, "y": 0}}}}}}
        return fid, pid

    def handle(self, path, headers, body):
        command = path.rsplit("/", 1)[-1]
        if command == "login-with-password":
            if body.get("email") == EMAIL and body.get("password") == PASSWORD:
                return 200, self.profile(), {"Set-Cookie": f"auth-token={SESSION}; Path=/; HttpOnly"}
            return 400, {"type": "validation", "code": "wrong-credentials"}, None
        authorised = (headers.get("Cookie") == f"auth-token={SESSION}"
                      or headers.get("Authorization") == f"Token {ACCESS}")
        if not authorised:
            return 401, {"type": "authentication", "code": "authentication-required"}, None
        if command == "get-profile":
            return 200, self.profile(), None
        if command == "create-file":
            fid, _ = self.add_file(body["name"])
            assert body["project-id"] == self.project_id
            return 200, {"id": fid, "name": body["name"]}, None
        if command == "get-file":
            if body["id"] not in self.files:
                return 404, {"type": "not-found", "code": "object-not-found"}, None
            return 200, self.files[body["id"]], None
        if command == "update-file":
            f = self.files[body["id"]]
            if body["revn"] != f["revn"]:
                return 409, {"type": "validation", "code": "revn-conflict", "hint": "stale"}, None
            for ch in body["changes"]:
                assert ch["type"] == "add-obj"
                obj = to_json_shape(dict(ch["obj"]))
                obj["content"] = content_to_d(ch["obj"]["content"])
                f["data"]["pagesIndex"][ch["page-id"]]["objects"][ch["id"]] = obj
            f["revn"] += 1
            return 200, {"revn": f["revn"], "lagged": []}, None
        if command == "export":
            return 200, {"~:uri": {"~#uri": f"{self.url}/assets/out.svg"}}, None
        return 404, {"type": "not-found", "code": "unknown-command"}, None

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake():
    server = FakePenpot()
    yield server
    server.close()


def client_for(fake, **kw):
    settings = P.PenpotSettings(url=fake.url, **kw)
    return P.PenpotClient(settings)


SQUARE = [(0.0, 0.0), (25.0, 0.0), (25.0, 25.0), (0.0, 25.0)]
HOLE = [(10.0, 5.0), (20.0, 5.0), (20.0, 15.0), (10.0, 15.0)]
PLATE = {"outer": [(0.0, 0.0), (60.0, 0.0), (60.0, 20.0), (0.0, 20.0)], "holes": [HOLE]}
TRIANGLE = [(0.0, 0.0), (30.0, 0.0), (12.5, 27.25)]


def assert_rings_close(a, b, tol=0.01):
    assert len(a) == len(b)
    for p, q in zip(a, b):
        assert math.hypot(p[0] - q[0], p[1] - q[1]) <= tol


# --------------------------------------------------------------------------- settings

def test_settings_from_toml_table_and_environment(tmp_path):
    path = tmp_path / "penpot.toml"
    path.write_text('[penpot]\nurl = "http://penpot.test"\nemail = "reviewer-account"\n'
                    'password = "from-file"\nfile_id = "f1"\ntimeout = 5\n')
    settings = P.PenpotSettings.load(path, environ={"PENPOT_PASSWORD": "from-env",
                                                    "PENPOT_PAGE_ID": "p1"})
    assert settings.url == "http://penpot.test"
    assert settings.email == "reviewer-account"
    assert settings.password == "from-env"
    assert settings.file_id == "f1" and settings.page_id == "p1"
    assert settings.timeout == 5.0
    assert "from-env" not in repr(settings)


def test_settings_from_json_session_file(tmp_path):
    path = tmp_path / "session.json"
    path.write_text(json.dumps({"url": "http://penpot.test", "auth_token": "abc",
                                "file_id": "f", "page_id": "p", "extra": 1}))
    settings = P.PenpotSettings.from_file(path)
    assert settings.session_token == "abc"
    assert (settings.file_id, settings.page_id) == ("f", "p")


def test_settings_file_named_by_environment(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"url": "http://a.test"}))
    settings = P.PenpotSettings.load(environ={"PENPOT_SETTINGS": str(path),
                                              "PENPOT_ACCESS_TOKEN": "t"})
    assert settings.url == "http://a.test" and settings.access_token == "t"


def test_client_needs_url():
    with pytest.raises(ValueError):
        P.PenpotClient(P.PenpotSettings())


# --------------------------------------------------------------------------- auth

def test_login_with_password_then_uses_the_session_cookie(fake):
    client = client_for(fake, email=EMAIL, password=PASSWORD)
    client.push({"square": SQUARE})
    login = fake.calls("login-with-password")[0]
    assert login["body"] == {"email": EMAIL, "password": PASSWORD}
    assert login["headers"]["Content-Type"] == "application/transit+json"
    later = fake.calls("update-file")[0]
    assert later["headers"]["Cookie"] == f"auth-token={SESSION}"
    assert later["headers"]["Accept"] == "application/json"


def test_access_token_goes_in_the_authorization_header(fake):
    fid, _ = fake.add_file()
    client = client_for(fake, access_token=ACCESS)
    client.push({"square": SQUARE}, file_id=fid)
    assert all(r["headers"].get("Authorization") == f"Token {ACCESS}" for r in fake.requests)
    assert not fake.calls("login-with-password")


def test_wrong_credentials_raise_penpot_error(fake):
    client = client_for(fake, email=EMAIL, password="wrong")
    with pytest.raises(P.PenpotError) as err:
        client.login()
    assert err.value.status == 400 and err.value.code == "wrong-credentials"


def test_missing_credentials_raise_penpot_error(fake):
    fid, _ = fake.add_file()
    client = client_for(fake)
    with pytest.raises(P.PenpotError) as err:
        client.pull(file_id=fid)
    assert err.value.status == 401


# --------------------------------------------------------------------------- push

def test_push_creates_a_file_in_the_default_project(fake):
    client = client_for(fake, session_token=SESSION)
    result = client.push({"square": SQUARE}, file_name="Parts")
    created = fake.calls("create-file")[0]["body"]
    assert created == {"project-id": fake.project_id, "name": "Parts"}
    assert result.file_id in fake.files
    assert result.page_id == fake.files[result.file_id]["data"]["pages"][0]
    assert f"file-id={result.file_id}" in result.url and f"team-id={fake.team_id}" in result.url


def test_push_writes_one_path_per_outline_in_mm_with_y_flipped(fake):
    fid, pid = fake.add_file()
    client = client_for(fake, session_token=SESSION, file_id=fid, page_id=pid)
    result = client.push({"plate": PLATE, "triangle": TRIANGLE})
    body = fake.calls("update-file")[0]["body"]
    assert body["id"] == fid and body["revn"] == 0
    assert [c["type"] for c in body["changes"]] == ["add-obj", "add-obj"]
    plate = body["changes"][0]["obj"]
    assert plate["type"] == "path" and plate["name"] == "plate"
    assert plate["id"] == result.shape_ids["plate"]
    assert plate["parent-id"] == ROOT and plate["frame-id"] == ROOT
    commands = [s["command"] for s in plate["content"]]
    assert commands == ["move-to"] + ["line-to"] * 3 + ["close-path"] + \
                       ["move-to"] + ["line-to"] * 3 + ["close-path"]
    first = plate["content"][1]["params"]
    assert (first["x"], first["y"]) == (60.0, -0.0)
    assert plate["selrect"] == {"x": 0.0, "y": -20.0, "width": 60.0, "height": 20.0,
                                "x1": 0.0, "y1": -20.0, "x2": 60.0, "y2": 0.0}
    assert plate["svg-attrs"] == {"fillRule": "evenodd"}
    assert plate["strokes"][0]["stroke-width"] == 0.6


def test_push_keeps_page_coordinates_when_y_up_is_off(fake):
    fid, _ = fake.add_file()
    client = client_for(fake, session_token=SESSION)
    client.push({"t": TRIANGLE}, file_id=fid, y_up=False)
    obj = fake.calls("update-file")[0]["body"]["changes"][0]["obj"]
    assert obj["content"][2]["params"] == {"x": 12.5, "y": 27.25}


def test_push_sends_the_current_revision(fake):
    fid, _ = fake.add_file()
    client = client_for(fake, session_token=SESSION)
    client.push({"a": SQUARE}, file_id=fid)
    client.push({"b": SQUARE}, file_id=fid)
    assert [c["body"]["revn"] for c in fake.calls("update-file")] == [0, 1]


def test_names_that_look_like_transit_markers_stay_text(fake):
    fid, pid = fake.add_file()
    client = client_for(fake, session_token=SESSION)
    client.push({"~draft": SQUARE, "^odd": TRIANGLE}, file_id=fid)
    back = client.pull(file_id=fid)
    assert set(back) == {"~draft", "^odd"}


def test_push_rejects_rings_with_too_few_points(fake):
    client = client_for(fake, session_token=SESSION)
    with pytest.raises(ValueError):
        client.push({"line": [(0, 0), (1, 1)]})


def test_settings_page_is_not_used_for_another_file(fake):
    fid, pid = fake.add_file()
    other, other_page = fake.add_file()
    client = client_for(fake, session_token=SESSION, file_id=fid, page_id=pid)
    result = client.push({"a": SQUARE}, file_id=other)
    assert result.page_id == other_page


# --------------------------------------------------------------------------- pull

def test_round_trip_through_the_fake_server(fake):
    client = client_for(fake, email=EMAIL, password=PASSWORD)
    laid = P.arrange({"square": SQUARE, "plate": PLATE, "triangle": TRIANGLE})
    result = client.push(laid)
    back = client.pull(file_id=result.file_id)
    assert list(back) == ["square", "plate", "triangle"]       # left to right
    for name, outline in laid.items():
        assert_rings_close(back[name]["outer"], outline["outer"])
        assert len(back[name]["holes"]) == len(outline["holes"])
        for a, b in zip(back[name]["holes"], outline["holes"]):
            assert_rings_close(a, b)


def test_pull_reads_every_shape_kind(fake):
    fid, pid = fake.add_file()
    objects = fake.files[fid]["data"]["pagesIndex"][pid]["objects"]

    def add(oid, **obj):
        objects[oid] = {"id": oid, "parentId": ROOT, "frameId": ROOT, **obj}

    add("r", type="rect", name="rotated", x=0,
        points=[{"x": 10, "y": 0}, {"x": 20, "y": 10}, {"x": 10, "y": 20}, {"x": 0, "y": 10}])
    add("c", type="circle", name="disc", x=100,
        points=[{"x": 100, "y": 0}, {"x": 120, "y": 0}, {"x": 120, "y": 10}, {"x": 100, "y": 10}])
    add("s", type="path", name="old format", x=200, content=[
        {"command": "move-to", "params": {"x": 200, "y": 0}},
        {"command": "line-to", "params": {"x": 210, "y": 0}},
        {"command": "line-to", "params": {"x": 210, "y": 10}},
        {"command": "close-path", "params": {}}])
    add("q", type="path", name="curved", x=300, content="M300,0C300,10,310,10,310,0Z")
    add("g", type="group", name="pair", x=400, shapes=["g1", "g2"])
    objects["g1"] = {"id": "g1", "type": "rect", "parentId": "g", "name": "big",
                     "points": [{"x": 400, "y": 0}, {"x": 440, "y": 0},
                                {"x": 440, "y": 40}, {"x": 400, "y": 40}]}
    objects["g2"] = {"id": "g2", "type": "rect", "parentId": "g", "name": "small",
                     "points": [{"x": 410, "y": 10}, {"x": 420, "y": 10},
                                {"x": 420, "y": 20}, {"x": 410, "y": 20}]}
    add("h", type="path", name="hidden", x=500, hidden=True, content="M0,0L1,0L1,1Z")
    add("b", type="frame", name="board", x=600)
    add("d", type="rect", name="disc", x=700,
        points=[{"x": 700, "y": 0}, {"x": 701, "y": 0}, {"x": 701, "y": 1}, {"x": 700, "y": 1}])

    back = client_for(fake, session_token=SESSION).pull(file_id=fid, y_up=False)
    assert list(back) == ["rotated", "disc", "old format", "curved", "pair", "disc_2"]
    assert back["rotated"]["outer"] == [(10, 0), (20, 10), (10, 20), (0, 10)]
    disc = back["disc"]["outer"]
    assert len(disc) == P.ELLIPSE_STEPS
    assert all(abs(((x - 110) / 10) ** 2 + ((y - 5) / 5) ** 2 - 1) < 1e-9 for x, y in disc)
    assert back["old format"]["outer"] == [(200, 0), (210, 0), (210, 10)]
    assert len(back["curved"]["outer"]) == 1 + P.CURVE_STEPS
    assert back["pair"]["outer"][0] == (400, 0) and len(back["pair"]["holes"]) == 1

    shown = client_for(fake, session_token=SESSION).pull(file_id=fid, include_hidden=True)
    assert "hidden" in shown


def test_pull_through_the_exporter(fake):
    fid, pid = fake.add_file()
    objects = fake.files[fid]["data"]["pagesIndex"][pid]["objects"]
    objects["e"] = {"id": "e", "type": "bool", "name": "cut", "parentId": ROOT, "x": 50, "y": 10}
    fake.svg = ('<svg viewBox="-1 -1 22 12"><path d="M0,0L20,0L20,10L0,10Z" fill="#fff"/>'
                '<path d="M0,0L20,0L20,10L0,10Z" stroke="#000"/>'
                '<path d="M5 2 h4 v4 h-4 Z"/></svg>')
    back = client_for(fake, session_token=SESSION).pull(file_id=fid, via="export", y_up=False)
    assert back["cut"]["outer"] == [(49, 9), (69, 9), (69, 19), (49, 19)]
    assert back["cut"]["holes"] == [[(54, 11), (58, 11), (58, 15), (54, 15)]]
    export = fake.calls("export")[0]
    assert export["body"]["cmd"] == "export-shapes" and export["body"]["exports"][0]["type"] == "svg"


def test_pull_rejects_unknown_reader(fake):
    fid, _ = fake.add_file()
    client = client_for(fake, session_token=SESSION)
    client.push({"a": SQUARE}, file_id=fid)
    with pytest.raises(ValueError):
        client.pull(file_id=fid, via="png")


# --------------------------------------------------------------------------- geometry

def test_parse_svg_path_relative_commands_and_implicit_lines():
    rings = P.parse_svg_path("m 1 1 2 0 0 2 h -2 z M10,10 L12,10 V12 H10 Z")
    assert rings == [[(1, 1), (3, 1), (3, 3), (1, 3)], [(10, 10), (12, 10), (12, 12), (10, 12)]]


def test_parse_svg_path_rejects_arcs():
    with pytest.raises(ValueError):
        P.parse_svg_path("M0,0 A5,5 0 0 1 10,0 Z")


def test_normalise_drops_closing_and_repeated_points():
    out = P.normalise([(0, 0), (1, 0), (1, 0), (1, 1), (0, 0)])
    assert out == {"outer": [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)], "holes": []}


def test_arrange_places_outlines_in_one_row():
    laid = P.arrange({"a": SQUARE, "b": TRIANGLE}, pad=5, gap=10)
    assert min(x for x, _ in laid["a"]["outer"]) == 5
    assert min(y for _, y in laid["a"]["outer"]) == 5
    assert min(x for x, _ in laid["b"]["outer"]) == 5 + 25 + 10


def test_apply_cuts_splits_a_part_where_a_blade_crosses():
    pytest.importorskip("shapely")
    parts = {"bar": [(0, 0), (10, 0), (10, 40), (0, 40)],
             "cut_1": [(-5, 19), (15, 19), (15, 21), (-5, 21)]}
    out = P.apply_cuts(parts)
    assert set(out) == {"bar_1", "bar_2"}
    assert max(y for _, y in out["bar_1"]["outer"]) == 40     # highest piece first
    assert max(y for _, y in out["bar_2"]["outer"]) == 19


# --------------------------------------------------------------------------- live

def _live_settings():
    env = os.environ
    if not env.get("PENPOT_URL"):
        return None
    if not (env.get("PENPOT_ACCESS_TOKEN") or env.get("PENPOT_SESSION_TOKEN")
            or (env.get("PENPOT_EMAIL") and env.get("PENPOT_PASSWORD"))):
        return None
    return P.PenpotSettings.from_env(env)


@pytest.mark.skipif(_live_settings() is None,
                    reason="set PENPOT_URL and a credential to run against a real Penpot")
def test_live_round_trip():
    settings = _live_settings()
    settings.file_id = settings.page_id = None
    hole = [(30 + 6 * math.cos(2 * math.pi * k / 24), 10 + 6 * math.sin(2 * math.pi * k / 24))
            for k in range(24)]
    outlines = P.arrange({
        "square": SQUARE,
        "rectangle with hole": {"outer": [(0, 0), (60, 0), (60, 20), (0, 20)], "holes": [hole]},
        "triangle": TRIANGLE,
    })
    client = P.PenpotClient(settings)
    result = client.push(outlines, file_name="review3d round trip test")
    back = client.pull(file_id=result.file_id)
    assert set(back) == set(outlines)
    for name, outline in outlines.items():
        assert_rings_close(back[name]["outer"], outline["outer"])
        assert len(back[name]["holes"]) == len(outline["holes"])
        for a, b in zip(back[name]["holes"], outline["holes"]):
            assert_rings_close(a, b)
