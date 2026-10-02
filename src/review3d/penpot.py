"""Send 2D outlines to a Penpot file and read a person's edits from it.

Penpot is an open-source design tool that can run on your own machine. This
module talks to its backend API over HTTP, with nothing beyond the Python
standard library, so a person can open the outlines in the Penpot editor,
move, rotate, duplicate or redraw them, and the program can read the result
as numbers.

Typical use::

    from review3d import penpot

    settings = penpot.PenpotSettings.load("penpot.toml")   # plus PENPOT_* variables
    client = penpot.PenpotClient(settings)
    result = client.push({
        "plate": [(0, 0), (40, 0), (40, 20), (0, 20)],
        "washer": {"outer": [...], "holes": [[...]]},
    }, file_name="Parts for review")
    print(result.url)                      # open this in a browser
    edited = client.pull(file_id=result.file_id)
    edited["plate"]["outer"]               # [(x, y), ...] in mm

Things that are true of this module and worth knowing before you use it:

Units. One Penpot unit is one millimetre. Penpot calls the unit a pixel, but
nothing is scaled on the way in or out, so a length measured on the Penpot
canvas is the real length of the part. ``UNITS_PER_MM`` holds the factor.

Axes. Penpot's canvas grows downward. Most CAD and mesh tools use a frame where
y grows upward. With ``y_up=True`` (the default) y is negated on the way in and
negated again on the way out, so a part does not appear upside down in the
editor and returns in the frame it left. Pass ``y_up=False`` to send page
coordinates unchanged.

No import step. Penpot imports SVG files in the browser, not on the server, so
there is no API call that imports a file. Each outline is therefore written as
a native Penpot path shape with real line segments. It opens already
selectable and editable, and the person has nothing to import.

Holes. An outline with holes becomes one path with several closed sub-paths,
drawn with the even-odd fill rule so the holes show as holes. When reading, the
ring with the largest area is the outer boundary and every other ring is a hole.

Reading. By default ``pull`` reads the stored geometry of each top-level shape
from the file: path and boolean shapes from their path data, rectangles and
ellipses from their corner points (so rotation is included). Groups are read as
the rings of all their children. Penpot's own SVG exporter can resolve every
construct an editor can produce, so ``pull(via="export")`` asks the exporter
for an SVG of each shape and reads that instead. It needs the exporter service
to be running and it is much slower.

Cuts. A person may want to divide an outline without learning Penpot's boolean
operations. ``apply_cuts`` treats every shape whose name starts with ``cut`` as
a blade: blades are subtracted from every other outline they cross, and each
remaining island becomes its own outline, numbered from top to bottom. Moving a
blade in the editor changes the division, and deleting one joins the pieces
again. This step needs the optional ``shapely`` package.

Settings. ``PenpotSettings`` can be read from a TOML or JSON file and from
environment variables (``PENPOT_URL``, ``PENPOT_EMAIL``, ``PENPOT_PASSWORD``,
``PENPOT_ACCESS_TOKEN``, ``PENPOT_SESSION_TOKEN``, ``PENPOT_TEAM_ID``,
``PENPOT_PROJECT_ID``, ``PENPOT_FILE_ID``, ``PENPOT_PAGE_ID``). Variables win
over the file. Authenticate with either an access token made in Penpot's
account settings, a session token from an earlier login, or an email and
password. Keep these values out of version control.
"""
from __future__ import annotations

import json
import math
import os
import re
import uuid
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
import urllib.error
import urllib.parse
import urllib.request

__all__ = [
    "UNITS_PER_MM",
    "PenpotError",
    "PenpotSettings",
    "PenpotClient",
    "PushResult",
    "push",
    "pull",
    "normalise",
    "arrange",
    "apply_cuts",
    "parse_svg_path",
]

UNITS_PER_MM = 1.0
ROOT_ID = "00000000-0000-0000-0000-000000000000"
CURVE_STEPS = 8          # points sampled along each Bezier segment when reading
ELLIPSE_STEPS = 64       # points used to describe an ellipse when reading
DEDUPE_MM = 1e-4         # repeated vertices closer than this are dropped
CUT_PREFIX = "cut"
MIN_PIECE_MM2 = 8.0      # pieces smaller than this after a cut are discarded

