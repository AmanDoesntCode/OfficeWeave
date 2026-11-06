import json

import pytest

from mail_merger.spreadsheets import grouped_recipients
from mail_merger.templates import prepare, plain_text, render, sanitize


HEADERS = ['UID', 'Email', 'Name', 'Subject']


def professor_rows():
    return [{'UID': '0007', 'Email': 'ada@example.com', 'Name': 'Professor Ada', 'Subject': subject}
            for subject in ['Algebra', 'Calculus', 'Statistics', 'Geometry', 'Algebra']]


def test_one_professor_collapses_only_email_and_keeps_all_source_values():
    recipients = grouped_recipients(professor_rows(), HEADERS, 'UID', 'Email')
    assert len(recipients) == 1
    professor = recipients[0]
    assert professor.source_count == 5 and professor.selected and not professor.issues
    assert professor.values['Email'] == 'ada@example.com'
    assert professor.values['Name'] == '\n'.join(['Professor Ada'] * 5)
    assert professor.values['UID'] == '\n'.join(['0007'] * 5)
    assert professor.uid_value == '0007'
    assert professor.values['Subject'] == 'Algebra\nCalculus\nStatistics\nGeometry\nAlgebra'
    body = render('<p>Dear ${Name},</p><p>Please submit the question papers for:</p><p>${Subject}</p>',
                  professor.values, html=True)
    assert body.count('Professor Ada') == 5
    assert 'Algebra<br>Calculus<br>Statistics<br>Geometry<br>Algebra' in body
    assert plain_text(body).count('Algebra') == 2


def test_uid_comparison_trims_but_keeps_case_and_leading_zeros():
    rows = professor_rows()[:1]
    rows += [dict(rows[0], UID=' 0007 ', Subject='Calculus'), dict(rows[0], UID='7'), dict(rows[0], UID='A'), dict(rows[0], UID='a')]
    assert len(grouped_recipients(rows, HEADERS, 'UID', 'Email')) == 4


def test_repeated_paper_titles_stay_paired_with_codes_in_review_and_history(application):
    c = application.campaign
    c.headers = ['UID', 'Email', 'Name', 'Paper Code', 'Paper Name']
    c.fields = c.headers.copy()
    c.uid_column, c.email_column = 'UID', 'Email'
    pairs = [('AI26509', 'Machine Learning'),
             ('CS24303', 'Data Mining Concepts and Techniques'),
             ('IT335', 'Data Mining Concepts and Techniques')]
    rows = [dict(zip(c.headers, ['09013', 'professor@example.com', 'Professor', code, title]))
            for code, title in pairs]
    c.recipients = grouped_recipients(rows, c.headers, 'UID', 'Email')
    c.body = '<ol><li><b>${Paper Code}</b>: ${Paper Name}</li></ol>'
    application.changed()
    assert application.review() == ([], [])
    original = c.id
    application.switch(original)
    recipient = application.campaign.recipients[0]
    assert recipient.uid_value == '09013'
    assert recipient.values['Name'] == 'Professor\nProfessor\nProfessor'
    assert recipient.values['Email'] == 'professor@example.com'
    assert list(zip(recipient.values['Paper Code'].split('\n'), recipient.values['Paper Name'].split('\n'))) == pairs
    assert application.review() == ([], [])
    mail = prepare(application.campaign, recipient)
    assert mail.to == 'professor@example.com'
    assert mail.html.count('<li>') == 3
    for code, title in pairs:
        assert f'<li><b>{code}</b>: {title}</li>' in mail.html


def test_grouped_blank_cells_keep_their_row_positions_and_require_acknowledgement(application):
    c = application.campaign
    c.headers = ['UID', 'Email', 'Code', 'Title']
    c.fields = c.headers.copy()
    c.uid_column, c.email_column = 'UID', 'Email'
    c.subject = 'Papers'
    c.body = '<ol><li>${Code}: ${Title}</li></ol>'
    rows = [dict(zip(c.headers, ['1', 'one@example.com', code, title]))
            for code, title in [('A', 'Same'), ('B', ''), ('C', 'Same')]]
    c.recipients = grouped_recipients(rows, c.headers, 'UID', 'Email')
    application.changed()
    assert c.recipients[0].values['Title'] == 'Same\n\nSame'
    errors, blanks = application.review()
    assert not errors and blanks == ['Row 1: Title']
    with pytest.raises(ValueError, match='blank merge values'):
        application.approve(['recipients', 'fields', 'content', 'attachments'], False)
    mail = prepare(c, c.recipients[0])
    assert '<li>A: Same</li><li>B: </li><li>C: Same</li>' in mail.html


