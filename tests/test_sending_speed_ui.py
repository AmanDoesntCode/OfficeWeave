import pytest
from playwright.sync_api import expect, sync_playwright

from mail_merger.desktop import LocalServer
from mail_merger.models import Recipient


@pytest.mark.ui
def test_speed_controls_save_estimate_and_keep_gmail_only(application):
    c = application.campaign
    c.recipients = [Recipient(str(i),{'Email':f'user{i}@example.com','Name':'Ada'}) for i in range(1000)]
    application.store.save(c)
    server = LocalServer(application)
    try:
        server.start()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page(viewport={'width':1280,'height':1000})
            page.goto(server.url)
            page.wait_for_function("document.querySelector('#mail-merger-shell')?.dataset.ready === 'true'")
            page.get_by_role('link', name='Review & send', exact=True).click()
            page.get_by_role('button', name='Sending speed', exact=False).click()
            page.get_by_label('Workers', exact=True).fill('8')
            page.get_by_label('Messages per minute', exact=True).fill('120')
            page.get_by_role('button',name='Apply sending speed',exact=True).click()
            expect(page.locator('#app-status')).to_have_text('Sending speed saved')
            expect(page.locator('#sending-speed-info')).to_contain_text('8 workers')
            expect(page.locator('#sending-speed-info')).to_contain_text('8m 20s')
            page.get_by_role('link',name='Campaign',exact=True).click()
            expect(page.get_by_role('combobox',name='Send using',exact=True)).to_be_disabled()
            expect(page.get_by_role('combobox',name='Send using',exact=True)).to_have_value('Gmail')
            page.get_by_role('link',name='Review & send',exact=True).click()
            expect(page.locator('#sending-speed-info')).to_contain_text('8 workers')
            expect(page.locator('#sending-speed-info')).to_contain_text('120 messages/minute')
            expect(page.locator('#sending-speed-info')).to_contain_text('8m 20s')
            page.reload()
            page.wait_for_function("document.querySelector('#mail-merger-shell')?.dataset.ready === 'true'")
            page.get_by_role('button', name='Sending speed', exact=False).click()
            expect(page.get_by_label('Workers',exact=True)).to_have_value('8')
            expect(page.get_by_label('Messages per minute',exact=True)).to_have_value('120')
            browser.close()
    finally:
        server.close()
