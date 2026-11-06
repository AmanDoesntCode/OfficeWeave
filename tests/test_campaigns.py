from pathlib import Path
import importlib

import pytest

from mail_merger.attachments import copy_attachment, files_for, verify_file
from mail_merger.models import AttachmentGroup, Campaign
from mail_merger.storage import Store
from mail_merger.ui import CHECKS


def test_snapshot_and_approval_round_trip(application):
    app = application
    assert app.review() == ([], [])
    app.approve(CHECKS, False)
    restored = app.store.load(app.campaign.id)
    assert restored.approved_revision == restored.revision
    app.save_content('Changed', 'Hi ${Name}', app.campaign.body, '', '', 'gmail')
    assert app.campaign.approved_revision is None
    with pytest.raises(ValueError, match='Check the campaign'):
        app.approve(CHECKS, False)


def test_storage_uses_one_serialized_connection_and_explicit_cleanup(store):
    import sqlite3
    with store.connect() as first:
        first.execute('SELECT 1').fetchone()
    with store.connect() as second:
        assert first is second
        assert second.execute('SELECT 1').fetchone() == (1,)
    store.close()
    with pytest.raises(sqlite3.ProgrammingError):
        first.execute('SELECT 1')


def test_blank_values_require_acknowledgement(application):
    app = application
    app.campaign.recipients[0].values['Name'] = ''
    errors, blanks = app.review()
    assert not errors and blanks
    with pytest.raises(ValueError, match='blank'):
        app.approve(CHECKS, False)
    app.approve(CHECKS, True)


def test_unconfirmed_fields_block_review(application):
    app = application
    app.campaign.fields = ['Email']
    assert any('Unconfirmed' in error for error in app.review()[0])


def test_attachments_are_referenced_deduplicated_and_checked(application, tmp_path):
    app = application
    path = tmp_path / 'file.txt'
    path.write_text('original')
    app.add_files([str(path)])
    app.add_files([str(path)], 'Team', ['1'])
    assert len(files_for(app.campaign, '1')) == 1
    assert len(files_for(app.campaign, '2')) == 1
    path.write_text('changed source')
    file = app.campaign.shared[0]
    assert Path(file.path) == path.resolve()
    with pytest.raises(ValueError, match='Attachment changed'):
        verify_file(file)
    Path(file.path).write_text('tampered')
    assert any('Attachment changed' in error for error in app.review()[0])


def test_group_files_only_reach_members(application, tmp_path):
    path = tmp_path / 'group.pdf'
    path.write_bytes(b'PDF')
    application.add_files([str(path)], 'Team', ['1'])
    assert files_for(application.campaign, '1')
    assert not files_for(application.campaign, '2')


def test_interrupted_submission_recovers_as_unknown(store, campaign):
    store.save(campaign)
    run = store.create_run(campaign, ['1', '2'])
    store.set_delivery(run, '1', 'submitting', attempt=True)
    restored = Store(store.root)
    assert restored.delivery(run, '1')['state'] == 'unknown'
    assert restored.delivery(run, '2')['state'] == 'pending'


def test_send_history_is_immutable_and_duplicate_is_independent(application):
    app = application
    app.store.create_run(app.campaign, ['1', '2'])
    with pytest.raises(ValueError, match='history'):
        app.save_content('Changed', '', '', '', '', 'gmail')
    old_id = app.campaign.id
    app.new(duplicate=True)
    assert app.campaign.id != old_id
    assert app.campaign.run_id is None
    assert app.campaign.approved_revision is None


def test_legacy_imports_have_no_network_or_environment_side_effects(monkeypatch):
    import socket
    def reject(*args, **kwargs):
        raise AssertionError('Network call during import')
    monkeypatch.setattr(socket, 'create_connection', reject)
    for module in ['app', 'automate_emails', 'read_glsheets', 'test_gmail_smtp']:
        importlib.import_module(module)
