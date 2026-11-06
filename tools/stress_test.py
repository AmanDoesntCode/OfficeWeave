"""Exercise real preparation/SQLite/dispatch with simulated providers, never email."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import tempfile
import time
import tracemalloc

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mail_merger.attachments import copy_attachment
from mail_merger.models import Campaign, Recipient, Submission
from mail_merger.sending import SenderEngine
from mail_merger.storage import Store


class SimulatedTransport:
    def __init__(self, fault=None):
        self.fault = fault
        self.calls = 0
        self.accepted = set()
        self.unknown = set()
        self.inflight = self.max_inflight = 0
        self.payload_bytes = 0
        self.closed = False
        self.failed_recipient = None
        self.failures = 0

    async def submit(self, mail):
        self.calls += 1
        number = self.calls
        self.inflight += 1
        self.max_inflight = max(self.max_inflight, self.inflight)
        self.payload_bytes += len(mail.payload)
        try:
            await asyncio.sleep(.001)
            if self.fault and (number == 101 or mail.to == self.failed_recipient):
                self.failed_recipient = mail.to
                self.failures += 1
                if self.fault == 'unknown':
                    self.unknown.add(mail.to)
                return Submission(self.fault, 'Simulated connection interruption', network_interrupted=True)
            assert mail.to not in self.accepted, 'Duplicate provider submission'
            self.accepted.add(mail.to)
            return Submission('accepted', 'Accepted by simulated provider')
        finally:
            self.inflight -= 1

    async def aclose(self):
        self.closed = True


def wait(engine, store, run, timeout=180):
    started = time.perf_counter()
    latencies = []
    while engine.running:
        if time.perf_counter() - started > timeout:
            engine.pause()
            engine.shutdown()
            raise AssertionError('Stress run exceeded timeout')
        tick = time.perf_counter()
        store.counts(run)
        latencies.append((time.perf_counter() - tick) * 1000)
        engine.thread.join(.02)
    assert engine.state == 'Finished' or engine.state.startswith('Paused'), engine.state
    return max(latencies, default=0)


def scenario(root, provider, fault=None):
    store = Store(root)
    c = Campaign(name='1000-message stress test', provider=provider, sender='sender@example.com',
                 headers=['Email', 'Name', 'Subject'], fields=['Email', 'Name', 'Subject'], email_column='Email',
                 subject='Question papers for ${Name}', body='<p>Hello ${Name}</p><ol><li>${Subject}</li></ol>')
    c.recipients = [Recipient(str(i), {'Email': f'user{i}@example.com', 'Name': f'Professor {i}',
                                     'Subject': 'Algebra\nStatistics\nCalculus'}) for i in range(1000)]
    path = root / 'syllabus.pdf'
    path.write_bytes(b'simulated attachment\n' * 6500)
    c.shared = [copy_attachment(str(path), root, c.id)]
    c.approved_revision = c.revision
    store.save(c)
    first = SimulatedTransport(fault)
    engine = SenderEngine(store, lambda campaign: first, interval=0)
    tracemalloc.start()
    started = time.perf_counter()
    engine.start(c)
    max_read = wait(engine, store, c.run_id)
    paused_counts = store.counts(c.run_id)
    second = None
    if fault:
        assert 'network' in engine.state and paused_counts.get('pending', 0) > 0
        # Reopen SQLite and use a fresh engine to exercise restart recovery.
        restored = Store(root)
        c = restored.load(c.id)
        second = SimulatedTransport()
        resumed = SenderEngine(restored, lambda campaign: second, interval=0)
        resumed.start(c)
        max_read = max(max_read, wait(resumed, restored, c.run_id))
        assert not first.accepted & second.accepted, 'Accepted mail was sent twice after restart'
        assert not first.unknown & second.accepted, 'Unknown mail was resent after restart'
        assert resumed.max_queue_size <= 100
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    counts = store.counts(c.run_id)
    accepted = first.accepted | (second.accepted if second else set())
    assert len(accepted) + len(first.unknown) == 1000
    assert counts.get('accepted', 0) == len(accepted)
    assert counts.get('unknown', 0) == len(first.unknown)
    assert not any(counts.get(key) for key in ('pending', 'submitting', 'failed'))
    assert engine.max_queue_size <= 100
    assert max(first.max_inflight, second.max_inflight if second else 0) <= 2
    assert first.closed and (second is None or second.closed)
    assert peak < 96 * 1024 * 1024, 'Unexpected memory growth'
    result = dict(provider=provider, scenario=fault or 'uninterrupted', recipients=1000,
                attachment_bytes=path.stat().st_size, elapsed_seconds=round(elapsed, 2), states=counts,
                paused_states=paused_counts if fault else None, duplicate_accepted_sends=0,
                max_queue=max(engine.max_queue_size, resumed.max_queue_size if fault else 0),
                max_concurrent=max(first.max_inflight, second.max_inflight if second else 0),
                peak_python_memory_mb=round(peak / 1024**2, 2),
                max_progress_read_ms=round(max_read, 2), initial_failed_attempts=first.failures, simulated=True)
    store.close()
    if fault:
        restored.close()
    return result


def main():
    results = []
    for provider, fault in [('gmail', None), ('outlook', None), ('gmail', 'retry'), ('outlook', 'unknown')]:
        print(f'Starting 1000-message {provider} scenario: {fault or "uninterrupted"}', flush=True)
        with tempfile.TemporaryDirectory(prefix='mail-merger-stress-') as directory:
            result = scenario(Path(directory), provider, fault)
        results.append(result)
        print(json.dumps(result), flush=True)
    output = Path(__file__).resolve().parents[1] / 'test-results' / 'stress-1000.json'
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps({'real_emails_sent': 0, 'production_rate_limit_unchanged': True,
                                  'results': results}, indent=2), encoding='utf-8')
    print(f'Wrote {output}', flush=True)


if __name__ == '__main__':
    main()
