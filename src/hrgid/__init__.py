"""HRgid — ИИ-помощник HR."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path


def _windows_libpq_fallback() -> None:
    """На Windows с политикой блокировки неподписанных DLL libpq из psycopg-binary не грузится.

    Тогда берём libpq из встроенного pgserver и включаем чисто-питоновую реализацию psycopg.
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
        ctypes.WinDLL(str(bin_dir / "libpq.dll"))


_windows_libpq_fallback()
