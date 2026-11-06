import asyncio
import json
import time
import threading

import httpx
import pytest

from mail_merger.models import Recipient, Submission
from mail_merger.providers import APIProvider
from mail_merger.sending import SenderEngine
from mail_merger.templates import prepare
from conftest import FakeAuth


class SimulatedProvider:
    def __init__(self, result=None, delay=0):
        self.result = result or Submission('accepted', 'Accepted by provider; simulation only.')
        self.delay = delay
        self.sent = []
        self.inflight = 0
        self.max_inflight = 0
        self.first_sent = threading.Event()
    async def submit(self, mail):
        self.inflight += 1
        self.max_inflight = max(self.max_inflight, self.inflight)
        await asyncio.sleep(self.delay)
        self.sent.append(mail.to)
        self.first_sent.set()
        self.inflight -= 1
        return self.result
    async def aclose(self):
        pass


def finish(engine, timeout=20):
    engine.thread.join(timeout)
    assert not engine.running, 'Background send did not finish'


def test_thousand_recipients_bounded_queue_and_two_workers(store, campaign):
    campaign.provider = 'outlook'
    campaign.recipients = [Recipient(str(i), {'Email': f'user{i}@example.com', 'Name': f'Person {i}'}) for i in range(1000)]
    campaign.approved_revision = campaign.revision
    store.save(campaign)
    provider = SimulatedProvider(delay=0.001)
    engine = SenderEngine(store, lambda c: provider, interval=0)
    engine.start(campaign)
    finish(engine, 45)
    assert store.counts(campaign.run_id) == {'accepted': 1000}
    assert len(set(provider.sent)) == 1000
    assert engine.max_queue_size <= 100
    assert provider.max_inflight == 2
    with pytest.raises(ValueError, match='No pending'):
        engine.start(campaign)


@pytest.mark.parametrize('state', ['auth', 'quota'])
def test_auth_or_quota_pauses_and_preserves_pending(store, campaign, state):
    campaign.approved_revision = campaign.revision
    store.save(campaign)
    provider = SimulatedProvider(Submission(state, 'Paused by simulation'))
    engine = SenderEngine(store, lambda c: provider, interval=0)
    engine.start(campaign)
    finish(engine)
    assert store.counts(campaign.run_id) == {'pending': 2}
    assert engine.state.startswith('Paused')


def test_unknown_outcome_never_automatically_retried(store, campaign):
    campaign.approved_revision = campaign.revision
    store.save(campaign)
    provider = SimulatedProvider(Submission('unknown', 'Uncertain; inspect Sent mail'))
    engine = SenderEngine(store, lambda c: provider, interval=0)
    engine.start(campaign)
    finish(engine)
    assert store.counts(campaign.run_id) == {'unknown': 2}
    assert len(provider.sent) == 2


def test_pause_resume_and_cancel(store, campaign):
    campaign.provider = 'outlook'
    campaign.recipients = [Recipient(str(i), {'Email': f'user{i}@example.com', 'Name': 'Ada'}) for i in range(25)]
    campaign.approved_revision = campaign.revision
    store.save(campaign)
    provider = SimulatedProvider(delay=0.03)
    engine = SenderEngine(store, lambda c: provider, interval=0.01)
    engine.start(campaign)
    assert provider.first_sent.wait(5)
    engine.pause()
    finish(engine)
    counts = store.counts(campaign.run_id)
    assert counts.get('accepted', 0) > 0
    assert counts['pending'] > 0
    first_sent = set(provider.sent)
    provider.first_sent.clear()
    engine.start(campaign)
    assert provider.first_sent.wait(5)
    engine.cancel()
    finish(engine)
    assert store.counts(campaign.run_id).get('cancelled', 0) > 0
    assert len(provider.sent) == len(set(provider.sent))
    assert first_sent.issubset(provider.sent)


def test_retry_after_then_accept(store, campaign, monkeypatch):
    import mail_merger.sending as sending
    # Keep the test short without replacing provider or persistence behavior.
    monkeypatch.setattr(sending.random, 'random', lambda: 0)
    campaign.recipients = campaign.recipients[:1]
    campaign.approved_revision = campaign.revision
    store.save(campaign)
    class RetryProvider(SimulatedProvider):
        async def submit(self, mail):
            self.sent.append(mail.to)
            return Submission('retry', 'Throttled', 0.01) if len(self.sent) == 1 else Submission('accepted', 'Accepted')
    provider = RetryProvider()
    engine = SenderEngine(store, lambda c: provider, interval=0)
    engine.start(campaign)
    finish(engine)
    delivery = store.delivery(campaign.run_id, campaign.recipients[0].id)
    assert delivery['state'] == 'accepted'
    assert delivery['attempts'] == 2


