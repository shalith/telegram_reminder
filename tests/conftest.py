from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from app.db import init_db, get_session


@pytest.fixture()
def session():
    tmpdir = Path(tempfile.mkdtemp())
    db_path = tmpdir / "test.db"
    init_db(f"sqlite:///{db_path}")
    session = get_session()
    try:
        yield session
    finally:
        session.close()
