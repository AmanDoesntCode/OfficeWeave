import asyncio
import threading
import re

import pytest
from playwright.sync_api import expect, sync_playwright

from mail_merger.desktop import LocalServer
from mail_merger.models import Recipient, Submission
from mail_merger.sending import SenderEngine


@pytest.mark.ui
def test_thousand_recipient_navigation_network_pause_and_resume(application):
    waiting, disconnect = threading.Event(), threading.Event()
    class FlakyTransport:
        def __init__(self):
            self.calls = 0
            self.interrupted = None
            self.accepted = []
        async def submit(self, mail):
            self.calls += 1
            number = self.calls
            if number == 10:
                self.interrupted = mail.to
                waiting.set()
                while not disconnect.is_set():
                    await asyncio.sleep(.01)
                return Submission('unknown', 'Network interrupted during submission. Inspect Sent mail.', network_interrupted=True)
            await asyncio.sleep(.005)
            self.accepted.append(mail.to)
            return Submission('accepted', 'Accepted by simulated provider')
        async def aclose(self):
            pass
    provider = FlakyTransport()
    application.campaign.recipients = [Recipient(str(i), {'Email': f'user{i}@example.com', 'Name': f'Person {i}'}) for i in range(1000)]
    application.campaign.approved_revision = application.campaign.revision
    application.store.save(application.campaign)
    application.engine = SenderEngine(application.store, lambda c: provider, interval=0)
    server = LocalServer(application)
    try:
        server.start()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page(viewport={'width': 1280, 'height': 900})
            page.goto(server.url)
            page.wait_for_function("document.querySelector('#mail-merger-shell')?.dataset.ready === 'true'")
            application.send()
            assert waiting.wait(10)
            assert application.engine.running
            for title in ('Compose', 'Attachments', 'Recipients', 'Review & send'):
                page.get_by_role('link', name=title, exact=True).click()
                expect(page.get_by_role('heading', name=title, exact=True)).to_be_visible()
            assert application.engine.running
            disconnect.set()
            expect(page.locator('#campaign-outcome')).to_contain_text('network interruption', timeout=15000)
            expect(page.locator('#send-terminal textarea')).to_have_value(re.compile('Network interrupted'))
            resume = page.get_by_role('button', name='Resume pending', exact=True)
            expect(resume).to_be_enabled()
            resume.click()
            expect(page.locator('#campaign-outcome')).to_contain_text('Campaign finished with issues', timeout=60000)
            assert application.store.counts(application.campaign.run_id) == {'accepted': 999, 'unknown': 1}
            assert len(provider.accepted) == len(set(provider.accepted)) == 999
            assert provider.interrupted not in provider.accepted
            assert application.engine.max_queue_size <= 100
            browser.close()
    finally:
        disconnect.set()
        server.close()
