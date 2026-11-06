import json
from pathlib import Path

import pytest

from mail_merger.attachments import files_for
from mail_merger.models import Campaign, Recipient
from mail_merger.storage import Store


def test_attachments_are_paths_and_not_buckets(application, tmp_path):
    original = tmp_path / "notes.pdf"
    original.write_bytes(b"test pdf")
    application.upload_attachments([str(original)])
    attachment = application.campaign.attachment_library[0]
    assert Path(attachment.path) == original.resolve()
    assert not (application.store.root / "attachments").exists()
    saved = json.loads(application.store._record_path(application.campaign.id).read_text("utf-8"))
    assert saved["attachment_library"][0]["path"] == str(original.resolve())
    original.unlink()
    application.assign_attachments([attachment.digest], True, [], [])
    assert any("Missing attachment" in error for error in application.review()[0])


def test_compact_json_preserves_templates_not_source_or_resolved_messages(application):
    c = application.campaign
    c.source_rows = [r.values.copy() for r in c.recipients]
    c.source = "private-source.xlsx"
    application.store.save(c)
    data = json.loads(application.store._record_path(c.id).read_text("utf-8"))
    assert "source_rows" not in data and "source" not in data
    assert data["subject"] == "Hello ${Name}"
    assert "Hello Ada" not in json.dumps(data)
    assert len(data["recipients"]) == 2
    assert application.store.load(c.id).recipients[0].values["Name"] == "Ada"
    with application.store.connect() as db:
        assert db.execute("SELECT data FROM campaigns WHERE id=?", (c.id,)).fetchone() == ("",)


def test_reuse_keeps_templates_and_mappings_but_requires_new_source(application, tmp_path):
    c = application.campaign
    c.uid_column = "Name"
    c.fields = ["Name"]
    old_id, old_subject, old_body = c.id, c.subject, c.body
    application.store.save(c)
    application.reuse()
    fresh = application.campaign
    assert fresh.id != old_id and fresh.parent_id == old_id
    assert fresh.subject == old_subject and fresh.body == old_body
    assert fresh.uid_column == "Name" and fresh.fields == ["Name"]
    assert not fresh.recipients and fresh.requires_source and not fresh.run_id
    assert not fresh.attachment_library
    assert application.store.load(old_id).recipients
    path = tmp_path / "fresh.csv"
    path.write_text("Name,Email,Department\nNew,new@example.com,Math\n")
    application.discover(str(path), "file")
    application.stage_rows("CSV")
    application.build_recipients("Name", "Email")
    assert application.campaign.fields == ["Name"]
    assert application.campaign.recipients[0].values["Name"] == "New"
    assert not application.campaign.requires_source


def test_retry_preserves_history_and_requires_unknown_confirmation(application):
    c = application.campaign
    c.recipients.append(Recipient("3", {"Email": "third@example.com", "Name": "Third"}))
    application.store.save(c)
    run = application.store.create_run(c, ["1", "2", "3"])
    application.store.set_delivery(run, "1", "accepted")
    application.store.set_delivery(run, "2", "failed", "Rejected")
    application.store.set_delivery(run, "3", "unknown", "Connection interrupted")
    parent = c.id
    with pytest.raises(ValueError, match="Accepted"):
        application.retry_draft(["1"])
    with pytest.raises(ValueError, match="Sent mail"):
        application.retry_draft(["3"])
    application.retry_draft(["2", "3"], acknowledge_unknown=True)
    retry = application.campaign
    assert retry.parent_id == parent and retry.run_id is None
    assert {r.id for r in retry.recipients} == {"2", "3"}
    assert retry.approved_revision is None
    assert application.store.counts(run) == {"accepted": 1, "failed": 1, "unknown": 1}
    application.switch(parent)
    with pytest.raises(ValueError, match="already exists"):
        application.retry_draft(["2"])
    assert application.store.retry_claims(parent) == {"2": retry.id, "3": retry.id}


def test_retry_corrects_invalid_primary_without_editing_original(application):
    c = application.campaign
    c.recipients[0].values["Email"] = "invalid"
    c.recipients[0].selected = False
    application.store.save(c)
    original = c.id
    application.retry_draft(["1"])
    application.correct_address("1", "correct@example.com")
    assert application.campaign.recipients[0].values["Email"] == "correct@example.com"
    assert application.store.load(original).recipients[0].values["Email"] == "invalid"


def test_dashboard_counts_outcomes_and_drafts(application):
    c = application.campaign
    run = application.store.create_run(c, ["1", "2"])
    application.store.set_delivery(run, "1", "accepted")
    application.store.set_delivery(run, "2", "failed")
    application.reuse()
    dashboard = {row["id"]: row for row in application.store.dashboard()}
    assert dashboard[c.id]["accepted"] == 1 and dashboard[c.id]["failed"] == 1
    assert dashboard[application.campaign.id]["state"] == "Draft"


def test_old_database_json_migrates_without_duplicate_source(tmp_path, campaign):
    import sqlite3
    root = tmp_path / "old"
    old = Store(root)
    old.save(campaign)
    with old.connect() as db:
        db.execute("UPDATE campaigns SET data=? WHERE id=?", (json.dumps(campaign.to_dict()), campaign.id))
    old._record_path(campaign.id).unlink()
    old.close()
    fresh = Store(root)
    try:
        assert fresh.load(campaign.id).subject == campaign.subject
        assert fresh._record_path(campaign.id).is_file()
        with fresh.connect() as db:
            assert db.execute("SELECT data FROM campaigns").fetchone() == ("",)
    finally:
        fresh.close()
