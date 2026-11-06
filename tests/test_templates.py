import json
from email import policy
from email.parser import BytesParser

import pytest

from mail_merger.attachments import copy_attachment
from mail_merger.templates import address, fields_used, prepare, render, sanitize


def test_values_are_escaped_without_recursive_substitution():
    result = render('<p>${Name}</p>', {"Name": '<script>bad()</script>${Other}'}, html=True)
    assert '<script>' not in result
    assert '&lt;script&gt;' in result
    assert '${Other}' in result


def test_case_sensitive_and_incomplete_fields():
    with pytest.raises(ValueError, match="Unresolved"):
        render('${name}', {"Name": "Ada"})
    with pytest.raises(ValueError, match="Incomplete"):
        fields_used('Hello ${Name')


def test_link_values_cannot_escape_the_attribute():
    result = render('<a href="https://example.com/?q=${Name}">Hello</a>',
                    {"Name": '\" onclick=\"alert(1)'}, html=True)
    assert 'onclick="' not in result
    assert '&quot;' in result


@pytest.mark.parametrize('body', ['<a href="${URL}">Open</a>', '<div title="${URL}">Open</div>',
                                  '<script>${URL}</script>', '<style>${URL}</style>'])
def test_unsafe_attribute_or_script_fields_rejected(body):
    with pytest.raises(ValueError):
        render(body, {"URL": "javascript:alert(1)"}, html=True)


def test_html_sanitization():
    result = sanitize('<p onclick="bad()">Hi</p><script>bad()</script><img src="https://example.com/tracker">')
    assert result == '<p>Hi</p>'


def test_gmail_payload_has_personalization_and_attachment(campaign, tmp_path):
    path = tmp_path / 'welcome.txt'
    path.write_text('A real attachment')
    campaign.shared = [copy_attachment(str(path), tmp_path, campaign.id)]
    mail = prepare(campaign, campaign.recipients[0])
    message = BytesParser(policy=policy.default).parsebytes(mail.payload)
    assert message['Subject'] == 'Hello Ada'
    assert message['To'] == 'one@example.com'
    assert next(message.iter_attachments()).get_content().strip() == 'A real attachment'


def test_outlook_payload_cc_bcc(campaign):
    campaign.provider = 'outlook'
    campaign.cc = 'cc@example.com; second@example.com'
    campaign.bcc = 'private@example.com'
    data = json.loads(prepare(campaign, campaign.recipients[0]).payload)
    assert data['saveToSentItems'] is True
    assert len(data['message']['ccRecipients']) == 2
    assert data['message']['bccRecipients'][0]['emailAddress']['address'] == 'private@example.com'
    assert data['message']['body']['content'] == '<p>Hello Ada</p>'


def test_encoded_size_not_just_raw_files(campaign, tmp_path):
    path = tmp_path / 'large.bin'
    path.write_bytes(b'x' * 2_990_000)
    campaign.shared = [copy_attachment(str(path), tmp_path, campaign.id)]
    with pytest.raises(ValueError, match='4,000,000'):
        prepare(campaign, campaign.recipients[0])


def test_subject_header_injection_blocked(campaign):
    campaign.subject = '${Name}'
    campaign.recipients[0].values['Name'] = 'Hi\r\nBcc: other@example.com'
    with pytest.raises(ValueError, match='line breaks'):
        prepare(campaign, campaign.recipients[0])


def test_gmail_bcc_only_in_smtp_envelope(campaign):
    campaign.cc = 'copy@example.com'
    campaign.bcc = 'private@example.com'
    mail = prepare(campaign, campaign.recipients[0])
    message = BytesParser(policy=policy.default).parsebytes(mail.payload)
    assert message['Bcc'] is None
    assert b'private@example.com' not in mail.payload
    assert mail.envelope_to == ['one@example.com', 'copy@example.com', 'private@example.com']


def test_gmail_unicode_body_is_transport_safe(campaign):
    campaign.recipients[0].values['Name'] = 'नमस्ते'
    mail = prepare(campaign, campaign.recipients[0])
    assert mail.payload.isascii()
    message = BytesParser(policy=policy.default).parsebytes(mail.payload)
    assert 'नमस्ते' in message.get_body(preferencelist=('html',)).get_content()


@pytest.mark.parametrize('email', ['not-an-email', 'user@example.com\r\nBcc: x@example.com', '', 'a,b@example.com'])
def test_bad_recipient(email):
    with pytest.raises(ValueError):
        address(email)
