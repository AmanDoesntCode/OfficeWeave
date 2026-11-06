from pathlib import Path

import pytest

from mail_merger.controller import Application
from mail_merger.models import Campaign, Recipient
from mail_merger.storage import Store


class MemoryVault:
    def __init__(self):
        self.data = {}
    def read(self, key):
        return self.data.get(key)
    def write(self, key, value):
        self.data[key] = value
    def delete(self, key):
        self.data.pop(key, None)


class FakeAuth:
    busy = False
    status = "Connected for simulation."
    def accounts(self):
        return {"gmail": "sender@example.com"}
    def token(self, key, expected_email=""):
        if key not in {"gmail", "outlook", "sheets"}:
            raise ValueError("No sender")
        return "simulated-token-never-sent"
    def validate_sender(self, key, expected_email=""):
        self.token(key, expected_email)
    def begin(self, key):
        return "Simulation only"


@pytest.fixture
def store(tmp_path):
    instance = Store(tmp_path / "data")
    yield instance
    instance.close()


@pytest.fixture
def campaign():
    c = Campaign(name="Test campaign", headers=["Email", "Name"], fields=["Email", "Name"],
                 email_column="Email", provider="gmail", sender="sender@example.com",
                 subject="Hello ${Name}", body="<p>Hello ${Name}</p>")
    c.recipients = [Recipient("1", {"Email": "one@example.com", "Name": "Ada"}),
                    Recipient("2", {"Email": "two@example.com", "Name": "Lin"})]
    return c


@pytest.fixture
def application(store, campaign):
    store.save(campaign)
    return Application(store, FakeAuth())
