from pathlib import Path

import pytest

from mail_merger.desktop import cleanup_upload_cache, migrate_legacy_data
from mail_merger.storage import Store


def test_default_documents_import_keeps_old_files_and_references(tmp_path, monkeypatch, campaign):
    local = tmp_path / "local"
    legacy = local / "Mail-Merger"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    old = Store(legacy)
    old.save(campaign)
    old.close()
    attachments = legacy / "attachments" / campaign.id
    attachments.mkdir(parents=True)
    original = attachments / "old-file"
    original.write_bytes(b"original")
    root = tmp_path / "Documents" / "Mail-Merger"
    migrate_legacy_data(root)
    fresh = Store(root)
    try:
        assert fresh.load(campaign.id).name == campaign.name
        assert original.read_bytes() == b"original"
        assert not (root / "attachments").exists()
        fresh.load(campaign.id)
        migrate_legacy_data(root)  # Existing history is not overwritten.
    finally:
        fresh.close()


def test_migration_refuses_an_active_old_instance(tmp_path, monkeypatch, campaign):
    from filelock import FileLock
    local = tmp_path / "local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    old = Store(local / "Mail-Merger")
    old.save(campaign)
    old.close()
    with FileLock(local / "Mail-Merger" / "desktop.lock"):
        with pytest.raises(ValueError, match="Close the older"):
            migrate_legacy_data(tmp_path / "Documents" / "Mail-Merger")


def test_cache_cleanup_keeps_originals_and_history(tmp_path):
    root = tmp_path / "data"
    cache = root / "upload-cache" / "hash"
    cache.mkdir(parents=True)
    cached = cache / "people.csv"
    cached.write_text("Name,Email\n")
    original = tmp_path / "people.csv"
    original.write_text("private original")
    history = root / "history.json"
    history.write_text("keep")
    cleanup_upload_cache(root)
    assert not cached.exists()
    assert original.read_text() == "private original"
    assert history.read_text() == "keep"


def test_history_log_pages_expose_all_rows_with_bounded_pages(store, campaign):
    for i in range(701):
        store.log(campaign.id, "", f"entry-{i}")
    seen = []
    for page in range(1, 4):
        rows, number, pages, total = store.log_page(campaign.id, page)
        assert pages == 3 and total == 701 and len(rows) <= 250
        seen += [row[3] for row in rows]
    assert len(seen) == len(set(seen)) == 701
    assert "entry-0" in seen and "entry-700" in seen
