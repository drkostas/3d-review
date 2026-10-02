"""The `3d-review` command.

    3d-review bench [--config bench.toml] [--port N] [--host H]
    3d-review stages part1.stl part2.stl ... -o DIR
    3d-review skill [--dir ~/.claude/skills]

`bench` serves the review bench with the settings in the TOML file (see `review3d.bench.settings`)
and a `FileNotifier` that writes to `<subs_dir>/published/notifications.log`. `stages` writes one
stage called "parts" from mesh files into DIR. `skill` copies the assistant skill file that ships
with the package into a skills folder.
"""
import argparse
import pathlib
import shutil
import sys

SKILL = pathlib.Path(__file__).parent / "skill" / "SKILL.md"


def _bench(args):
    from review3d.bench import server, settings
    from review3d.bench.notify import FileNotifier

    cfg = settings.load(args.config)
    port = args.port if args.port is not None else cfg["port"]
    host = args.host if args.host is not None else cfg["host"]
    subs = pathlib.Path(cfg["subs_dir"])
    bench = server.Bench(cfg["model_dirs"], subs, cfg["stages_dir"],
                         notifier=FileNotifier(subs / "published" / "notifications.log"))
    shown = host or "localhost"
    print(f"review bench on http://{shown}:{port}/twist.html (threads at /threads.html)",
          flush=True)
    try:
        server.serve(bench, port, host)
    except KeyboardInterrupt:
        pass
    return 0


def _stages(args):
    from review3d import stages

    info = stages.write_stages(stages.stages_from_files(args.files), args.out)
    parts = info["parts"]
    print(f"wrote {args.out}/stages.json and {args.out}/{parts['file']} "
          f"({parts['pieces']} parts, {' x '.join(str(v) for v in parts['size_mm'])} mm)")
    return 0


def _skill(args):
    if not SKILL.is_file():
        print(f"error: the skill file is not in this installation (expected {SKILL})",
              file=sys.stderr)
        return 1
    dest = pathlib.Path(args.dir).expanduser() / "3d-review" / "SKILL.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SKILL, dest)
    print(f"copied the skill to {dest}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="3d-review",
                                 description="Review a 3D design with an assistant.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("bench", help="serve the review bench")
    b.add_argument("--config", default="bench.toml", help="settings file (default bench.toml)")
    b.add_argument("--port", type=int, default=None, help="port, overrides the settings file")
    b.add_argument("--host", default=None, help="address to listen on, overrides the settings file")
    b.set_defaults(fn=_bench)

    s = sub.add_parser("stages", help="write one stage called 'parts' from mesh files")
    s.add_argument("files", nargs="+", help="mesh files, one part each (STL, OBJ, PLY, GLB)")
    s.add_argument("-o", "--out", required=True, help="folder for stages.json and stages/")
    s.set_defaults(fn=_stages)

    k = sub.add_parser("skill", help="install the assistant skill file")
    k.add_argument("--dir", default="~/.claude/skills", help="skills folder (default ~/.claude/skills)")
    k.set_defaults(fn=_skill)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