def test_third_safe_retry_accepts_once_and_is_never_started_again(store, campaign, monkeypatch):
    import mail_merger.sending as sending
    monkeypatch.setattr(sending.random, 'random', lambda: 0)
    campaign.recipients = campaign.recipients[:1]
    campaign.approved_revision = campaign.revision
    store.save(campaign)
    class RecoversOnFourthAttempt(SimulatedProvider):
        attempts = 0
        async def submit(self, mail):
            self.attempts += 1
            if self.attempts <= 3:
                return Submission('retry', 'Connection failed before submission', network_interrupted=True)
            self.sent.append(mail.to)
            return Submission('accepted', 'Accepted exactly once by simulated provider')
    provider = RecoversOnFourthAttempt()
    engine = SenderEngine(store, lambda c: provider, interval=0)
    engine.start(campaign)
    finish(engine, 30)
    assert provider.attempts == 4
    assert provider.sent == ['one@example.com']
    assert store.counts(campaign.run_id) == {'accepted': 1}
    with pytest.raises(ValueError, match='No pending'):
        engine.start(campaign)


@pytest.mark.parametrize('status,headers,body,expected', [
    (202, {}, {}, 'accepted'),
    (401, {}, {}, 'auth'),
    (429, {'Retry-After': '3'}, {}, 'retry'),
    (403, {}, {'error': {'errors': [{'reason': 'dailyLimitExceeded'}]}}, 'quota'),
    (429, {'Retry-After': '600'}, {}, 'quota'),
    (500, {}, {}, 'unknown'),
    (400, {}, {}, 'failed'),
])
async def test_provider_response_classification(campaign, status, headers, body, expected):
    campaign.provider = 'outlook'
    async def handler(request):
        assert request.headers['Authorization'].startswith('Bearer ')
        return httpx.Response(status, headers=headers, json=body)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = APIProvider('outlook', campaign.sender, FakeAuth(), client)
    result = await provider.submit(prepare(campaign, campaign.recipients[0]))
    assert result.state == expected
    if expected == 'retry':
        assert result.retry_after == 3
    await provider.aclose()


@pytest.mark.parametrize('error,expected', [(httpx.ConnectError, 'retry'), (httpx.ReadTimeout, 'unknown'),
                                          (httpx.WriteError, 'unknown')])
async def test_network_uncertainty_classification(campaign, error, expected):
    campaign.provider = 'outlook'
    async def handler(request):
        raise error('simulated interruption', request=request)
    provider = APIProvider('outlook', campaign.sender, FakeAuth(), httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    result = await provider.submit(prepare(campaign, campaign.recipients[0]))
    assert result.state == expected
    assert result.network_interrupted
    await provider.aclose()


@pytest.mark.parametrize('outcome', ['retry', 'unknown'])
def test_network_interruption_pauses_then_restart_resumes_only_pending(store, campaign, outcome):
    campaign.recipients = [Recipient(str(i), {'Email': f'user{i}@example.com', 'Name': 'Ada'}) for i in range(1000)]
    campaign.approved_revision = campaign.revision
    store.save(campaign)
    interrupted = SimulatedProvider(Submission(outcome, 'Simulated network interruption', network_interrupted=True))
    engine = SenderEngine(store, lambda c: interrupted, interval=0)
    engine.start(campaign)
    finish(engine, 45)
    paused = store.counts(campaign.run_id)
    assert engine.state.startswith('Paused') and 'network' in engine.state
    assert 1 <= len(interrupted.sent) <= (8 if outcome == 'retry' else 2)
    if outcome == 'retry':
        attempts = [store.delivery(campaign.run_id, r.id)['attempts'] for r in campaign.recipients]
        assert max(attempts) == 4  # initial attempt plus exactly three retries
        assert max(interrupted.sent.count(email) for email in set(interrupted.sent)) == 4
    assert paused.get('pending', 0) >= 998
    assert not paused.get('submitting') and not paused.get('failed')
    assert paused.get('unknown', 0) == (len(interrupted.sent) if outcome == 'unknown' else 0)
    from mail_merger.storage import Store
    restored_store = Store(store.root)
    restored = restored_store.load(campaign.id)
    recovered = SimulatedProvider()
    restarted = SenderEngine(restored_store, lambda c: recovered, interval=0)
    restarted.start(restored)
    finish(restarted, 45)
    assert len(recovered.sent) == len(set(recovered.sent))
    if outcome == 'unknown':
        assert not set(interrupted.sent) & set(recovered.sent)
    counts = restored_store.counts(restored.run_id)
    assert counts.get('accepted', 0) + counts.get('unknown', 0) == 1000
    assert not counts.get('pending')


def test_unexpected_submission_exception_also_pauses_without_resending(store, campaign):
    campaign.approved_revision = campaign.revision
    store.save(campaign)
    class BrokenProvider(SimulatedProvider):
        async def submit(self, mail):
            raise RuntimeError('simulated interruption')
    engine = SenderEngine(store, lambda c: BrokenProvider(), interval=0)
    engine.start(campaign)
    finish(engine)
    assert engine.state.startswith('Paused')
    assert store.counts(campaign.run_id).get('unknown', 0) >= 1