Point = tuple[float, float]


class PenpotError(RuntimeError):
    """The Penpot server refused a request or answered with an error."""

    def __init__(self, status: int, code: str | None, hint: str | None, command: str):
        self.status = status
        self.code = code
        self.hint = hint
        self.command = command
        super().__init__(f"penpot {command} failed: HTTP {status} {code or ''} {hint or ''}".strip())


# --------------------------------------------------------------------------- settings

_ENV = {
    "url": "PENPOT_URL",
    "email": "PENPOT_EMAIL",
    "password": "PENPOT_PASSWORD",
    "access_token": "PENPOT_ACCESS_TOKEN",
    "session_token": "PENPOT_SESSION_TOKEN",
    "team_id": "PENPOT_TEAM_ID",
    "project_id": "PENPOT_PROJECT_ID",
    "file_id": "PENPOT_FILE_ID",
    "page_id": "PENPOT_PAGE_ID",
}
_ALIASES = {"base_url": "url", "auth_token": "session_token", "token": "access_token"}


@dataclass
class PenpotSettings:
    """Where the Penpot server is, how to authenticate, and which file to use.

    ``url`` is the address of the Penpot web app, the same address a
    browser opens. Give one of ``access_token``, ``session_token``
    or ``email`` with ``password``. ``project_id`` is where new files are made
    (the profile's default project when empty). ``file_id`` and ``page_id``
    name an existing file and page to write to and read from.
    """

    url: str = ""
    email: str | None = None
    password: str | None = field(default=None, repr=False)
    access_token: str | None = field(default=None, repr=False)
    session_token: str | None = field(default=None, repr=False)
    team_id: str | None = None
    project_id: str | None = None
    file_id: str | None = None
    page_id: str | None = None
    timeout: float = 60.0

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "PenpotSettings":
        """Build settings from a dict. Unknown keys are ignored."""
        if isinstance(data.get("penpot"), Mapping):
            data = data["penpot"]
        names = {f.name for f in fields(cls)}
        kwargs: dict[str, Any] = {}
        for key, value in data.items():
            key = _ALIASES.get(key, key)
            if key in names and value not in (None, ""):
                kwargs[key] = float(value) if key == "timeout" else value
        return cls(**kwargs)

    @classmethod
    def from_file(cls, path: str | os.PathLike) -> "PenpotSettings":
        """Read a ``.toml`` or ``.json`` file. Keys may sit in a ``[penpot]`` table."""
        path = Path(path)
        text = path.read_text()
        if path.suffix.lower() == ".toml":
            import tomllib
            data = tomllib.loads(text)
        else:
            data = json.loads(text)
        return cls.from_mapping(data)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None,
                 base: "PenpotSettings | None" = None) -> "PenpotSettings":
        """Apply ``PENPOT_*`` variables on top of ``base`` (or of empty settings)."""
        environ = os.environ if environ is None else environ
        settings = base or cls()
        changes = {name: environ[var] for name, var in _ENV.items() if environ.get(var)}
        if environ.get("PENPOT_TIMEOUT"):
            changes["timeout"] = float(environ["PENPOT_TIMEOUT"])
        return replace(settings, **changes)

    @classmethod
    def load(cls, path: str | os.PathLike | None = None,
             environ: Mapping[str, str] | None = None) -> "PenpotSettings":
        """Read ``path`` if given (or ``PENPOT_SETTINGS``), then apply the environment."""
        environ = os.environ if environ is None else environ
        path = path or environ.get("PENPOT_SETTINGS")
        base = cls.from_file(path) if path else cls()
        return cls.from_env(environ, base)


# --------------------------------------------------------------------------- transit

class _Kw(str):
    """A Penpot keyword such as ``:path``. Written as ``~:path``."""


class _Uid(str):
    """A uuid value. Written as ``~u<uuid>``."""


