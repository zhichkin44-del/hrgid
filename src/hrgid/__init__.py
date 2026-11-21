"""HRgid - AI assistant for HR."""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path


def _windows_libpq_fallback() -> None:
    """On Windows with a policy that blocks unsigned DLLs, libpq from psycopg-binary does not load.

    In that case we take libpq from the bundled pgserver and enable the pure-Python implementation
    of psycopg.
    """
    if os.name != "nt" or "PSYCOPG_IMPL" in os.environ:
        return
    try:
        import psycopg_binary  # noqa: F401
        import psycopg_binary.pq  # type: ignore[import-not-found]  # noqa: F401

        return
    except ImportError:
        pass
    try:
        import pgserver
    except ImportError:
        return
    bin_dir = Path(pgserver.__file__).parent / "pginstall" / "bin"
    if (bin_dir / "libpq.dll").exists():
        os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
        os.environ["PSYCOPG_IMPL"] = "python"
        if sys.platform == "win32":  # for mypy on Linux: WinDLL exists only on Windows
            ctypes.WinDLL(str(bin_dir / "libpq.dll"))


_windows_libpq_fallback()
