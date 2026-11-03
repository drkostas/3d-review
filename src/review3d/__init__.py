"""Review a 3D design with an assistant."""
from importlib.metadata import PackageNotFoundError, version as _version

try:
    __version__ = _version("3d-review")  # the one place the version is written is pyproject.toml
except PackageNotFoundError:  # a source tree that was never installed
    __version__ = "0+unknown"
del _version, PackageNotFoundError