def _encode(value: Any) -> Any:
    """Convert Python values to Penpot's transit JSON (the verbose form, no key cache).

    Dict keys become keywords. A plain string that starts with one of transit's
    marker characters is escaped so a shape name such as ``~draft`` stays text.
    """
    if isinstance(value, _Kw):
        return "~:" + value
    if isinstance(value, _Uid):
        return "~u" + value
    if isinstance(value, str):
        return "~" + value if value[:1] in ("~", "^", "`") else value
    if isinstance(value, Mapping):
        out: list[Any] = ["^ "]
        for k, v in value.items():
            out += ["~:" + str(k), _encode(v)]
        return out
    if isinstance(value, (list, tuple)):
        return [_encode(v) for v in value]
    return value


# --------------------------------------------------------------------------- geometry

def normalise(outline: Any) -> dict[str, list]:
    """Return ``{"outer": [(x, y), ...], "holes": [[(x, y), ...], ...]}``.

    Accepts a plain list of points, or a mapping with ``outer`` and optional
    ``holes``. A closing point equal to the first point is dropped.
    """
    if isinstance(outline, Mapping):
        outer, holes = outline["outer"], outline.get("holes") or []
    else:
        outer, holes = outline, []
    outer = _ring(outer)
    holes = [_ring(h) for h in holes]
    if len(outer) < 3 or any(len(h) < 3 for h in holes):
        raise ValueError("every ring needs at least three distinct points")
    return {"outer": outer, "holes": holes}


def _ring(points: Iterable[Sequence[float]]) -> list[Point]:
    out: list[Point] = []
    for p in points:
        q = (float(p[0]), float(p[1]))
        if out and max(abs(q[0] - out[-1][0]), abs(q[1] - out[-1][1])) <= DEDUPE_MM:
            continue
        out.append(q)
    if len(out) > 1 and max(abs(out[0][0] - out[-1][0]), abs(out[0][1] - out[-1][1])) <= DEDUPE_MM:
        out.pop()
    return out


def _area(ring: Sequence[Point]) -> float:
    n = len(ring)
    return 0.5 * sum(ring[i][0] * ring[(i + 1) % n][1] - ring[(i + 1) % n][0] * ring[i][1]
                     for i in range(n))


def _bounds(rings: Iterable[Sequence[Point]]) -> tuple[float, float, float, float]:
    xs = [p[0] for r in rings for p in r]
    ys = [p[1] for r in rings for p in r]
    return min(xs), min(ys), max(xs), max(ys)


def arrange(outlines: Mapping[str, Any], pad: float = 20.0, gap: float = 30.0) -> dict[str, dict]:
    """Place outlines in one row, left to right, so none overlap on the page.

    Each outline is moved so its left edge sits ``gap`` mm after the previous
    one and its lowest point is ``pad`` mm from the axis. Use the result for
    ``push`` when the outlines share an origin and would otherwise be drawn on
    top of each other.
    """
    out: dict[str, dict] = {}
    cursor = pad
    for name, outline in outlines.items():
        o = normalise(outline)
        x0, y0, x1, _ = _bounds([o["outer"], *o["holes"]])
        dx, dy = cursor - x0, pad - y0
        out[name] = {"outer": [(x + dx, y + dy) for x, y in o["outer"]],
                     "holes": [[(x + dx, y + dy) for x, y in h] for h in o["holes"]]}
        cursor += (x1 - x0) + gap
    return out


_TOKEN = re.compile(r"([A-Za-z])|(-?\d*\.?\d+(?:[eE][-+]?\d+)?)")


