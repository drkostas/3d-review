"""Know whether a picture or a stage file shows the current model, by what made it, not by when.

A picture is current when it was made from exactly the code, and the subject, that would make it
now. Modification times answer a different question: a revert copied back from a backup, a touch
or a save without changes all move the clock and leave the content as it was, and a file copied
back with its old time hides a real change. So every check here is a content hash, and the clock
is used only for a picture that was never stamped.

- `import_closure(root, folder, packages)` finds the source files a module reaches by import,
  read from their text (a lazy import inside a function is still found).
- `fingerprint(files, extra)` hashes them in an order that depends only on names and contents.
- `Stamps(path)` records which fingerprint made each picture. Take the fingerprint before
  drawing and stamp it after, so an edit made during a render leaves the picture stale.
- `verdict(matched, minutes)` gives the answer, with the content check outranking the clock both
  ways, and `Stale` carries it with the reason.
- `lock(path)` lets one rebuild run at a time across processes, and frees itself when its holder
  has died.

    sources = import_closure("build_stages", "project/", packages={"mylib": mylib_dir})
    fp = fingerprint(sources)
    ... draw the picture ...
    Stamps("out/.fingerprints.json").put("front-view", fp)
"""
import contextlib
import hashlib
import json
import os
import pathlib
import re

#: the `why` of a picture or stage that should exist and does not
MISSING = "not on disk"


def _module_file(base, dotted):
    """The file for `dotted` (a.b.c) under a package folder `base` that holds package `a`."""
    parts = dotted.split(".")[1:]
    here = pathlib.Path(base).joinpath(*parts) if parts else pathlib.Path(base)
    for f in (here.with_suffix(".py"), here / "__init__.py"):
        if f.exists():
            return f
    return None


def import_closure(root, folder, packages=None, skip=(), resolve=None):
    """Every source file that module `root` (a file `root.py` in `folder`) reaches by import.

    The files are read, not run, so an import written inside a function is found too. Imports of
    modules in `folder` are followed, and so are imports of the packages named in `packages`
    ({top-level name: the package's folder}), including relative imports inside them. Anything else
    (the standard library, numpy) is the edge and is not followed. Modules named in `skip` are not
    walked: a module that only reports on a picture must not date it, or editing the report
    changes the verdict on everything it reports about. `resolve(dotted)` may return the dotted
    name of the module that one really runs, for a re-export module that hands over to another
    (`ball = import_module("mylib.shapes").ball`), and the walk follows that module in its place.
    Returns the files, sorted.
    """
    folder = pathlib.Path(folder)
    packages = {k: pathlib.Path(v) for k, v in (packages or {}).items()}
    names = "|".join(re.escape(p) for p in packages) or r"(?!)"
    plain = re.compile(r"^\s*(?:from\s+(\w+)\s+import|import\s+(\w+))", re.M)
    from_pkg = re.compile(r"^\s*from\s+(" + names + r")(?:\.([\w.]+))?\s+import\s+(\([^)]*\)|[^\n#]+)", re.M)
    import_pkg = re.compile(r"^\s*import\s+((?:" + names + r")(?:\.[\w.]+)?)", re.M)
    relative = re.compile(r"^\s*from\s+(\.+)([\w.]*)\s+import\s+(\([^)]*\)|[^\n#]+)", re.M)

    def file_of(name):
        top = name.split(".")[0]
        if top in packages:
            return _module_file(packages[top], name)
        f = folder / f"{name}.py"
        return f if f.exists() else None

    def imported_names(text):
        return [n.strip().split(" as ")[0].strip()
                for n in text.replace("(", " ").replace(")", " ").replace("\n", " ").split(",")]

    seen, queue, out = set(skip) - {root}, [root], set()

    def with_parents(dotted):
        # importing a.b.c runs a/__init__.py and a/b/__init__.py first, so they count too
        bits = dotted.split(".")
        return [".".join(bits[:i]) for i in range(1, len(bits) + 1)]

    while queue:
        name = queue.pop()
        if name in seen:
            continue
        seen.add(name)
        target = resolve(name) if resolve is not None else None
        if target and target != name:
            queue.extend(with_parents(target))
            continue
        f = file_of(name)
        if f is None:
            continue
        out.add(f)
        try:
            text = f.read_text()
        except OSError:
            continue
        for a, b in plain.findall(text):
            nxt = a or b
            if nxt and nxt not in packages:
                queue.append(nxt)
        for dotted in import_pkg.findall(text):
            queue.extend(with_parents(dotted))
        for pkg, sub, imported in from_pkg.findall(text):
            base = f"{pkg}.{sub}" if sub else pkg
            queue.extend(with_parents(base))
            queue.extend(f"{base}.{n}" for n in imported_names(imported) if n)
        if name.split(".")[0] in packages:
            # relative imports inside a package: from .x import y, from .. import z
            here = name.split(".") if f.name == "__init__.py" else name.split(".")[:-1]
            for dots, sub, imported in relative.findall(text):
                up = here[: len(here) - (len(dots) - 1)] if len(dots) > 1 else here
                base = ".".join(up + ([sub] if sub else []))
                queue.append(base)
                queue.extend(f"{base}.{n}" for n in imported_names(imported) if n)
    return sorted(out)


