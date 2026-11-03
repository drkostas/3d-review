"""The version is written once, in pyproject.toml, and review3d.__version__ reads it back."""
import re
from pathlib import Path

import review3d

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def pyproject_version():
    text = PYPROJECT.read_text()
    try:
        import tomllib
    except ImportError:  # Python 3.10 and older have no tomllib
        return re.search(r'^version\s*=\s*"([^"]+)"', text, re.M).group(1)
    return tomllib.loads(text)["project"]["version"]


def test_version_matches_pyproject():
    want = pyproject_version()
    assert review3d.__version__ == want, (
        f"review3d.__version__ is {review3d.__version__!r} but pyproject.toml says {want!r}. "
        "An editable install keeps the version it was installed with, so run "
        "pip install -e . again after changing the version.")