def parse_svg_path(d: str) -> list[list[Point]]:
    """Read SVG path data into a list of rings.

    Handles the commands Penpot writes (M, L, H, V, C, Z, in absolute and
    relative form). Cubic curves are sampled with ``CURVE_STEPS`` points.
    Other commands raise ``ValueError``.
    """
    toks: list[Any] = [m.group(1) or float(m.group(2)) for m in _TOKEN.finditer(d)]
    rings: list[list[Point]] = []
    ring: list[Point] = []
    cur: Point = (0.0, 0.0)
    start: Point = (0.0, 0.0)
    cmd = "M"
    i = 0
    while i < len(toks):
        t = toks[i]
        if isinstance(t, str):
            cmd = t
            i += 1
            if cmd not in "MmLlHhVvCcZz":
                raise ValueError(f"unhandled path command {cmd!r}")
            if cmd in "Zz":
                if len(ring) >= 3:
                    rings.append(ring)
                ring, cur = [], start
                continue
        rel = cmd.islower()
        c = cmd.upper()
        if c == "M":
            x, y = toks[i], toks[i + 1]
            i += 2
            cur = (cur[0] + x, cur[1] + y) if rel else (x, y)
            if len(ring) >= 3:
                rings.append(ring)
            ring, start = [cur], cur
            cmd = "l" if rel else "L"          # a move is followed by implicit line-to
        elif c == "L":
            x, y = toks[i], toks[i + 1]
            i += 2
            cur = (cur[0] + x, cur[1] + y) if rel else (x, y)
            ring.append(cur)
        elif c == "H":
            x = toks[i]
            i += 1
            cur = (cur[0] + x if rel else x, cur[1])
            ring.append(cur)
        elif c == "V":
            y = toks[i]
            i += 1
            cur = (cur[0], cur[1] + y if rel else y)
            ring.append(cur)
        elif c == "C":
            p = toks[i:i + 6]
            i += 6
            c1, c2, end = [(cur[0] + p[k], cur[1] + p[k + 1]) if rel else (p[k], p[k + 1])
                           for k in (0, 2, 4)]
            ring += _cubic(cur, c1, c2, end)
            cur = end
        else:
            raise ValueError(f"unhandled path command {cmd!r}")
    if len(ring) >= 3:
        rings.append(ring)
    return rings


def _cubic(p0: Point, c1: Point, c2: Point, p3: Point) -> list[Point]:
    out = []
    for k in range(1, CURVE_STEPS + 1):
        u = k / CURVE_STEPS
        v = 1 - u
        out.append((v ** 3 * p0[0] + 3 * v * v * u * c1[0] + 3 * v * u * u * c2[0] + u ** 3 * p3[0],
                    v ** 3 * p0[1] + 3 * v * v * u * c1[1] + 3 * v * u * u * c2[1] + u ** 3 * p3[1]))
    return out


def _segments_to_rings(content: list) -> list[list[Point]]:
    """Read path content written as a list of segments (older Penpot versions)."""
    rings: list[list[Point]] = []
    ring: list[Point] = []
    cur: Point = (0.0, 0.0)
    for seg in content:
        command = str(seg.get("command", "")).lstrip(":")
        params = seg.get("params") or {}
        if command == "move-to":
            if len(ring) >= 3:
                rings.append(ring)
            cur = (float(params["x"]), float(params["y"]))
            ring = [cur]
        elif command == "line-to":
            cur = (float(params["x"]), float(params["y"]))
            ring.append(cur)
        elif command == "curve-to":
            end = (float(params["x"]), float(params["y"]))
            ring += _cubic(cur, (float(params["c1x"]), float(params["c1y"])),
                           (float(params["c2x"]), float(params["c2y"])), end)
            cur = end
        elif command == "close-path":
            if len(ring) >= 3:
                rings.append(ring)
            ring = []
    if len(ring) >= 3:
        rings.append(ring)
    return rings


def _ellipse(points: list[Point]) -> list[Point]:
    """An ellipse from the four corner points of its bounding box (rotation included)."""
    (x0, y0), (x1, y1), _, (x3, y3) = points[:4]
    cx, cy = (x1 + x3) / 2, (y1 + y3) / 2
    ux, uy = (x1 - x0) / 2, (y1 - y0) / 2
    vx, vy = (x3 - x0) / 2, (y3 - y0) / 2
    return [(cx + ux * math.cos(t) + vx * math.sin(t), cy + uy * math.cos(t) + vy * math.sin(t))
            for t in (2 * math.pi * k / ELLIPSE_STEPS for k in range(ELLIPSE_STEPS))]


