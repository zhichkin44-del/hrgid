from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest

from norma.db import connect, database_url, init_schema


@pytest.fixture(scope="session")
def pg_url(tmp_path_factory: pytest.TempPathFactory) -> str:
    return database_url(tmp_path_factory.mktemp("pgdata"))


@pytest.fixture()
def conn(pg_url: str) -> Iterator[psycopg.Connection[Any]]:
    c = connect(pg_url)
    init_schema(c)
    c.execute("TRUNCATE chunks RESTART IDENTITY")
    yield c
    c.close()


@pytest.fixture()
def fixtures_dir() -> Path:
    return Path(__file__).parent / "fixtures"
