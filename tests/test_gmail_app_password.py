"""Gmail app-password authentication and SMTP submission without real networking."""

import smtplib

import pytest

from mail_merger.auth import AuthenticationRequired, AuthService
from mail_merger.providers import GmailSMTPProvider
from mail_merger.templates import prepare
from conftest import MemoryVault


class FakeSMTP:
    instances = []
    login_error = None
    send_error = None
    refused = {}

    def __init__(self, host, port, context, timeout):
        assert host == 'smtp.gmail.com' and port == 465
        assert context.check_hostname
        self.logged_in = False
        self.closed = False
        self.messages = []
        self.__class__.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def login(self, email, password):
        assert email == 'sender@example.com' and password == 'abcdefghijklmnop'
        if self.login_error:
            raise self.login_error
        self.logged_in = True

    def sendmail(self, sender, recipients, payload):
        assert self.logged_in
        if self.send_error:
            raise self.send_error
        self.messages.append((sender, recipients, payload))
        return self.refused

    def close(self):
        self.closed = True


@pytest.fixture
def smtp_auth(tmp_path, monkeypatch):
    FakeSMTP.instances = []
    FakeSMTP.login_error = FakeSMTP.send_error = None
    FakeSMTP.refused = {}
    monkeypatch.setattr(smtplib, 'SMTP_SSL', FakeSMTP)
    return AuthService(tmp_path, MemoryVault())


def test_gmail_login_checks_credentials_without_sending(smtp_auth):
    smtp_auth.connect_gmail('sender@example.com', 'abcd efgh ijkl mnop')
    assert smtp_auth.accounts()['gmail'] == 'sender@example.com'
    assert smtp_auth.gmail_credentials('sender@example.com') == ('sender@example.com', 'abcdefghijklmnop')
    assert not FakeSMTP.instances[0].messages
    assert FakeSMTP.instances[0].closed
    with pytest.raises(AuthenticationRequired):
        smtp_auth.token('gmail')


def test_rejected_password_not_saved_or_exposed(smtp_auth):
    FakeSMTP.login_error = smtplib.SMTPAuthenticationError(535, b'rejected abcdefghijklmnop')
    with pytest.raises(AuthenticationRequired) as exc:
        smtp_auth.connect_gmail('sender@example.com', 'abcdefghijklmnop')
    assert 'abcdefghijklmnop' not in str(exc.value)
    assert smtp_auth.vault.read('gmail') is None
    assert not smtp_auth.busy


def test_gmail_login_requires_no_oauth_client(smtp_auth):
    assert not (smtp_auth.config_directory / 'google-client.json').exists()
    smtp_auth.connect_gmail('sender@example.com', 'abcdefghijklmnop')
    smtp_auth.validate_sender('gmail', 'sender@example.com')
    with pytest.raises(AuthenticationRequired):
        smtp_auth.validate_sender('gmail', 'someone-else@example.com')


def test_updating_sheets_oauth_does_not_disconnect_gmail(smtp_auth):
    smtp_auth.connect_gmail('sender@example.com', 'abcdefghijklmnop')
    smtp_auth.save_configuration('{"installed":{"client_id":"google-id","client_secret":"client-config"}}', '')
    assert smtp_auth.gmail_credentials()[0] == 'sender@example.com'


def test_second_sender_requires_signout(smtp_auth):
    smtp_auth.connect_gmail('sender@example.com', 'abcdefghijklmnop')
    with pytest.raises(ValueError, match='Only Gmail'):
        smtp_auth.begin('outlook')
    assert 'Sign out' in smtp_auth.begin('gmail')
    with pytest.raises(AuthenticationRequired, match='Sign out'):
        smtp_auth.connect_gmail('sender@example.com', 'abcdefghijklmnop')
    assert len(FakeSMTP.instances) == 1
    assert smtp_auth.accounts() == {'gmail': 'sender@example.com'}
    smtp_auth.sign_out()
    smtp_auth.connect_gmail('sender@example.com', 'abcdefghijklmnop')
    assert len(FakeSMTP.instances) == 2