# --------------------------------------------------------------------------- client

@dataclass
class PushResult:
    """What ``push`` wrote: the file, the page, and the id of each new shape."""

    file_id: str
    page_id: str
    shape_ids: dict[str, str]
    url: str


class PenpotClient:
    """A small client for the parts of the Penpot backend API this module needs.

    Requests are sent as transit JSON, which is what Penpot expects for changes
    to a file. Answers are requested as plain JSON (``Accept:
    application/json``), which avoids decoding transit's key cache.
    """

    def __init__(self, settings: PenpotSettings | None = None):
        self.settings = settings or PenpotSettings.load()
        if not self.settings.url:
            raise ValueError("Penpot settings need a url (or PENPOT_URL)")
        self.base = self.settings.url.rstrip("/")
        self._session = self.settings.session_token
        self._profile: dict | None = None

    # -- transport

    def _headers(self, content_type: str = "application/transit+json") -> dict[str, str]:
        headers = {"Content-Type": content_type, "Accept": "application/json"}
        if self.settings.access_token:
            headers["Authorization"] = f"Token {self.settings.access_token}"
        if self._session:
            headers["Cookie"] = f"auth-token={self._session}"
        return headers

    def _send(self, url: str, body: Any, command: str, headers: dict[str, str] | None = None):
        data = None if body is None else json.dumps(_encode(body)).encode()
        req = urllib.request.Request(url, data=data, headers=headers or self._headers(),
                                     method="POST" if data is not None else "GET")
        try:
            resp = urllib.request.urlopen(req, timeout=self.settings.timeout)
            return resp, resp.read()
        except urllib.error.HTTPError as err:
            raw = err.read().decode(errors="replace")
            code = hint = None
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    code = str(parsed.get("code") or parsed.get("~:code") or "").lstrip("~:") or None
                    hint = parsed.get("hint") or parsed.get("~:hint")
            except ValueError:
                hint = raw[:300] or None
            raise PenpotError(err.code, code, hint, command) from None

    def rpc(self, command: str, params: Mapping[str, Any] | None = None) -> Any:
        """Call ``/api/rpc/command/<command>`` and return the decoded JSON answer."""
        resp, raw = self._send(f"{self.base}/api/rpc/command/{command}", dict(params or {}), command)
        if command == "login-with-password":
            for cookie in resp.headers.get_all("Set-Cookie") or []:
                if "auth-token=" in cookie:
                    self._session = cookie.split("auth-token=", 1)[1].split(";", 1)[0]
        return json.loads(raw) if raw else None

    # -- account

    def login(self, register: bool = False, fullname: str = "review3d") -> dict:
        """Authenticate with the email and password from the settings.

        With ``register=True`` a profile that does not exist yet is created
        first. That only works on a Penpot instance that allows registration
        without email confirmation, which is common for a local install.
        """
        s = self.settings
        if not (s.email and s.password):
            raise ValueError("login needs an email and a password")
        try:
            profile = self.rpc("login-with-password", {"email": s.email, "password": s.password})
        except PenpotError:
            if not register:
                raise
            prep = self.rpc("prepare-register-profile",
                            {"fullname": fullname, "email": s.email, "password": s.password})
            self.rpc("register-profile", {"token": prep["token"]})
            profile = self.rpc("login-with-password", {"email": s.email, "password": s.password})
        self._profile = profile
        return profile

    def profile(self) -> dict:
        """The authenticated profile. Calls ``login`` first when only a password is configured."""
        if self._profile is None:
            if not (self._session or self.settings.access_token):
                self.login()
            else:
                self._profile = self.rpc("get-profile", {})
        return self._profile

    # -- files

    def create_file(self, name: str, project_id: str | None = None) -> str:
        """Create an empty file and return its id."""
        project_id = project_id or self.settings.project_id or self.profile()["defaultProjectId"]
        created = self.rpc("create-file", {"project-id": _Uid(project_id), "name": name})
        return created["id"]

    def get_file(self, file_id: str) -> dict:
        return self.rpc("get-file", {"id": _Uid(file_id)})

    def workspace_url(self, file_id: str, page_id: str | None = None) -> str:
        team = self.settings.team_id or self.profile().get("defaultTeamId", "")
        query = {"team-id": team, "file-id": file_id}
        if page_id:
            query["page-id"] = page_id
        return f"{self.base}/#/workspace?{urllib.parse.urlencode(query)}"

    def _target(self, file_id: str | None, page_id: str | None) -> tuple[str | None, str | None]:
        """The file and page to use. The page from the settings only applies to their file."""
        s = self.settings
        if file_id is None or file_id == s.file_id:
            return file_id or s.file_id, page_id or s.page_id
        return file_id, page_id

    @staticmethod
    def _page(file: dict, page_id: str | None) -> str:
        pages = file["data"].get("pages") or list(file["data"].get("pagesIndex", {}))
        if page_id:
            return page_id
        if not pages:
            raise PenpotError(200, "no-pages", "the file has no pages", "get-file")
        return pages[0]

    # -- push

    def push(self, outlines: Mapping[str, Any], *, file_id: str | None = None,
             page_id: str | None = None, file_name: str = "review3d outlines",
             y_up: bool = True, fill: str = "#ffffff", stroke: str = "#111111",
             stroke_width: float = 0.6) -> PushResult:
        """Write each outline as one editable path shape and return where they went.

        ``outlines`` maps a name to a list of ``(x, y)`` points in mm, or to
        ``{"outer": points, "holes": [points, ...]}``. When no file is given
        (here or in the settings) a new file named ``file_name`` is created.
        Points are written as given, so call ``arrange`` first if outlines
        would overlap.
        """
        file_id, page_id = self._target(file_id, page_id)
        if not file_id:
            file_id = self.create_file(file_name)
        file = self.get_file(file_id)
        page_id = self._page(file, page_id)

        shape_ids: dict[str, str] = {}
        changes = []
        for name, outline in outlines.items():
            sid = str(uuid.uuid4())
            obj = self._path_obj(sid, str(name), normalise(outline), y_up, fill, stroke, stroke_width)
            changes.append({"type": _Kw("add-obj"), "page-id": _Uid(page_id), "id": _Uid(sid),
                            "frame-id": _Uid(ROOT_ID), "parent-id": _Uid(ROOT_ID), "obj": obj})
            shape_ids[str(name)] = sid
        if changes:
            self.rpc("update-file", {"id": _Uid(file_id), "session-id": _Uid(str(uuid.uuid4())),
                                     "revn": file.get("revn", 0), "vern": file.get("vern", 0),
                                     "changes": changes})
        return PushResult(file_id, page_id, shape_ids, self.workspace_url(file_id, page_id))

    @staticmethod
    def _path_obj(sid: str, name: str, outline: dict, y_up: bool, fill: str, stroke: str,
                  stroke_width: float) -> dict:
        sign = -1.0 if y_up else 1.0
        rings = [[(x * UNITS_PER_MM, sign * y * UNITS_PER_MM) for x, y in r]
                 for r in [outline["outer"], *outline["holes"]]]
        content = []
        for ring in rings:
            content.append({"command": _Kw("move-to"), "params": {"x": ring[0][0], "y": ring[0][1]}})
            content += [{"command": _Kw("line-to"), "params": {"x": x, "y": y}} for x, y in ring[1:]]
            content.append({"command": _Kw("close-path"), "params": {}})
        x0, y0, x1, y1 = _bounds(rings)
        w, h = x1 - x0, y1 - y0
        identity = {"a": 1.0, "b": 0.0, "c": 0.0, "d": 1.0, "e": 0.0, "f": 0.0}
        return {
            "id": _Uid(sid), "name": name, "type": _Kw("path"),
            "parent-id": _Uid(ROOT_ID), "frame-id": _Uid(ROOT_ID),
            "x": x0, "y": y0, "width": w, "height": h,
            "content": content,
            "selrect": {"x": x0, "y": y0, "width": w, "height": h,
                        "x1": x0, "y1": y0, "x2": x1, "y2": y1},
            "points": [{"x": x0, "y": y0}, {"x": x1, "y": y0}, {"x": x1, "y": y1}, {"x": x0, "y": y1}],
            "fills": [{"fill-color": fill, "fill-opacity": 1.0}],
            "strokes": [{"stroke-color": stroke, "stroke-opacity": 1.0, "stroke-width": stroke_width,
                         "stroke-style": _Kw("solid"), "stroke-alignment": _Kw("center")}],
            "svg-attrs": {"fillRule": "evenodd"},
            "rotation": 0, "opacity": 1.0,
            "transform": identity, "transform-inverse": identity,
        }

    # -- pull

    def pull(self, *, file_id: str | None = None, page_id: str | None = None, y_up: bool = True,
             via: str = "file", include_hidden: bool = False, cuts: bool = False) -> dict[str, dict]:
        """Read every top-level shape on the page as outlines in mm.

        Returns ``{name: {"outer": [(x, y), ...], "holes": [[...], ...]}}``,
        ordered from left to right. Boards (frames) are skipped, and hidden
        shapes are skipped unless ``include_hidden`` is set. Two shapes with
        the same name get ``_2``, ``_3`` and so on. ``via`` is ``"file"`` or
        ``"export"`` (see the module notes). With ``cuts=True`` the result is
        passed through ``apply_cuts``.
        """
        file_id, page_id = self._target(file_id, page_id)
        if not file_id:
            raise ValueError("pull needs a file id")
        file = self.get_file(file_id)
        page_id = self._page(file, page_id)
        objects = file["data"]["pagesIndex"][page_id]["objects"]
        tops = [(oid, o) for oid, o in objects.items()
                if oid != ROOT_ID and o.get("parentId") == ROOT_ID and o.get("type") != "frame"
                and (include_hidden or not o.get("hidden"))]
        tops.sort(key=lambda item: float(item[1].get("x") or 0.0))

        sign = -1.0 if y_up else 1.0
        result: dict[str, dict] = {}
        for oid, obj in tops:
            if via == "export":
                rings = self._rings_from_export(file_id, page_id, oid, obj)
            elif via == "file":
                rings = _rings_from_object(obj, objects)
            else:
                raise ValueError("via must be 'file' or 'export'")
            rings = [r for r in (_ring(r) for r in rings) if len(r) >= 3]
            if not rings:
                continue
            rings = [[(x / UNITS_PER_MM, sign * y / UNITS_PER_MM) for x, y in r] for r in rings]
            rings.sort(key=lambda r: -abs(_area(r)))
            name = base = obj.get("name") or oid[:8]
            n = 2
            while name in result:
                name, n = f"{base}_{n}", n + 1
            result[name] = {"outer": rings[0], "holes": rings[1:]}
        return apply_cuts(result) if cuts else result

    def _rings_from_export(self, file_id: str, page_id: str, oid: str, obj: dict) -> list[list[Point]]:
        """Ask Penpot's exporter for an SVG of one shape and read its outlines."""
        profile_id = self.profile()["id"]
        body = {"cmd": _Kw("export-shapes"), "profile-id": _Uid(profile_id), "wait": True,
                "exports": [{"file-id": _Uid(file_id), "page-id": _Uid(page_id),
                             "object-id": _Uid(oid), "name": obj.get("name") or oid,
                             "type": _Kw("svg"), "suffix": "", "scale": 1}]}
        headers = self._headers()
        headers.pop("Accept")
        _, raw = self._send(f"{self.base}/api/export", body, "export", headers)
        meta = json.loads(raw)
        uri = meta.get("~:uri") or meta.get("uri")
        if isinstance(uri, dict):
            uri = uri.get("~#uri") or next(iter(uri.values()))
        _, raw = self._send(uri, None, "export download", self._headers())
        svg = raw.decode()
        view = re.search(r'viewBox="([^"]+)"', svg)
        vb = [float(v) for v in view.group(1).replace(",", " ").split()] if view else [0.0, 0.0]
        ox = float(obj.get("x") or 0.0) + vb[0]
        oy = float(obj.get("y") or 0.0) + vb[1]
        seen: set[str] = set()
        rings: list[list[Point]] = []
        for d in re.findall(r'\sd="([^"]+)"', svg):
            if d in seen:                 # a fill and its stroke repeat the same outline
                continue
            seen.add(d)
            rings += [[(x + ox, y + oy) for x, y in r] for r in parse_svg_path(d)]
        return rings


