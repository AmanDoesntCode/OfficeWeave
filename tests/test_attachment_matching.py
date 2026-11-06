import json
from email import policy
from email.parser import BytesParser

import pytest

from mail_merger.attachments import files_for
from mail_merger.spreadsheets import grouped_recipients
from mail_merger.templates import prepare, render


def setup_subjects(app):
    c = app.campaign
    c.headers = ['UID', 'Email', 'Name', 'Subject', 'Department']
    c.fields = c.headers.copy()
    c.uid_column, c.email_column = 'UID', 'Email'
    c.source_rows = [dict(zip(c.headers, row)) for row in [
        ('1', 'both@example.com', 'Both', 'DA', 'Science'),
        ('1', 'both@example.com', 'Both', 'EE', 'Engineering'),
        ('2', 'da@example.com', 'DA only', 'DA', 'Science'),
        ('3', 'ee@example.com', 'EE only', 'EE', 'Engineering'),
    ]]
    c.recipients = grouped_recipients(c.source_rows, c.headers, 'UID', 'Email')
    c.body = '<p>Hello ${Name}</p><ol><li>${Subject}</li><li>Submit before Friday</li></ol>'
    app.changed()


def test_list_values_repeat_items_and_continue_numbering_with_formatting():
    body = render('<ol start="3"><li><b>${Subject}</b></li><li>Finish</li></ol>', {'Subject': 'DA\nEE\nCS'}, html=True)
    assert body.count('<li>') == 4
    assert '<li><b>DA</b></li><li><b>EE</b></li><li><b>CS</b></li>' in body
    assert '<li>Finish</li>' in body
    assert '<br>' not in body
    assert '<ol start="3">' in body


def test_bullet_list_and_nested_list_keep_structure_and_escape_values():
    body = render('<ol><li>Subjects<ul><li>${Subject}</li></ul></li><li>Done</li></ol>', {'Subject': '<DA>\nEE & CS'}, html=True)
    assert '<ul><li>&lt;DA&gt;</li><li>EE &amp; CS</li></ul>' in body
    assert body.count('<ol>') == 1


def test_single_upload_column_assignments_survive_uid_grouping_and_reload(application, tmp_path):
    app = application
    setup_subjects(app)
    paths = []
    for name in ('DA.pdf', 'EE.pdf'):
        path = tmp_path / name
        path.write_bytes(name.encode())
        paths.append(str(path))
    app.upload_attachments(paths)
    assert all(not files_for(app.campaign, r.id) for r in app.campaign.recipients)
    assert any('Assign or remove' in error for error in app.review()[0])
    da, ee = [file.digest for file in app.campaign.attachment_library]
    app.assign_attachments([da], False, ['Subject'], [['DA']])
    app.assign_attachments([ee], False, ['Subject'], [['EE']])
    assert [[file.name for file in files_for(app.campaign, r.id)] for r in app.campaign.recipients] == [
        ['DA.pdf', 'EE.pdf'], ['DA.pdf'], ['EE.pdf']]
    assert not app.campaign.shared
    assert app.review() == ([], [])
    for recipient, expected in zip(app.campaign.recipients, [['DA.pdf', 'EE.pdf'], ['DA.pdf'], ['EE.pdf']]):
        message = BytesParser(policy=policy.default).parsebytes(prepare(app.campaign, recipient).payload)
        assert [file.get_filename() for file in message.iter_attachments()] == expected
    original = app.campaign.id
    app.switch(original)
    assert [file.name for file in files_for(app.campaign, '2')] == ['DA.pdf']
    assert '2 source rows combined' in app.preview('1')[2]
    app.new(duplicate=True)
    assert [file.name for file in files_for(app.campaign, '3')] == ['EE.pdf']
    assert all(file.path == old.path for file, old in zip(app.campaign.attachment_library, app.store.load(original).attachment_library))


def test_multiple_columns_match_same_source_row_without_cross_product(application, tmp_path):
    setup_subjects(application)
    path = tmp_path / 'file.pdf'
    path.write_bytes(b'file')
    application.upload_attachments([str(path)])
    digest = application.campaign.attachment_library[0].digest
    with pytest.raises(ValueError, match='matching value group'):
        application.assign_attachments([digest], False, ['Subject', 'Department'], [['DA', 'Engineering']])
    application.assign_attachments([digest], False, ['Subject', 'Department'], [['EE', 'Engineering']])
    assert files_for(application.campaign, '1') and files_for(application.campaign, '3')
    assert not files_for(application.campaign, '2')
    application.remove_library_files([digest])
    assert not application.campaign.attachment_assignments


def test_everyone_requires_explicit_assignment_and_changes_invalidate_review(application, tmp_path):
    path = tmp_path / 'file.pdf'
    path.write_bytes(b'file')
    application.upload_attachments([str(path)])
    digest = application.campaign.attachment_library[0].digest
    with pytest.raises(ValueError):
        application.assign_attachments([digest], False, [], [])
    application.assign_attachments([digest], True, [], [])
    assert all(files_for(application.campaign, r.id) for r in application.campaign.recipients)
    application.review()
    application.assign_attachments([digest], False, ['Name'], [['Ada']])
    assert application.review_revision is None
    assert not files_for(application.campaign, '2')


def test_previous_shared_uploads_migrate_unassigned_when_draft_reopens(application, tmp_path):
    path = tmp_path / 'old.pdf'
    path.write_bytes(b'old shared file')
    application.add_files([str(path)])
    application.switch(application.campaign.id)
    assert not application.campaign.shared
    assert len(application.campaign.attachment_library) == 1
    assert not files_for(application.campaign, '1')
    assert any('Assign or remove' in error for error in application.review()[0])
