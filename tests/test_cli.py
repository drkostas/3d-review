"""The 3d-review command."""
import json
import socket
import subprocess
import sys
import time
import urllib.request

import pytest
import trimesh

from review3d import cli


def test_stages_command_writes_one_parts_stage(tmp_path):
    a = trimesh.creation.box((10, 10, 10))
    b = trimesh.creation.cylinder(radius=3, height=12)
    a.export(tmp_path / "cube.stl")
    b.export(tmp_path / "rod.stl")
    out = tmp_path / "model"
    assert cli.main(["stages", str(tmp_path / "cube.stl"), str(tmp_path / "rod.stl"),
                     "-o", str(out)]) == 0
    info = json.loads((out / "stages.json").read_text())
    assert list(info) == ["parts", "meta"]
    assert info["parts"]["pieces"] == 2
    sc = trimesh.load(out / "stages" / "parts.glb", force="scene")
    assert set(sc.geometry) == {"cube", "rod"}


def test_skill_command(tmp_path, monkeypatch, capsys):
    missing = tmp_path / "nothing" / "SKILL.md"
    monkeypatch.setattr(cli, "SKILL", missing)
    assert cli.main(["skill", "--dir", str(tmp_path / "skills")]) == 1
    assert "skill file is not in this installation" in capsys.readouterr().err
    src = tmp_path / "SKILL.md"
    src.write_text("---\nname: 3d-review\n---\n")
    monkeypatch.setattr(cli, "SKILL", src)
    assert cli.main(["skill", "--dir", str(tmp_path / "skills")]) == 0
    assert (tmp_path / "skills" / "3d-review" / "SKILL.md").read_text() == src.read_text()


def test_bench_command_serves_from_a_config(tmp_path):
    a = trimesh.creation.box((10, 10, 10))
    a.export(tmp_path / "cube.stl")
    assert cli.main(["stages", str(tmp_path / "cube.stl"), "-o", str(tmp_path)]) == 0
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    (tmp_path / "bench.toml").write_text('host = "127.0.0.1"\nport = 1\n')
    p = subprocess.Popen([sys.executable, "-m", "review3d.cli", "bench", "--config",
                          str(tmp_path / "bench.toml"), "--port", str(port)],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        body = None
        for _ in range(100):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/stages.json") as r:
                    body = json.loads(r.read())
                break
            except OSError:
                time.sleep(0.1)
        assert body is not None and body["parts"]["pieces"] == 1
    finally:
        p.terminate()
        p.wait(timeout=10)
