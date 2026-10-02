"""Bench settings read from a small TOML file, so machine-specific values stay out of the code.

    port = 8766                 # the port the bench listens on
    host = ""                   # "" listens on every interface, "127.0.0.1" on this machine only
    model_dirs = ["."]          # searched first for stages.json, stages/*.glb and extra pages
    subs_dir = "submissions"    # where the threads are stored
    stages_dir = "stages"       # the stage files used to render reply pictures

Relative paths are resolved against the folder that holds the TOML file. A missing file gives the
defaults.
"""
import pathlib

try:
    import tomllib
except ImportError:  # Python 3.10
    import tomli as tomllib

DEFAULTS = {"port": 8766, "host": "", "model_dirs": ["."], "subs_dir": "submissions",
            "stages_dir": "stages"}


def load(path):
    path = pathlib.Path(path)
    got = dict(DEFAULTS)
    if path.exists():
        got.update(tomllib.loads(path.read_text()))
    base = path.parent
    got["model_dirs"] = [base / d for d in got["model_dirs"]]
    got["subs_dir"] = base / got["subs_dir"]
    got["stages_dir"] = base / got["stages_dir"]
    return got
