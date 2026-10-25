from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.database.repository import Repository
from app.services.archive import ArchiveService

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def now() -> datetime:
    return NOW


@pytest.fixture
def repo(tmp_path: Path) -> Repository:
    repository = Repository(tmp_path / "capsule.db")
    repository.migrate()
    repository.migrate()
    return repository


@pytest.fixture
def archive(repo: Repository, tmp_path: Path) -> ArchiveService:
    storage = tmp_path / "storage"
    storage.mkdir()
    return ArchiveService(repo, storage)
