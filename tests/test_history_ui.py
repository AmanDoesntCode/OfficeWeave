import re

import pytest
from playwright.sync_api import expect, sync_playwright

from mail_merger.desktop import LocalServer
from mail_merger.models import Recipient


@pytest.mark.ui
def test_dashboard_history_filters_preview_retry_and_reuse(application):
    c = application.campaign
    c.uid_column = "Name"
    c.recipients.append(Recipient("3", {"Email": "third@example.com", "Name": "Pat"}))
    application.store.save(c)
    run = application.store.create_run(c, ["1", "2", "3"])
    application.store.set_delivery(run, "1", "accepted", "Accepted by provider")
    application.store.set_delivery(run, "2", "failed", "Rejected temporarily")
    application.store.set_delivery(run, "3", "unknown", "Response interrupted")
    application.store.log(c.id, "two@example.com", "Recipient rejected")
    original = c.id
    server = LocalServer(application)
    try:
        server.start()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1100})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(server.url)
            page.wait_for_function("document.querySelector('#mail-merger-shell')?.dataset.ready === 'true'")
            expect(page.get_by_role("heading", name="Campaigns", exact=True)).to_be_visible()
            expect(page.locator(".campaign-entry")).to_have_count(1)
            expect(page.locator(".campaign-entry")).to_contain_text("1 accepted")
            page.get_by_role("button", name="View history", exact=True).click()
            expect(page.get_by_role("heading", name="Campaign history", exact=True)).to_be_visible()
            expect(page.get_by_label("Campaign log", exact=True)).to_have_value(re.compile("Recipient rejected"))
            page.get_by_role("combobox", name="Outcome", exact=True).click()
            page.get_by_role("option", name="Accepted", exact=True).click()
            expect(page.get_by_text("one@example.com", exact=True).first).to_be_visible()
            expect(page.locator("#page-history")).to_contain_text("Accepted by provider")
            page.get_by_role("combobox", name="Show column", exact=True).click()
            page.get_by_role("option", name="Name", exact=True).click()
            page.get_by_label("Filter column values", exact=True).fill("Ada")
            expect(page.locator("#page-history")).to_contain_text("Ada")
            page.get_by_role("combobox", name="View recipient message", exact=True).click()
            page.get_by_role("option", name="one@example.com", exact=True).click()
            expect(page.locator("#page-history").get_by_label("Subject", exact=True)).to_have_value("Hello Ada")
            expect(page.frame_locator('#page-history iframe[title="Email preview"]').locator("body")).to_contain_text("Hello Ada")
            page.screenshot(path="test-results/campaign-history.png", full_page=True)
            # Unknown recipients remain unselected until explicitly chosen and checked.
            page.get_by_role("checkbox", name=re.compile("^two@example.com")).check()
            page.get_by_role("button", name="Create retry draft", exact=True).click()
            expect(page.locator("#app-status")).to_contain_text("Retry draft created")
            assert application.campaign.parent_id == original
            assert [r.id for r in application.campaign.recipients] == ["2"]
            assert application.store.counts(run) == {"accepted": 1, "failed": 1, "unknown": 1}
            page.get_by_role("link", name="All campaigns", exact=True).click()
            page.get_by_role("button", name="Refresh campaigns", exact=True).click()
            expect(page.locator(".campaign-entry")).to_have_count(2)
            original_card = page.locator(".campaign-entry").filter(has=page.get_by_role("heading", name="Test campaign", exact=True))
            original_card.get_by_role("button", name="Reuse", exact=True).click()
            expect(page.locator("#app-status")).to_contain_text("Import a new spreadsheet")
            assert application.campaign.subject == "Hello ${Name}"
            assert application.campaign.requires_source and not application.campaign.recipients
            assert not errors, errors
            browser.close()
    finally:
        server.close()


@pytest.mark.ui
def test_encryption_setup_does_not_expose_secrets(application, monkeypatch):
    from test_vault import NoNetworkSMTP
    monkeypatch.setattr("smtplib.SMTP_SSL", NoNetworkSMTP)
    application.auth.gmail_credentials = lambda _: ("sender@example.com", "abcdefghijklmnop")
    server = LocalServer(application)
    try:
        server.start()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 1000})
            page.goto(server.url)
            page.wait_for_function("document.querySelector('#mail-merger-shell')?.dataset.ready === 'true'")
            page.get_by_role("link", name="Accounts", exact=True).click()
            page.get_by_text("History storage & encryption", exact=True).click()
            expect(page.get_by_label("Private-key passphrase", exact=True)).to_have_count(0)
            expect(page.get_by_label("Confirm passphrase", exact=True)).to_have_count(0)
            page.get_by_label("Code-sending Gmail account", exact=True).fill("codes@example.com")
            page.get_by_label("Code-sending account app password", exact=True).fill("abcdefghijklmnop")
            page.get_by_role("button", name="Encrypt history for my signed-in account", exact=True).click()
            expect(page.get_by_text("History encrypted for sender@example.com", exact=True)).to_be_visible()
            expect(page.get_by_label("Code-sending account app password", exact=True)).to_have_value("")
            assert application.store.vault.owner == "sender@example.com"
            assert "abcdefghijklmnop" not in application.store.vault.path.read_text()
            expect(page.get_by_role("button", name="Sign out and close history", exact=True)).to_be_visible()
            browser.close()
    finally:
        server.close()
