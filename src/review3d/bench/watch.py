"""Keep the bench's pictures current by themselves: a watcher that rebuilds what has fallen behind.

The person reviewing should never have to press anything to see the current model. A `Watcher`
asks `stale()` every `every` seconds and hands what it lists to `repair(names)`. It waits while
the code is still being edited (any file from `sources()` changed in the last `quiet_s` seconds),
because a rebuild started after the first of a run of edits builds code that is already out of
date and takes the machine from the next one. It also waits while a hold file exists and is under
`hold_s` old, so someone testing a run of changes can pause it, and a hold someone forgot stops
holding by itself. A hold file that names its end (an ISO time with its zone) holds until then, for a
pause longer than `hold_s` such as a redesign, but never more than `HOLD_MAX_S` (a day) after it was
written.

`status()` is the dict a bench serves at `/api/fresh` (pass it as `Bench(fresh=...)`).

`run_step` runs one rebuild step in a fresh interpreter, in its own process group, so a timeout
ends everything it started. `stop_steps()` ends any still running, for a clean restart: an
orphaned rebuild keeps holding the rebuild lock, and every later rebuild is skipped.
`stop_on_signals()` does that when the server is told to stop (SIGTERM or SIGINT).

    w = Watcher(stale=my_stale, repair=my_repair, sources=my_sources, hold="kitview/.hold")
    w.start()
    bench = Bench(..., fresh=w.status, rebuild=lambda: w.pass_once(force=True))
"""
import atexit
import datetime as _dt
import os
import pathlib
import signal
import subprocess
import sys
import threading
import time

_STEPS = set()
_STOPPING = [False]


#: the longest a hold that names its end may last, from when it was written
HOLD_MAX_S = 24 * 3600.0


def hold_until(text):
    """The end a hold file names, as a Unix time, or None for a plain hold.

    The file may hold an ISO time with its zone, such as `2026-10-31T09:00:00+02:00`. A time without
    a zone, a relative time, or anything else (an empty file included) is a plain hold of `hold_s`.
    """
    s = (text or "").strip()
    if not s:
        return None
    try:
        when = _dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    if when.tzinfo is None:
        return None
    return when.timestamp()


class Watcher:
    """Rebuild what `stale()` lists, in the background, once the code has gone quiet.

    `stale` returns a list of names (or `(name, minutes)` pairs) that are behind. `repair(names)`
    rebuilds them. `sources` returns the files whose edits count as "still working". `hold` is a
    file that pauses the watcher while it is under `hold_s` seconds old.
    """

    def __init__(self, stale, repair, sources=None, quiet_s=300.0, every=45.0, hold=None,
                 hold_s=3600.0):
        self.stale, self.repair, self.sources = stale, repair, sources
        self.quiet_s, self.every, self.hold_s = float(quiet_s), float(every), float(hold_s)
        self.hold = pathlib.Path(hold) if hold else None
        self.state = {"checked": None, "rebuilding": False, "last": [], "error": None}
        self._busy = threading.Lock()

    def editing(self):
        """True while a source file changed in the last `quiet_s` seconds."""
        if not self.sources:
            return False
        try:
            newest = max(pathlib.Path(p).stat().st_mtime for p in self.sources())
        except (OSError, ValueError):
            return False
        return time.time() - newest < self.quiet_s

    def held(self):
        """True while the hold holds: an empty hold file for `hold_s` seconds after it was touched, or
        one that names its end (`hold_until`) until then, but never more than `HOLD_MAX_S` after it
        was written, so a hold someone forgot still ends."""
        if self.hold is None:
            return False
        try:
            age = time.time() - self.hold.stat().st_mtime
            until = hold_until(self.hold.read_text())
        except OSError:
            return False
        if until is None:
            return age < self.hold_s
        return age < HOLD_MAX_S and time.time() < until

    def pass_once(self, force=False):
        """One look: rebuild whatever is behind. Returns the names it repaired. Never raises."""
        if not force and (self.editing() or self.held()):
            return []
        if not self._busy.acquire(blocking=False):
            return []
        self.state["rebuilding"] = True
        try:
            late = [n if isinstance(n, str) else n[0] for n in self.stale()]
            self.state["checked"] = _dt.datetime.now().astimezone().isoformat()
            if not late and not force:
                self.state["last"], self.state["error"] = [], None
                return []
            self.repair(sorted(set(late)))
            self.state["last"], self.state["error"] = sorted(set(late)), None
            return self.state["last"]
        except Exception as e:                    # a watcher must never take the server down
            self.state["error"] = f"{type(e).__name__}: {e}"
            return []
        finally:
            self.state["rebuilding"] = False
            self._busy.release()

    def start(self):
        """Watch in a daemon thread, every `every` seconds."""
        def loop():
            while True:
                self.pass_once()
                time.sleep(self.every)
        t = threading.Thread(target=loop, daemon=True)
        t.start()
        return t

    def status(self):
        """What `/api/fresh` reports: what is behind, and what the watcher is doing about it."""
        try:
            late = list(self.stale())
        except Exception as e:
            late, self.state["error"] = [], f"{type(e).__name__}: {e}"
        rows = [{"sheet": n, "minutes": None} if isinstance(n, str) else {"sheet": n[0], "minutes": n[1]}
                for n in late]
        return {"stale": rows, "up_to_date": not late, "rebuilding": self.state["rebuilding"],
                "checked": self.state["checked"], "last_rebuilt": self.state["last"],
                "error": self.state["error"], "held": self.held(), "editing": self.editing()}


