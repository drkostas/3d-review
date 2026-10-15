"""The bench names a turn's axis only when the turn is really about it.

The function is read out of the page itself and run under node, so the test exercises the code the
browser runs rather than a copy of it.
"""
import json
import pathlib
import re
import shutil
import subprocess

import pytest

PAGE = pathlib.Path(__file__).resolve().parents[1] / "src/review3d/bench/static/twist.html"


def _axis_name(*vectors):
    src = PAGE.read_text()
    const = re.search(r"const AXIS_NAME_DEG = [^;]+;", src).group(0)
    start = src.index("function axisName(ax) {")
    depth, i = 0, src.index("{", start)
    while True:
        depth += {"{": 1, "}": -1}.get(src[i], 0)
        i += 1
        if depth == 0:
            break
    fn = src[start:i]
    js = (
        "const THREE = {MathUtils: {radToDeg: r => r * 180 / Math.PI}};\n"
        "let ghostOf = null; const partFrame = () => null;\n"
        f"{const}\n{fn}\n"
        f"const out = {json.dumps([list(v) for v in vectors])}.map(([x, y, z]) => {{\n"
        "  const n = Math.hypot(x, y, z); return axisName({x: x / n, y: y / n, z: z / n}); });\n"
        "console.log(JSON.stringify(out));\n"
    )
    res = subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True)
    return json.loads(res.stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_axis_is_named_only_within_two_degrees():
    straight, off11, down, off1 = _axis_name(
        (0, 0, 1),
        (0.1931, 0.0428, 0.9803),      # 11.4 degrees off vertical, which the old 14 degree bar named "world z"
        (0, 0, -1),
        (0.02, 0.0, 1.0),              # 1.1 degrees off
    )
    assert straight == "world z"
    assert off11 == "a free axis, 11° from world z"
    assert down == "-world z"
    assert off1 == "world z"
