"""The review bench server, which serves the pages, the threads, submissions and "Publish all".

A project builds a `Bench` with its own folders and plug-ins and calls `serve(bench, port)`.

- `model_dirs` are folders searched first for static files. The model's `stages.json` and
  `stages/<name>.glb` live here, and so can any extra pages the project adds.
- `subs_dir` is where submissions and their threads are stored (one JSON file per submission,
  plus PNG pictures).
- `stages_dir` holds the stage files the reply renderer draws from.
- `notifier` is an object with `notify(text) -> (ok, detail)`. The default is `FileNotifier`.
- `rebuild` is a callable that rebuilds the stages and returns a dict, or None.
- `fresh` is a callable returning a dict for `/api/fresh`, or None, which means the stages are
  always reported as up to date.
- `publish_text` is a callable `(ids, subs, stamp, manifest_name) -> str` that builds the message
  sent on publish.
- `extra_get` and `extra_post` are callables `(handler, route)` for routes the project adds. They
  return False when the route is not theirs. Any other return value means the route was answered.

The bench never changes the model. A submission is a request, recorded with its before and after
pictures and the exact transform. Applying it is a separate step that the assistant takes.
"""
import base64
import datetime as _dt
import json
import pathlib
import socketserver
from http.server import SimpleHTTPRequestHandler

from review3d.bench import thread as _TH
from review3d.bench.notify import FileNotifier

STATIC = pathlib.Path(__file__).parent / "static"


def _default_publish_text(ids, subs, stamp, manifest_name):
    first = [f"{d.get('part')}: {(d.get('said') or '').strip()[:60]}" for _, d in subs[:5]]
    return (f"Publish all was pressed at {stamp[9:11]}:{stamp[11:13]}: {len(ids)} submission(s), "
            f"manifest submissions/published/{manifest_name}. First: " + " | ".join(first)
            + ". Read every one and act on it.")


class Bench:
    def __init__(self, model_dirs, subs_dir, stages_dir, notifier=None, rebuild=None, fresh=None,
                 publish_text=None, extra_get=None, extra_post=None, log=print):
        self.model_dirs = [pathlib.Path(d) for d in model_dirs]
        self.subs = pathlib.Path(subs_dir)
        self.stages = pathlib.Path(stages_dir)
        self.notifier = notifier or FileNotifier(self.subs / "published" / "notifications.log")
        self.rebuild = rebuild
        self.fresh = fresh
        self.publish_text = publish_text or _default_publish_text
        self.extra_get = extra_get
        self.extra_post = extra_post
        self.log = log
        _TH.configure(self.subs, self.stages)

    @property
    def published(self):
        return self.subs / "published"