def run_step(code, what, cwd=None, timeout=2400, prefix=None, env=None, log=print):
    """Run `code` in a fresh Python interpreter, in its own process group, and say if it failed.

    `prefix` is a command to run it under (a job queue or a memory cap), as a list. On a timeout
    the whole process group is ended, so nothing it started is left running. The last lines of its
    output are passed to `log`. Raises RuntimeError when the step exits non-zero.
    """
    cmd = list(prefix or []) + [sys.executable, "-c", code]
    c = subprocess.Popen(cmd, cwd=str(cwd) if cwd else None, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, start_new_session=True,
                         env={**os.environ, **env} if env else None)
    _STEPS.add(c)
    try:
        out, err = c.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(c.pid, signal.SIGKILL)
        except OSError:
            c.kill()
        out, err = c.communicate()
    finally:
        _STEPS.discard(c)
    for line in (out or "").strip().splitlines()[-3:] + (err or "").strip().splitlines()[-4:]:
        log(f"    | {line}")
    if c.returncode != 0:
        raise RuntimeError(f"{what} failed in its own process (exit {c.returncode})")
    return c.returncode


def stop_steps():
    """End every step `run_step` started that is still running, with everything it started."""
    for c in list(_STEPS):
        try:
            os.killpg(c.pid, signal.SIGTERM)
        except OSError:
            pass


def _exiting():
    _STOPPING[0] = True


def stop_on_signals(signals=(signal.SIGTERM, signal.SIGINT)):
    """End the program on SIGTERM or SIGINT, and end every step `run_step` started with it.

    The first signal stops the steps and raises SystemExit(0), so the program's own cleanup runs.
    A signal that comes after that, or while the program is already exiting, only stops the steps.
    SystemExit raised then lands in an exit-time callback, where Python prints it as a traceback
    and changes nothing. This happens when whatever stops the server sends its signal twice, or
    sends it to the whole session as it shuts down. Returns the handler.
    """
    import logging  # noqa: F401  its exit-time shutdown must be registered before ours, so ours runs first
    atexit.register(_exiting)

    def handler(*_a):
        stop_steps()
        if _STOPPING[0]:
            return
        _STOPPING[0] = True
        raise SystemExit(0)

    for s_ in signals:
        signal.signal(s_, handler)
    return handler
