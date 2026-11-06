import pytest

from mail_merger.grouping_analytics import analytics_html, grouping_statistics
from mail_merger.spreadsheets import grouped_recipients


def setup_papers(app):
    c = app.campaign
    c.headers = ['UID', 'Email', 'Name', 'Code', 'Paper']
    c.fields = c.headers.copy()
    c.uid_column, c.email_column = 'UID', 'Email'
    c.subject = 'Papers'
    c.body = '<p>Hello ${Name}</p><ol><li>${Code}: ${Paper}</li></ol>'
    c.source_rows = [dict(zip(c.headers, ['09013', 'professor@example.com', 'Professor', code, paper]))
                     for code, paper in [('AI26509', 'Machine Learning'), ('CS24303', 'Data Mining'), ('IT335', 'Data Mining')]]
    c.recipients = grouped_recipients(c.source_rows, c.headers, 'UID', 'Email')
    app.changed()


def test_user_can_collapse_name_and_keep_correlated_papers_and_saved_policy(application):
    setup_papers(application)
    application.review()
    application.set_collapse_columns(['Name', 'UID'])
    c = application.campaign
    assert application.review_revision is None
    assert c.recipients[0].values['Name'] == 'Professor'
    assert c.recipients[0].values['Paper'] == 'Machine Learning\nData Mining\nData Mining'
    assert application.review() == ([], [])
    saved = application.store.load(c.id)
    assert saved.collapse_columns == ['UID', 'Name']
    assert saved.recipients[0].field_rows == {'UID': [0], 'Name': [0]}
    assert saved.recipients[0].values == c.recipients[0].values
    application.reuse()
    assert application.campaign.collapse_columns == ['UID', 'Name']


def test_partial_collapse_is_counted_and_blocks_paired_list_review(application):
    setup_papers(application)
    application.set_collapse_columns(['Name', 'Paper'])
    metrics = {label: count for label, count, _, _ in grouping_statistics(application.campaign)}
    assert metrics == {'Source rows': 3, 'Recipients': 1, 'Rows with collapsed values': 2, 'Rows with pairing risks': 3}
    errors, _ = application.review()
    assert len(errors) == 1 and 'no longer match their source rows' in errors[0]
    assert 'Code: 3 values' in errors[0] and 'Paper: 2 values' in errors[0]
    with pytest.raises(ValueError, match='review errors'):
        application.approve(['recipients', 'fields', 'content', 'attachments'], True)
    application.set_collapse_columns(['Name'])
    assert application.review() == ([], [])
    assert grouping_statistics(application.campaign)[-1][1] == 0


def test_equal_value_counts_with_different_original_row_positions_are_not_paired(application):
    setup_papers(application)
    c = application.campaign
    for row, code in zip(c.source_rows, ['A', 'A', 'B']):
        row['Code'] = code
    for row, paper in zip(c.source_rows, ['X', 'Y', 'Y']):
        row['Paper'] = paper
    application.set_collapse_columns(['Name', 'Code', 'Paper'])
    assert c.recipients[0].field_rows['Code'] == [0, 2]
    assert c.recipients[0].field_rows['Paper'] == [0, 1]
    assert 'no longer match their source rows' in application.review()[0][0]


def test_hover_examples_are_bounded_and_escaped_and_snapshot_requires_reimport(application):
    setup_papers(application)
    application.campaign.source_rows[0]['Name'] = '<script>bad()</script>'
    markup = analytics_html(application.campaign)
    assert '&lt;script&gt;bad()&lt;/script&gt;' in markup
    assert '<script>' not in markup
    assert markup.count('class="analytics-card') == 4
    application.switch(application.campaign.id)
    with pytest.raises(ValueError, match='Reimport'):
        application.set_collapse_columns(['Name'])


def test_risk_card_lists_every_affected_person_and_every_original_row(application):
    from lxml import html
    setup_papers(application)
    c = application.campaign
    pattern = c.source_rows + [dict(c.source_rows[-1], Code='FOURTH-PAPER')]
    c.source_rows = [dict(row, UID=f'professor-{i}', Email=f'professor{i}@example.com')
                     for i in range(1, 6) for row in pattern]
    c.recipients = grouped_recipients(c.source_rows, c.headers, 'UID', 'Email')
    application.set_collapse_columns(['Name', 'Paper'])
    cards = html.fragment_fromstring(analytics_html(c)).xpath('./article')
    risk = cards[-1]
    assert risk.xpath('.//strong[@class="analytics-count"]/text()') == ['20']
    assert len(risk.xpath('.//section')) == 5
    assert len(risk.xpath('.//tbody/tr')) == 20
    assert 'UID professor-5' in risk.text_content()
    assert risk.text_content().count('FOURTH-PAPER') == 5
    assert 'all affected rows' in risk.text_content()
    assert len(cards[0].xpath('.//section')) == 3
    assert len(cards[0].xpath('.//tbody/tr')) == 9
    application.switch(c.id)
    saved_risk = html.fragment_fromstring(analytics_html(application.campaign)).xpath('./article')[-1]
    assert len(saved_risk.xpath('.//section')) == 5
    assert 'Reimport the sheet' in saved_risk.text_content()
