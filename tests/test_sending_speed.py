import asyncio
import time

import pytest

from conftest import FakeAuth
from mail_merger.controller import Application
from mail_merger.models import Recipient, Submission
from mail_merger.sending import SenderEngine
from test_sending import SimulatedProvider, finish
from test_gmail_app_password import smtp_auth


def test_settings_persist_and_outlook_has_effective_caps(application):
    app = application
    revision = app.campaign.revision
    app.configure_sending(8, 120)
    assert app.engine.workers == 8 and app.engine.interval == .5
    assert app.campaign.revision == revision
    restored = Application(app.store, FakeAuth())
    assert restored.sending_preferences == {'workers': 8, 'per_minute': 120}
    restored.campaign.provider = 'outlook'
    assert restored.effective_sending_speed() == (4, 30)
    assert '4 workers' in restored.sending_speed_info()


@pytest.mark.parametrize('workers,rate', [(0,30),(9,30),(2,0),(2,301),(2.5,30),(2,float('nan')),(None,30)])
def test_invalid_speed_is_rejected(application, workers, rate):
    with pytest.raises(ValueError):
        application.configure_sending(workers, rate)
    assert application.sending_preferences == {'workers': 2, 'per_minute': 30}


def test_eight_workers_process_thousand_recipients_once_with_bounded_queue(store, campaign):
    campaign.provider = 'outlook'  # engine simulation bypasses production account limits
    campaign.recipients = [Recipient(str(i), {'Email':f'user{i}@example.com','Name':'Ada'}) for i in range(1000)]
    campaign.approved_revision = campaign.revision
    store.save(campaign)
    provider = SimulatedProvider(delay=.02)
    engine = SenderEngine(store, lambda c: provider, interval=0, workers=8)
    engine.start(campaign)
    with pytest.raises(ValueError, match='Pause sending'):
        engine.configure(4,120)
    finish(engine, 45)
    assert store.counts(campaign.run_id) == {'accepted':1000}
    assert len(provider.sent) == len(set(provider.sent)) == 1000
    assert 3 <= provider.max_inflight <= 8
    assert engine.max_queue_size <= 100


def test_rate_is_global_across_workers(store, campaign, monkeypatch):
    starts = []
    log = store.log
    delayed = False
    def slow_first_submission(*args):
        nonlocal delayed
        if args[2].startswith('Submitting') and not delayed:
            delayed = True
            time.sleep(.08)
        return log(*args)
    monkeypatch.setattr(store, 'log', slow_first_submission)
    class TimedProvider(SimulatedProvider):
        async def submit(self, mail):
            starts.append(time.monotonic())
            return Submission('accepted','Accepted by simulation')
    campaign.recipients = [Recipient(str(i), {'Email':f'user{i}@example.com','Name':'Ada'}) for i in range(6)]
    campaign.approved_revision = campaign.revision
    store.save(campaign)
    engine = SenderEngine(store, lambda c: TimedProvider(), interval=.03, workers=8)
    engine.start(campaign)
    finish(engine)
    assert len(starts) == 6
    assert all(b-a >= .025 for a,b in zip(starts,starts[1:]))


async def test_smtp_pool_matches_selected_worker_count(smtp_auth, campaign):
    from test_gmail_app_password import FakeSMTP
    from mail_merger.providers import GmailSMTPProvider
    smtp_auth.connect_gmail('sender@example.com', 'abcdefghijklmnop')
    provider = GmailSMTPProvider(campaign.sender, smtp_auth, workers=8)
    from mail_merger.templates import prepare
    mail = prepare(campaign, campaign.recipients[0])
    results = await asyncio.gather(*(provider.submit(mail) for _ in range(8)))
    assert all(result.state == 'accepted' for result in results)
    assert len(provider.connections) == 8
    assert sum(len(connection.messages) for connection in FakeSMTP.instances) == 8
    await provider.aclose()
    assert all(connection.closed for connection in FakeSMTP.instances)