def _rings_from_object(obj: dict, objects: Mapping[str, dict]) -> list[list[Point]]:
    """Read the stored geometry of one shape, in page units."""
    kind = obj.get("type")
    content = obj.get("content")
    if kind in ("path", "bool") and content:
        if isinstance(content, str):
            return parse_svg_path(content)
        return _segments_to_rings(content)
    points = [(float(p["x"]), float(p["y"])) for p in obj.get("points") or []]
    if kind == "rect" and len(points) == 4:
        return [points]
    if kind == "circle" and len(points) == 4:
        return [_ellipse(points)]
    if kind in ("group", "bool"):
        rings: list[list[Point]] = []
        for child in obj.get("shapes") or []:
            if child in objects and not objects[child].get("hidden"):
                rings += _rings_from_object(objects[child], objects)
        return rings
    return []


# --------------------------------------------------------------------------- cuts

def apply_cuts(outlines: Mapping[str, Any], prefix: str = CUT_PREFIX,
               min_area: float = MIN_PIECE_MM2) -> dict[str, dict]:
    """Subtract every outline named ``prefix...`` from the others and split what is left.

    Each surviving island becomes its own outline. When a part is split into more
    than one piece the pieces are named ``<name>_1``, ``<name>_2`` and so on,
    from the highest piece to the lowest. Pieces smaller than ``min_area``
    mm2 are discarded. Needs ``shapely``.
    """
    try:
        from shapely.geometry import Polygon
        from shapely.ops import unary_union
    except ImportError as err:            # pragma: no cover - depends on the environment
        raise ImportError("apply_cuts needs the 'shapely' package") from err

    norm = {name: normalise(o) for name, o in outlines.items()}
    blades = [Polygon(o["outer"], o["holes"]) for n, o in norm.items()
              if n.lower().startswith(prefix)]
    stock = {n: o for n, o in norm.items() if not n.lower().startswith(prefix)}
    blades = [b if b.is_valid else b.buffer(0) for b in blades]
    blades = [b for b in blades if not b.is_empty]
    if not blades:
        return stock
    blade = unary_union(blades)

    out: dict[str, dict] = {}
    for name, o in stock.items():
        geom = Polygon(o["outer"], o["holes"])
        if not geom.is_valid:
            geom = geom.buffer(0)
        cut = geom.difference(blade)
        pieces = [g for g in getattr(cut, "geoms", [cut]) if g.area >= min_area]
        pieces.sort(key=lambda g: -g.bounds[3])
        for i, g in enumerate(pieces, 1):
            label = name if len(pieces) == 1 else f"{name}_{i}"
            out[label] = {"outer": [(float(x), float(y)) for x, y in g.exterior.coords[:-1]],
                          "holes": [[(float(x), float(y)) for x, y in r.coords[:-1]]
                                    for r in g.interiors]}
    return out


# --------------------------------------------------------------------------- shortcuts

def push(outlines: Mapping[str, Any], settings: PenpotSettings | None = None, **kwargs) -> PushResult:
    """``PenpotClient(settings).push(outlines, **kwargs)``. Settings default to ``load()``."""
    return PenpotClient(settings).push(outlines, **kwargs)


def pull(settings: PenpotSettings | None = None, **kwargs) -> dict[str, dict]:
    """``PenpotClient(settings).pull(**kwargs)``. Settings default to ``load()``."""
    return PenpotClient(settings).pull(**kwargs)