def _ordered(files):
    """The files in an order that depends only on their names and their contents.

    Sorting by bare name leaves two files of one name (a module and a wrapper of it) in set order,
    which Python changes per process. Sorting by full path makes the answer depend on where the
    folder is, so moving a project rebuilt everything. A tie on the name is broken by the content.
    """
    def key(q):
        try:
            return (q.name, hashlib.sha256(q.read_bytes()).hexdigest())
        except OSError:
            return (q.name, "")
    return sorted({pathlib.Path(f) for f in files}, key=key)


def fingerprint(files, extra=()):
    """sha256 over the files' names and contents, then any raw bytes in `extra` (a stage file a
    picture is drawn from, for example). None when a file cannot be read."""
    h = hashlib.sha256()
    for q in _ordered(files):
        try:
            h.update(q.name.encode())
            h.update(q.read_bytes())
        except OSError:
            return None
    for b in extra:
        h.update(b)
    return h.hexdigest()


class Stamps:
    """Which fingerprint made each picture, kept in one JSON file: {name: fingerprint}."""

    def __init__(self, path):
        self.path = pathlib.Path(path)

    def _read(self):
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}

    def get(self, name):
        return self._read().get(name)

    def put(self, name, fp):
        """Record `fp` for `name`. Take `fp` before the picture is drawn, not after it."""
        if not fp:
            return
        d = self._read()
        d[name] = fp
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(d, indent=1, sort_keys=True))
        tmp.replace(self.path)

    def matches(self, name, fp):
        """True when `name` was made from `fp`, False when from something else, None when unknown."""
        if not fp:
            return None
        got = self.get(name)
        return None if got is None else got == fp


class Stale(tuple):
    """`(name, minutes)` to anything that unpacks it, with the reason as `.why`."""

    def __new__(cls, name, minutes, why):
        o = super().__new__(cls, (name, minutes))
        o.why = why
        return o


def verdict(matched, minutes):
    """One picture's answer: (why, minutes) when stale, None when current.

    `matched` is the content check (`Stamps.matches`) and `minutes` how much newer the newest input
    is than the picture. The content check outranks the clock in both directions: True means the
    picture was made from exactly this code, so no timestamp may call it stale, and False means it
    was not, so no timestamp may call it current. The clock decides only when nothing was stamped.
    """
    if matched is False:
        return ("the code that drew it has changed", max(0.0, minutes))
    if matched is True:
        return None
    return ("older than the code that draws it", minutes) if minutes > 0 else None


class StaleInput(RuntimeError):
    """A saved input (a pose, a cache, a measured file) was made by code that has changed since."""


def require_current(stamps, name, fp, strict=True, warn=print):
    """Use a saved input only if it was made by the code that would make it now.

    A check that reads a saved file instead of building it (a saved pose, a cached part) answers
    about the figure the file was made from. When the code moved on, that figure is not the one
    being built, and a check that passes on the wrong figure is worse than one that fails. Stamp the
    file with `stamps.put(name, fp)` when it is written, and call this before reading it. Raises
    `StaleInput` when it was made by other code (with `strict`, the default) and warns when it was
    never stamped. Returns True when the input is current.
    """
    m = stamps.matches(name, fp)
    if m is True:
        return True
    if m is False:
        msg = f"{name} was made by code that has changed since; build it again before reading it"
        if strict:
            raise StaleInput(msg)
    else:
        msg = f"{name} was never stamped, so it may be older than the code that reads it"
    warn(msg)
    return False


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


@contextlib.contextmanager
def lock(path):
    """Hold `path` as the one rebuild running: yields True when held, False when another live
    process holds it. The holder's pid is in the file, so a lock left by a killed process is
    noticed and taken over. It is reentrant within a process, so a rebuild that calls another
    rebuild step does not wait on itself."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    me = os.getpid()
    try:
        other = int(path.read_text().strip() or 0)
    except (OSError, ValueError):
        other = 0
    if other == me:
        yield True
        return
    if other and _alive(other):
        yield False
        return
    path.write_text(str(me))
    try:
        yield True
    finally:
        try:
            if path.read_text().strip() == str(me):
                path.unlink()
        except OSError:
            pass
