import json
from email import policy
from email.parser import BytesParser

import pytest

from mail_merger.spreadsheets import grouped_recipients
from mail_merger.templates import prepare, recipient_addresses


def alternate_campaign(campaign):
    campaign.headers.append('Alternate email id')
    campaign.alternate_email_column = 'Alternate email id'
    campaign.recipients[0].values['Alternate email id'] = 'ada.alt@example.com'
    campaign.recipients[1].values['Alternate email id'] = 'lin.alt@example.com'
    return campaign


def test_each_person_gets_only_their_own_alternate_address_in_cc(campaign):
    alternate_campaign(campaign)
    for recipient, alternate in zip(campaign.recipients, ['ada.alt@example.com','lin.alt@example.com']):
        mail = prepare(campaign, recipient)
        message = BytesParser(policy=policy.default).parsebytes(mail.payload)
        assert message['Cc'] == alternate
        assert message['Bcc'] is None
        assert mail.envelope_to == [recipient.values['Email'], alternate]
        other = 'lin.alt@example.com' if recipient.id == '1' else 'ada.alt@example.com'
        assert other not in mail.envelope_to and other.encode() not in mail.payload


@pytest.mark.parametrize('provider', ['gmail','outlook'])
def test_bcc_route_is_private_and_does_not_create_a_second_message(campaign, provider):
    alternate_campaign(campaign)
    campaign.provider = provider
    recipient = campaign.recipients[0]
    recipient.alternate_mode = 'bcc'
    mail = prepare(campaign, recipient)
    assert mail.envelope_to == ['one@example.com','ada.alt@example.com']
    if provider == 'gmail':
        message = BytesParser(policy=policy.default).parsebytes(mail.payload)
        assert message['Cc'] is None and message['Bcc'] is None
        assert b'ada.alt@example.com' not in mail.payload
    else:
        message = json.loads(mail.payload)['message']
        assert message['ccRecipients'] == []
        assert message['bccRecipients'] == [{'emailAddress': {'address': 'ada.alt@example.com'}}]


def test_grouped_uid_merges_alternates_without_repeated_destinations(campaign):
    campaign.headers = ['UID','Email','Name','Alternate']
    campaign.uid_column = 'UID'
    campaign.alternate_email_column = 'Alternate'
    rows = [dict(zip(campaign.headers, ['1','one@example.com','Ada', alternate]))
            for alternate in ['ONE@example.com','alt@example.com','ALT@example.com','other@example.com']]
    recipient = grouped_recipients(rows, campaign.headers, 'UID','Email')[0]
    assert recipient_addresses(campaign, recipient) == ('one@example.com', ['ALT@example.com','other@example.com'], [])
    assert len(prepare(campaign, recipient).envelope_to) == 3


def test_blank_optional_email_is_allowed_invalid_email_blocks_review(application):
    alternate_campaign(application.campaign)
    application.campaign.recipients[0].values['Alternate email id'] = ''
    assert not application.review()[0]
    application.campaign.recipients[0].values['Alternate email id'] = 'invalid'
    assert any('Invalid email address' in error for error in application.review()[0])


@pytest.mark.parametrize('provider', ['gmail','outlook'])
@pytest.mark.parametrize('mode', ['cc','bcc'])
@pytest.mark.parametrize('empty', ['', '   ', '\n\t', None])
def test_sparse_alternate_column_does_not_require_every_person_to_have_one(campaign, provider, mode, empty):
    alternate_campaign(campaign)
    campaign.provider = provider
    recipient = campaign.recipients[0]
    recipient.values['Alternate email id'] = empty
    recipient.alternate_mode = mode
    mail = prepare(campaign, recipient)
    assert mail.envelope_to == ['one@example.com']
    if provider == 'gmail':
        message = BytesParser(policy=policy.default).parsebytes(mail.payload)
        assert message['Cc'] is None and message['Bcc'] is None
    else:
        message = json.loads(mail.payload)['message']
        assert message['ccRecipients'] == message['bccRecipients'] == []
    assert prepare(campaign, campaign.recipients[1]).envelope_to == ['two@example.com','lin.alt@example.com']


@pytest.mark.parametrize('empty', ['', '   '])
def test_primary_address_remains_mandatory_even_with_an_alternate(campaign, empty):
    alternate_campaign(campaign)
    campaign.recipients[0].values['Email'] = empty
    with pytest.raises(ValueError, match='Invalid email address'):
        prepare(campaign, campaign.recipients[0])


def test_missing_alternate_cell_is_optional_but_other_merge_blanks_still_need_ack(application):
    alternate_campaign(application.campaign)
    del application.campaign.recipients[0].values['Alternate email id']
    application.campaign.recipients[0].values['Name'] = ''
    errors, blanks = application.review()
    assert not errors and any('Name' in blank for blank in blanks)


def test_grouped_source_rows_allow_sparse_alternate_addresses(campaign):
    campaign.headers = ['UID','Email','Name','Alternate']
    campaign.alternate_email_column = 'Alternate'
    rows = [{'UID':str(i),'Email':f'user{i}@example.com','Name':'Professor','Alternate':alt}
            for i,alt in [(1,''),(1,'alt@example.com'),(2,''),(3,'')]]
    recipients = grouped_recipients(rows, campaign.headers, 'UID','Email')
    assert all(recipient.selected for recipient in recipients)
    assert [prepare(campaign, recipient).envelope_to for recipient in recipients] == [
        ['user1@example.com','alt@example.com'], ['user2@example.com'], ['user3@example.com']]


def test_row_bcc_choice_invalidates_review_and_survives_reopening(application):
    alternate_campaign(application.campaign)
    application.review()
    application.selection(['1','2'], application.campaign.fields, 'Email', {'1':'bcc','2':'cc'})
    assert application.review_revision is None
    saved = application.store.load(application.campaign.id)
    assert saved.alternate_email_column == 'Alternate email id'
    assert [r.alternate_mode for r in saved.recipients] == ['bcc','cc']
    assert 'BCC: ada.alt@example.com' in application.preview('1')[2]
    assert 'CC: lin.alt@example.com' in application.preview('2')[2]


def test_no_alternate_column_is_a_noop_and_primary_cannot_be_reused(application):
    with pytest.raises(ValueError, match='different column'):
        application.set_alternate_email_column('Email')
    assert recipient_addresses(application.campaign, application.campaign.recipients[0]) == ('one@example.com', [], [])
