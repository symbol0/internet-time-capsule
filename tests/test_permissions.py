from datetime import timedelta
from pathlib import Path

import pytest

from app.errors import NotFoundError, UserInputError
from app.services.access import can_manage, is_user_allowed
from app.services.archive import delete_attachment
from app.services.export import export_payload, loads_payload, restore_payload
from config import normalize_token


def test_allowlist_and_ownership():
    assert is_user_allowed(5, frozenset()) is True
    assert is_user_allowed(5, frozenset({5})) is True
    assert is_user_allowed(6, frozenset({5})) is False
    assert can_manage(5, 5) is True
    assert can_manage(6, 5) is False


def test_token_colon_is_repaired_without_changing_a_real_token():
    repaired = normalize_token("1234567890" + "A" * 35)
    assert repaired == "1234567890:" + "A" * 35
    assert normalize_token(" 1234567890:" + "B" * 35 + " ") == "1234567890:" + "B" * 35


def test_users_cannot_read_or_change_each_others_notes(archive, repo, now):
    capsule = archive.save_text(1, "private idea #secret", now=now)
    assert repo.get(2, capsule.id) is None
    with pytest.raises(NotFoundError):
        archive.get_public(2, capsule.public_id)
    with pytest.raises(NotFoundError):
        archive.delete(2, capsule.id)
    with pytest.raises(NotFoundError):
        archive.seal(2, capsule.id, now + timedelta(days=3), now)
    assert archive.search(2, "private") == []
    assert archive.search(2, "#secret") == []
    assert archive.get_public(1, "TC-0001").text_content == "private idea #secret"
    delivery_id = repo.claim_delivery(1, capsule.id, "rediscovery", now)
    repo.mark_delivery(delivery_id, "sent", 9)
    assert repo.set_response(2, delivery_id, "useful", now) is False
    assert repo.set_response(1, delivery_id, "useful", now) is True


def test_import_rejects_a_different_owner_and_a_path_escape(archive, repo, now, tmp_path):
    archive.save_text(1, "mine", now=now)
    payload = export_payload(repo, 1, now)
    with pytest.raises(UserInputError):
        restore_payload(repo, 2, payload, now)
    assert archive.count(1) == 1
    stolen = dict(payload)
    stolen["capsules"] = [dict(payload["capsules"][0], file_path="../secret.txt")]
    with pytest.raises(UserInputError):
        loads_payload(__import__("json").dumps(stolen))

    secret = tmp_path / "secret.txt"
    secret.write_text("keep", encoding="utf-8")
    with pytest.raises(UserInputError):
        delete_attachment(archive.storage_root, "../secret.txt")
    with pytest.raises(UserInputError):
        delete_attachment(archive.storage_root, str(Path("/tmp/secret.txt")))
    assert secret.read_text(encoding="utf-8") == "keep"


def test_restore_does_not_touch_another_vault(archive, repo, now):
    archive.save_text(1, "owner", now=now)
    archive.save_text(2, "neighbor", now=now)
    payload = export_payload(repo, 1, now)
    archive.delete(1, archive.get_public(1, "TC-0001").id)
    restore_payload(repo, 1, payload, now)
    assert archive.get_public(2, "TC-0001").text_content == "neighbor"
    assert archive.get_public(1, "TC-0001").text_content == "owner"