def test_old_outlook_credentials_are_not_a_connected_account(smtp_auth):
    smtp_auth.vault.write('outlook', {'email': 'outlook@example.com', 'cache': 'simulated'})
    assert smtp_auth.accounts() == {}
    smtp_auth.connect_gmail('sender@example.com', 'abcdefghijklmnop')
    assert smtp_auth.accounts() == {'gmail': 'sender@example.com'}
    assert smtp_auth.vault.read('outlook') is None


def test_restored_multiple_tokens_expose_only_one_sender(smtp_auth):
    smtp_auth.vault.write('gmail', {'email': 'sender@example.com', 'method': 'app_password', 'app_password': 'abcdefghijklmnop'})
    smtp_auth.vault.write('outlook', {'email': 'outlook@example.com', 'cache': 'simulated'})
    assert smtp_auth.accounts() == {'gmail': 'sender@example.com'}
    with pytest.raises(AuthenticationRequired):
        smtp_auth.token('outlook')


async def test_smtp_connections_reused_and_all_closed(smtp_auth, campaign):
    smtp_auth.connect_gmail('sender@example.com', 'abcdefghijklmnop')
    provider = GmailSMTPProvider(campaign.sender, smtp_auth)
    mail = prepare(campaign, campaign.recipients[0])
    for _ in range(6):
        assert (await provider.submit(mail)).state == 'accepted'
    assert len(FakeSMTP.instances) == 3  # one login check plus two pooled sessions
    assert sum(len(s.messages) for s in FakeSMTP.instances) == 6
    await provider.aclose()
    assert all(s.closed for s in FakeSMTP.instances)


@pytest.mark.parametrize('error,expected', [
    (smtplib.SMTPAuthenticationError(535, b'bad credentials'), 'auth'),
    (smtplib.SMTPDataError(451, b'temporarily unavailable'), 'retry'),
    (smtplib.SMTPDataError(550, b'daily user sending quota exceeded'), 'quota'),
    (smtplib.SMTPDataError(550, b'message rejected'), 'failed'),
    (smtplib.SMTPServerDisconnected('lost after DATA'), 'unknown'),
    (TimeoutError('timed out after DATA'), 'unknown'),
])
async def test_smtp_rejection_and_uncertainty(smtp_auth, campaign, error, expected):
    smtp_auth.connect_gmail('sender@example.com', 'abcdefghijklmnop')
    FakeSMTP.send_error = error
    provider = GmailSMTPProvider(campaign.sender, smtp_auth)
    result = await provider.submit(prepare(campaign, campaign.recipients[0]))
    assert result.state == expected
    assert result.network_interrupted == (isinstance(error, (smtplib.SMTPServerDisconnected, OSError))
                                          and not isinstance(error, smtplib.SMTPResponseException))
    assert not any('abcdefghijklmnop' in text for text in [result.detail, smtp_auth.status])
    await provider.aclose()


async def test_smtp_disconnect_before_submission_requests_network_pause(smtp_auth, campaign):
    smtp_auth.connect_gmail('sender@example.com', 'abcdefghijklmnop')
    FakeSMTP.login_error = TimeoutError('connection lost before submission')
    provider = GmailSMTPProvider(campaign.sender, smtp_auth)
    result = await provider.submit(prepare(campaign, campaign.recipients[0]))
    assert result.state == 'retry' and result.network_interrupted
    assert all(not connection.messages for connection in FakeSMTP.instances)
    await provider.aclose()


async def test_partial_envelope_rejection_not_retried(smtp_auth, campaign):
    smtp_auth.connect_gmail('sender@example.com', 'abcdefghijklmnop')
    FakeSMTP.refused = {'copy@example.com': (550, b'no such mailbox')}
    campaign.cc = 'copy@example.com'
    provider = GmailSMTPProvider(campaign.sender, smtp_auth)
    assert (await provider.submit(prepare(campaign, campaign.recipients[0]))).state == 'unknown'
    await provider.aclose()
