from __future__ import annotations

import asyncio
import random
import threading
import time

from .models import Campaign
from .templates import prepare


class SenderEngine:
    """One asyncio loop in a worker thread; queue items are lightweight row references."""

    def __init__(self, store, provider_factory, interval: float = 2.0, workers: int = 2):
        self.store = store
        self.provider_factory = provider_factory
        self.interval = interval
        self.workers = workers
        self.thread: threading.Thread | None = None
        self.stop = threading.Event()
        self.cancel_requested = threading.Event()
        self.lock = threading.RLock()
        self.campaign_id: str | None = None
        self.state = "Idle"
        self.max_queue_size = 0

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def configure(self, workers, per_minute):
        with self.lock:
            if self.running:
                raise ValueError("Pause sending and wait for in-flight messages before changing speed.")
            if not 1 <= workers <= 8 or not 1 <= per_minute <= 300:
                raise ValueError("Choose 1–8 workers and 1–300 messages per minute.")
            self.workers, self.interval = int(workers), 60 / per_minute

    def start(self, campaign: Campaign):
        with self.lock:
            if self.running:
                raise ValueError("A send is already running.")
            if campaign.approved_revision != campaign.revision:
                raise ValueError("Review and approve the current campaign before sending.")
            selected = [r for r in campaign.recipients if r.selected]
            if not selected:
                raise ValueError("Select at least one recipient.")
            if not campaign.run_id:
                self.store.create_run(campaign, [r.id for r in selected])
            if not self.store.counts(campaign.run_id).get("pending"):
                raise ValueError("No pending recipients remain. Accepted and uncertain submissions are never resent automatically.")
            self.stop.clear()
            self.cancel_requested.clear()
            self.campaign_id = campaign.id
            self.state = "Sending"
            self.thread = threading.Thread(target=self._thread_main, args=(campaign,),
                                           name="mail-merger-sender", daemon=True)
            self.thread.start()

    def pause(self):
        self.stop.set()
        if self.running:
            self.state = "Pausing — waiting for in-flight submissions"

    def cancel(self):
        self.cancel_requested.set()
        self.pause()

    def shutdown(self, timeout: float = 50):
        self.pause()
        if self.thread:
            self.thread.join(timeout)

    def _thread_main(self, campaign):
        try:
            asyncio.run(self._run(campaign))
        except Exception:
            self.state = "Paused after an internal error"
            self.store.log(campaign.id, "", "Sending stopped after an internal error. Pending work is saved.")
        finally:
            if self.cancel_requested.is_set():
                self.store.cancel_pending(campaign.run_id)
                self.state = "Remaining recipients cancelled"
            elif self.state.startswith("Pausing") or (self.state == "Sending" and self.stop.is_set()):
                self.state = "Paused"
            elif self.state == "Sending":
                self.state = "Finished"
            self.store.log(campaign.id, "", self.state)
            counts = self.store.counts(campaign.run_id)
            if not counts.get("pending") and not counts.get("submitting"):
                summary = ", ".join(f"{counts.get(key, 0)} {label}" for key, label in
                                    (("accepted", "accepted by provider"), ("failed", "failed"),
                                     ("unknown", "unknown"), ("cancelled", "cancelled")))
                self.store.log(campaign.id, "", "Campaign closed: " + summary + ".")

    async def _run(self, campaign):
        provider = self.provider_factory(campaign)
        queue = asyncio.Queue(maxsize=100)
        rate_lock = asyncio.Lock()
        next_start = 0.0
        cooldown = 0.0

        async def wait_until(deadline):
            while time.monotonic() < deadline:
                if self.stop.is_set():
                    return False
                await asyncio.sleep(min(0.1, max(0, deadline - time.monotonic())))
            return not self.stop.is_set()

        async def rate_slot(recipient_id, email, attempts):
            nonlocal next_start
            async with rate_lock:
                while True:
                    target = max(next_start, cooldown)
                    if not await wait_until(target):
                        return False
                    if time.monotonic() >= max(next_start, cooldown):
                        self.store.set_delivery(campaign.run_id, recipient_id, "submitting", attempt=True)
                        self.store.log(campaign.id, email, f"Submitting · attempt {attempts + 1}")
                        if self.stop.is_set():
                            self.store.set_delivery(campaign.run_id, recipient_id, "pending", "Paused before submission.")
                            return False
                        # Pace actual submission starts, after persistence. Slow
                        # disk writes must not turn worker reservations into a burst.
                        next_start = time.monotonic() + self.interval
                        return True

        async def producer():
            for recipient in campaign.recipients:
                if self.stop.is_set():
                    break
                if recipient.selected and self.store.delivery(campaign.run_id, recipient.id).get("state") == "pending":
                    await queue.put(recipient)
                    self.max_queue_size = max(self.max_queue_size, queue.qsize())
            for _ in range(self.workers):
                await queue.put(None)

        async def worker():
            nonlocal cooldown
            while True:
                recipient = await queue.get()
                try:
                    if recipient is None:
                        return
                    if self.stop.is_set():
                        continue
                    email = recipient.values.get(campaign.email_column, "")
                    try:
                        mail = await asyncio.to_thread(prepare, campaign, recipient)
                    except ValueError as exc:
                        self.store.set_delivery(campaign.run_id, recipient.id, "failed", str(exc))
                        self.store.log(campaign.id, email, f"Failed validation: {exc}")
                        continue
                    retries = 0
                    while not self.stop.is_set():
                        attempts = self.store.delivery(campaign.run_id, recipient.id)["attempts"]
                        if not await rate_slot(recipient.id, email, attempts):
                            break
                        try:
                            result = await provider.submit(mail)
                        except Exception:
                            # Once submit() starts, an unexpected exception cannot prove nonacceptance.
                            self.store.set_delivery(campaign.run_id, recipient.id, "unknown", "Unexpected submission error; inspect Sent mail.")
                            self.store.log(campaign.id, email, "Unknown outcome; inspect provider Sent mail.")
                            self.state = "Paused — unexpected submission interruption"
                            self.stop.set()
                            break
                        if result.network_interrupted and result.state != "retry":
                            self.store.set_delivery(campaign.run_id, recipient.id, "unknown", result.detail)
                            self.store.log(campaign.id, email, result.detail)
                            self.state = "Paused — network interruption. Check your connection, then Resume pending."
                            self.stop.set()
                            break
                        if result.state == "retry":
                            self.store.set_delivery(campaign.run_id, recipient.id, "pending", result.detail)
                            if retries >= 3:
                                self.store.log(campaign.id, email, "Three retries exhausted; campaign paused. Recipient remains pending.")
                                self.state = ("Paused — network interruption after three retries. Resume pending when connected."
                                              if result.network_interrupted else "Paused — temporary rejection after three retries. Resume pending to continue.")
                                self.stop.set()
                                break
                            delay = max(result.retry_after, 2 ** retries + random.random())
                            retries += 1
                            cooldown = max(cooldown, time.monotonic() + delay)
                            self.store.log(campaign.id, email, f"Retry {retries}/3 scheduled in {delay:.1f}s · {result.detail}")
                            if not await wait_until(cooldown):
                                break
                            continue
                        if result.state in {"auth", "quota"}:
                            self.store.set_delivery(campaign.run_id, recipient.id, "pending", result.detail)
                            self.store.log(campaign.id, email, result.detail)
                            self.state = "Paused — sign-in required" if result.state == "auth" else "Paused — sending quota reached"
                            self.stop.set()
                            break
                        state = result.state if result.state in {"accepted", "failed", "unknown"} else "unknown"
                        self.store.set_delivery(campaign.run_id, recipient.id, state, result.detail)
                        self.store.log(campaign.id, email, result.detail or state)
                        break
                finally:
                    queue.task_done()
        try:
            rate = f"{60 / self.interval:g}/minute" if self.interval else "unlimited (simulation)"
            self.store.log(campaign.id, "", f"Sending started · {self.workers} workers · rate cap {rate}")
            await asyncio.gather(producer(), *(worker() for _ in range(self.workers)))
        finally:
            await provider.aclose()