def test_missing_uid_and_unselected_uid_are_rejected():
    with pytest.raises(ValueError, match='unique ID column'):
        grouped_recipients(professor_rows(), HEADERS, '', 'Email')
    with pytest.raises(ValueError, match='row 2'):
        grouped_recipients([dict(professor_rows()[0], UID=' ')], HEADERS, 'UID', 'Email')


@pytest.mark.parametrize('email', ['other@example.com', 'invalid', ''])
def test_conflicting_or_invalid_email_blocks_submission(campaign, email):
    rows = professor_rows()[:2]
    rows[1]['Email'] = email
    recipient = grouped_recipients(rows, HEADERS, 'UID', 'Email')[0]
    assert not recipient.selected and recipient.issues
    with pytest.raises(ValueError, match='email'):
        prepare(campaign, recipient)


def test_email_case_differences_do_not_create_a_conflict():
    rows = professor_rows()[:2]
    rows[1]['Email'] = 'ADA@EXAMPLE.COM'
    recipients = grouped_recipients(rows, HEADERS, 'UID', 'Email')
    assert recipients[0].values['Email'] == 'ada@example.com'
    assert recipients[0].selected and not recipients[0].issues


def test_staged_import_requires_uid_then_persists_grouped_snapshot(application, tmp_path):
    path = tmp_path / 'professors.csv'
    path.write_text('UID,Email,Name,Subject\n0007,ada@example.com,Professor Ada,Algebra\n0007,ada@example.com,Professor Ada,Calculus\n')
    application.discover(str(path), 'file')
    original = application.campaign.to_dict()
    application.stage_rows('CSV')
    assert application.campaign.to_dict() == original
    with pytest.raises(ValueError, match='unique ID'):
        application.build_recipients('', 'Email')
    application.build_recipients('UID', 'Email')
    saved = application.store.load(application.campaign.id)
    assert len(saved.recipients) == 1
    assert saved.uid_column == 'UID' and saved.source_rows == []
    assert saved.recipients[0].source_count == 2
    assert saved.recipients[0].values['Subject'] == 'Algebra\nCalculus'


def test_colored_formatting_survives_render_and_provider_payload(campaign):
    campaign.body = '<p style="text-align:center"><span style="color:#cc0000;background-color:#fff2cc;font-family:Georgia;font-size:24px">Hello ${Name}</span></p>'
    campaign.provider = 'outlook'
    mail = prepare(campaign, campaign.recipients[0])
    assert 'color:#cc0000' in mail.html and 'font-family:Georgia' in mail.html
    assert 'background-color:#fff2cc' in json.loads(mail.payload)['message']['body']['content']


def test_formatting_cannot_introduce_scripts_tracking_or_layout_overlays():
    value = sanitize('<p style="color:red;position:fixed;inset:0;background-image:url(https://evil.example);font-size:99999px" onclick="alert(1)">Safe</p>')
    assert 'color:red' in value
    assert not any(word in value for word in ('position', 'inset', 'url(', 'onclick', '99999'))


def test_grouped_fields_in_subject_use_a_single_line(campaign):
    campaign.subject = 'Papers: ${Subject} for ${Name}'
    recipient = grouped_recipients(professor_rows(), HEADERS, 'UID', 'Email')[0]
    mail = prepare(campaign, recipient)
    assert mail.subject == 'Papers: Algebra, Calculus, Statistics, Geometry, Algebra for ' + ', '.join(['Professor Ada'] * 5)


def test_finished_campaign_has_closure_and_continuing_preserves_history(application):
    app = application
    old = app.campaign.id
    run = app.store.create_run(app.campaign, ['1', '2'])
    app.store.set_delivery(run, '1', 'accepted')
    app.store.set_delivery(run, '2', 'accepted')
    app.switch(old)
    assert app.run_summary()['title'] == 'Campaign complete'
    app.new(duplicate=True)
    assert app.campaign.run_id is None and app.campaign.id != old
    app.save_content('Follow up', 'Follow up ${Name}', app.campaign.body, '', '', 'gmail')
    assert app.store.load(old).run_id == run
    assert app.store.counts(run) == {'accepted': 2}


@pytest.mark.parametrize('state,title', [('pending','Campaign paused'), ('unknown','Campaign finished with issues'),
                                       ('failed','Campaign finished with issues'), ('cancelled','Campaign closed')])
def test_reloaded_campaign_has_an_accurate_outcome(application, state, title):
    run = application.store.create_run(application.campaign, ['1'])
    application.store.set_delivery(run, '1', state)
    application.switch(application.campaign.id)
    assert application.run_summary()['title'] == title
