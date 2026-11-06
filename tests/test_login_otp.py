import pytest
from conftest import MemoryVault
from mail_merger.login_otp import LoginOTP


@pytest.fixture
def challenge(tmp_path, monkeypatch):
    credentials = MemoryVault()
    credentials.write("otp", {"email": "codes@example.com", "app_password": "abcdefghijklmnop"})
    monkeypatch.setattr("mail_merger.login_otp.ProtectedCredentials", lambda _: credentials)
    sent, now = [], [1000.0]
    otp = LoginOTP(tmp_path, "user@example.com", mailer=lambda *args: sent.append(args), clock=lambda: now[0])
    return otp, sent, now


def test_code_goes_to_signed_in_account_and_is_single_use(challenge):
    otp, sent, _ = challenge
    otp.request()
    assert sent[0][:3] == ("codes@example.com", "abcdefghijklmnop", "user@example.com")
    otp.verify(sent[0][3])
    with pytest.raises(ValueError):
        otp.verify(sent[0][3])


def test_resend_expiry_and_five_attempt_limit(challenge):
    otp, sent, now = challenge
    otp.request()
    with pytest.raises(ValueError, match="60 seconds"):
        otp.request()
    wrong = "000000" if sent[-1][3] != "000000" else "111111"
    for _ in range(5):
        with pytest.raises(ValueError):
            otp.verify(wrong)
    with pytest.raises(ValueError, match="five minutes"):
        otp.request()
    now[0] += 301
    otp.request()
    now[0] += 301
    with pytest.raises(ValueError, match="new sign-in code"):
        otp.verify(sent[-1][3])


def test_smtp_failure_does_not_create_a_valid_challenge(challenge):
    otp, _, _ = challenge
    otp.mailer = lambda *args: (_ for _ in ()).throw(OSError("private dummy failure"))
    with pytest.raises(ValueError, match="Could not send") as exc:
        otp.request()
    assert "private dummy" not in str(exc.value)
    assert otp.challenge is None