def make_handler(bench):
    dirs = bench.model_dirs + [STATIC]

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(dirs[0]), **kw)

        def log_message(self, *a):
            pass

        def translate_path(self, path):
            # The first folder that has the file wins: the project's folders first, then the
            # bench's own pages. A path that resolves outside its folder is not served from it.
            rel = path.split("?", 1)[0].split("#", 1)[0].lstrip("/")
            for d in dirs:
                cand = (d / rel).resolve()
                if str(cand).startswith(str(d.resolve())) and cand.exists():
                    return str(cand)
            return super().translate_path(path)

        def end_headers(self):
            # Stages can be rebuilt while the page is open, so a cached file could show an old model.
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def send_image(self, body, mime):
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)

        def send_json(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def read_body(self, limit=60 * 1024 * 1024):
            n = int(self.headers.get("Content-Length") or 0)
            if n > limit:
                raise ValueError("request too large")
            return json.loads(self.rfile.read(n) or b"{}") if n else {}

        def do_GET(self):
            route = self.path.split("?")[0]
            try:
                if route == "/api/threads":
                    out = []
                    for d in _TH.every():
                        th = d.get("thread") or []
                        out.append({"id": d["id"], "at": d.get("at"), "part": d.get("part"),
                                    "stage": d.get("stage"), "kind": d.get("kind"),
                                    "state": d.get("state", "new"), "said": d.get("said"),
                                    "replies": len([e for e in th
                                                    if e.get("by") == _TH.ASSISTANT]),
                                    "turns": len(th),
                                    "last": (th[-1].get("text", "")[:90] if th else ""),
                                    "last_by": (th[-1].get("by") if th else None)})
                    return self.send_json({"threads": out})
                if route.startswith("/api/thread/"):
                    sid = route[len("/api/thread/"):].strip("/")
                    if not sid or "/" in sid or ".." in sid:
                        return self.send_json({"error": "bad id"}, 404)
                    if not (bench.subs / f"{sid}.json").exists():
                        return self.send_json({"error": "no such submission"}, 404)
                    return self.send_json(_TH.read(sid))
                if route == "/api/submissions":
                    out = []
                    for f in sorted(bench.subs.glob("*.json"), reverse=True)[:60]:
                        try:
                            out.append(json.loads(f.read_text()))
                        except Exception:
                            continue
                    return self.send_json({"submissions": out})
                if route.startswith("/api/sub/"):
                    name = route[len("/api/sub/"):].strip("/")
                    f = bench.subs / name
                    if f.exists() and f.suffix == ".png" and f.parent == bench.subs:
                        return self.send_image(f.read_bytes(), "image/png")
                    return self.send_json({"error": "no such shot"}, 404)
                if route == "/api/fresh":
                    return self.send_json(bench.fresh() if bench.fresh else
                                          {"stale": [], "up_to_date": True, "rebuilding": False})
                if bench.extra_get and bench.extra_get(self, route) is not False:
                    return None
            except Exception as e:
                return self.send_json({"error": f"{type(e).__name__}: {e}"}, 500)
            return super().do_GET()

        def do_POST(self):
            route = self.path.split("?")[0]
            try:
                if route == "/api/rebuild":
                    if not bench.rebuild:
                        return self.send_json({"error": "this bench has no rebuild"}, 404)
                    return self.send_json(bench.rebuild())
                if route == "/api/comment":
                    body = self.read_body()
                    sid = str(body.get("id") or "")
                    text = str(body.get("text") or "").strip()
                    if not sid or "/" in sid or not (bench.subs / f"{sid}.json").exists():
                        return self.send_json({"error": "no such submission"}, 404)
                    if not text:
                        return self.send_json({"error": "say something"}, 400)
                    d = _TH.append(sid, _TH.REVIEWER, text)
                    bench.log(f"  follow-up on {sid}: {text[:90]}")
                    return self.send_json({"ok": True, "turns": len(d["thread"]), "state": d["state"]})
                if route == "/api/publish":
                    self.read_body()
                    return self.send_json(publish(bench))
                if route == "/api/submit":
                    return self.send_json(submit(bench, self.read_body()))
                if bench.extra_post and bench.extra_post(self, route) is not False:
                    return None
            except Exception as e:
                return self.send_json({"error": f"{type(e).__name__}: {e}"}, 500)
            return self.send_json({"error": "unknown route"}, 404)

    return Handler


def submit(bench, body):
    """Record one submission: the reviewer's words, the exact transform, and the pictures.

    The page sends the pictures as PNG data URLs in `before_png`, `after_png` and `marked_png`.
    They are written next to the submission as `<id>-before.png` and so on, and the JSON keeps
    only their file names under `shots`.
    """
    part = str(body.get("part") or "none")
    safe = "".join(c for c in part if c.isalnum() or c in "_-") or "none"
    now = _dt.datetime.now()
    sid = f"{now.strftime('%Y%m%d-%H%M%S')}-{safe}"
    bench.subs.mkdir(parents=True, exist_ok=True)
    shots = {}
    for which in ("before", "after", "marked"):
        png = body.pop(which + "_png", "")
        if isinstance(png, str) and png.startswith("data:image/png;base64,"):
            name = f"{sid}-{which}.png"
            (bench.subs / name).write_bytes(base64.b64decode(png.split(",", 1)[1]))
            shots[which] = name
    body.update({"id": sid, "at": now.astimezone().isoformat(), "shots": shots})
    body.setdefault("state", "new")
    (bench.subs / f"{sid}.json").write_text(json.dumps(body, indent=2))
    bench.log(f"  submission {sid}: {body.get('kind')} on {part}: {(body.get('said') or '')[:70]}")
    return {"ok": True, "id": sid, "shots": shots}


def publish(bench):
    """Stamp every unpublished submission, write a manifest, and notify the assistant once.

    Returns a dict with `count`, `manifest`, `pinged` and `detail`. When nothing is new, the count
    is 0 and the notifier is not called.
    """
    subs = []
    for f in sorted(bench.subs.glob("*.json")):
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        # Selected by the `published` stamp, not by the state. A thread moves on to answered or
        # fixed after it is published, so selecting by state would publish it a second time.
        if not d.get("published"):
            subs.append((f, d))
    if not subs:
        return {"ok": True, "count": 0, "pinged": False, "detail": "nothing new to publish"}
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    ids = [d.get("id") or f.stem for f, d in subs]
    bench.published.mkdir(parents=True, exist_ok=True)
    manifest = bench.published / f"publish-{stamp}.json"
    manifest.write_text(json.dumps({"at": _dt.datetime.now().astimezone().isoformat(),
                                    "count": len(ids), "ids": ids}, indent=1))
    for f, d in subs:
        d["published"] = stamp
        d.setdefault("state", "new")
        f.write_text(json.dumps(d, indent=1, ensure_ascii=False))
    try:
        pinged, detail = bench.notifier.notify(bench.publish_text(ids, subs, stamp, manifest.name))
    except Exception as e:
        pinged, detail = False, f"{type(e).__name__}: {e}"
    bench.log(f"  publish: {len(ids)} -> {manifest.name}; pinged={pinged} {str(detail)[-120:]}")
    return {"ok": True, "count": len(ids), "manifest": manifest.name, "pinged": pinged,
            "detail": detail}


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def serve(bench, port, host=""):
    """Serve the bench on every interface (host "") or on one address. Blocks until stopped."""
    _Server((host, port), make_handler(bench)).serve_forever()
