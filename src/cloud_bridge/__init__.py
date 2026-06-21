"""Local-first multi-provider cloud execution bridge for Symphony workers."""

# Single source of truth for the version. pyproject.toml reads this via
# [tool.setuptools.dynamic] version = {attr = "cloud_bridge.__version__"};
# the CLI imports it for `cloud-bridge --version`. Bump here only.
__version__ = "0.1.0"
