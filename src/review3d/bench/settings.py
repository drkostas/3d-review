"""Bench settings read from a small TOML file, so machine-specific values stay out of the code.

    port = 8766                 # the port the bench listens on
    host = ""                   # "" listens on every interface, "127.0.0.1" on this machine only,
                                # and ["127.0.0.1", "192.0.2.7"] on each address in the list
    model_dirs = ["."]          # searched first for stages.json, stages/*.glb and extra pages
    subs_dir = "submissions"    # where the threads are stored
    stages_dir = "stages"       # the stage files used to render reply pictures
    post_from = ["alice@example.com"]   # optional: only these Tailscale users may post
    post_devices = ["laptop"]           # optional: and only from these devices
    tailscale = "tailscale"             # the Tailscale command, for `tailscale whois`

Relative paths are resolved against the folder that holds the TOML file. A missing file gives the
defaults.
"""
import pathlib

try:
    import tomllib
except ImportError:  # Python 3.10
    import tomli as tomllib

DEFAULTS = {"port": 8766, "host": "", "model_dirs": ["."], "subs_dir": "submissions",
            "stages_dir": "stages", "post_from": None, "post_devices": None,
            "tailscale": "tailscale"}


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


def may_post(cfg):
    """The post check the settings ask for, or None when they name no `post_from`."""
    if not cfg.get("post_from"):
        return None
    from review3d.bench.access import tailnet_users
    return tailnet_users(cfg["post_from"], cfg.get("post_devices"), cli=cfg.get("tailscale") or "tailscale")
