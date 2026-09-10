import sys
from pathlib import Path


def assert_owner_mode(path: Path) -> None:
    """chmod 0600 on Unix. Windows has no POSIX mode bits."""
    assert path.exists()
    if sys.platform == "win32":
        return
    assert path.stat().st_mode & 0o777 == 0o600
