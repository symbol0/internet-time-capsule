from datetime import timedelta

import pytest

from app.database.repository import Repository
from app.errors import NotFoundError, UserInputError
from app.services.export import (
    backup_database,
    export_payload,
    restore_payload,
    rotate_daily_backups,
)


def test_save_survives_a_new_connection(archive, repo, now, tmp_path):
    saved = archive.save_text(7, "Build a tiny offline-first notes app.", now=now)
    assert saved.public_id == "TC-0001"
    assert saved.kind == "note"
    reopened = Repository(repo.path)
    reopened.migrate()
    again = reopened.get_by_public_id(7, "TC-0001")
    assert again is not None
    assert again.text_content == "Build a tiny offline-first notes app."
    assert again.created_at == now


def test_public_ids_are_per_user_and_keep_increasing(archive, now):
    first = archive.save_text(1, "one", now=now)
    second = archive.save_text(1, "two", now=now)
    other = archive.save_text(2, "theirs", now=now)
    assert (first.public_id, second.public_id, other.public_id) == (
        "TC-0001",
        "TC-0002",
        "TC-0001",
    )


def test_urls_tags_and_search(archive, now):
    link = archive.save_text(1, "https://example.com/notes", now=now)
    note = archive.save_text(
        1,
        "Build a tiny offline-first notes app. #Project",
        now=now + timedelta(minutes=1),
    )
    mixed = archive.save_text(1, "See https://example.com/other later", now=now)
    assert link.kind == "url"
    assert link.url == "https://example.com/notes"
    assert mixed.kind == "note"
    assert mixed.url == "https://example.com/other"
    assert note.tags == ("project",)
    found = archive.search(1, "offline")
    assert [item.public_id for item in found] == [note.public_id]
    by_id = archive.search(1, "TC-0002")
    assert by_id[0].id == note.id
    by_tag = archive.search(1, "#project")
    assert [item.id for item in by_tag] == [note.id]


def test_duplicate_forward_is_rejected(archive, now):
    archive.save_text(1, "forwarded thought", forwarded=True, source=(10, 4), now=now)
    with pytest.raises(UserInputError):
        archive.save_text(1, "forwarded thought", forwarded=True, source=(10, 4), now=now)


def test_delete_removes_the_record_the_search_hit_and_the_file(archive, repo, now):
    folder = archive.storage_root / "notes"
    folder.mkdir()
    attachment = folder / "idea.txt"
    attachment.write_text("bytes", encoding="utf-8")
    capsule = repo.insert_capsule(
        user_id=1,
        kind="note",
        text_content="notes about the file",
        url=None,
        created_at=now,
        tags=["project"],
        file_path="notes/idea.txt",
        now=now,
    )
    assert archive.search(1, "notes")
    removed = archive.delete(1, capsule.id)
    assert removed.public_id == capsule.public_id
    assert not attachment.exists()
    assert repo.get(1, capsule.id) is None
    assert archive.search(1, "notes") == []
    with pytest.raises(NotFoundError):
        archive.delete(1, capsule.id)


def test_export_restore_round_trip_and_backup(archive, repo, now, tmp_path):
    archive.save_text(1, "keep this #ideas", now=now)
    archive.update_settings(1, timezone="Asia/Dhaka", daily_limit=3, delivery_time="21:00")
    payload = export_payload(repo, 1, now)
    archive.delete(1, archive.get_public(1, "TC-0001").id)
    assert archive.count(1) == 0
    restored = restore_payload(repo, 1, payload, now)
    assert restored == 1
    capsule = archive.get_public(1, "TC-0001")
    assert capsule.text_content == "keep this #ideas"
    assert capsule.tags == ("ideas",)
    settings = archive.ensure_user(1, now)
    assert settings.timezone == "Asia/Dhaka"
    assert settings.daily_limit == 3
    follow = archive.save_text(1, "after restore", now=now)
    assert follow.public_id == "TC-0002"

    destination = tmp_path / "backups" / "manual.db"
    backup_database(repo.path, destination)
    copy = Repository(destination)
    assert copy.get_by_public_id(1, "TC-0002").text_content == "after restore"

    backup_dir = tmp_path / "rotation"
    backup_dir.mkdir()
    for day in range(1, 9):
        (backup_dir / f"capsule-2026-01-{day:02d}.db").write_text("old", encoding="utf-8")
    created = rotate_daily_backups(repo.path, backup_dir, now, keep=7)
    assert created is not None
    assert rotate_daily_backups(repo.path, backup_dir, now, keep=7) is None
    names = sorted(path.name for path in backup_dir.glob("capsule-*.db"))
    assert "capsule-2026-01-01.db" not in names
    assert "capsule-2026-01-02.db" not in names
    assert names[-1] == "capsule-2026-10-25.db"
    assert len(names) == 7


def test_handlers_build():
    from app.handlers import build_router

    assert build_router().name == "root"
