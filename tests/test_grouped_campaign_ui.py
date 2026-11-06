import re

import pytest
from playwright.sync_api import expect, sync_playwright

from mail_merger.desktop import LocalServer
from mail_merger.sending import SenderEngine
from test_sending import SimulatedProvider


@pytest.mark.ui
def test_professor_gets_one_message_and_completed_campaign_can_continue(application, tmp_path):
    provider = SimulatedProvider()
    application.engine = SenderEngine(application.store, lambda c: provider, interval=0)
    path = tmp_path / 'professors.csv'
    path.write_text('UID,Email,Name,Subject\n' + ''.join(
        f'0007,ada@example.com,Professor Ada,{subject}\n' for subject in ('Algebra','Calculus','Statistics','Geometry')))
    server = LocalServer(application)
    try:
        server.start()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            page.goto(server.url)
            page.wait_for_function("document.querySelector('#mail-merger-shell')?.dataset.ready === 'true'")
            page.get_by_role('link', name='Recipients', exact=True).click()
            page.locator('#recipient-upload input[type=file]').set_input_files(str(path))
            expect(page.get_by_text('Worksheets loaded', exact=True)).to_be_visible()
            page.get_by_role('button', name='Import', exact=True).click()
            expect(page.locator('#column-mapping')).to_be_visible()
            expect(page.locator('#recipient-results')).not_to_be_visible()
            page.get_by_role('combobox', name='Unique ID column', exact=True).click()
            page.get_by_role('option', name='UID', exact=True).click()
            page.get_by_role('button', name='Create recipients', exact=True).click()
            expect(page.get_by_text('1 recipients created from 4 rows', exact=True)).to_be_visible()
            expect(page.locator('#page-recipients .grid-count')).to_have_text('1 selected / 1 recipients')
            expect(page.locator('#page-recipients .recipient-grid tbody tr')).to_have_count(1)
            page.get_by_role('link', name='Compose', exact=True).click()
            subject = page.locator('.subject')
            subject.fill('Question papers: ')
            page.get_by_role('button', name='Insert subject field', exact=True).click()
            page.get_by_role('option', name='Name', exact=True).click()
            expect(subject).to_have_value('Question papers: ${Name}')
            page.locator('.source-toggle').click()
            page.locator('.source').fill('<p>Dear ${Name},</p><p>Please submit your question papers for:</p><p>${Subject}</p>')
            page.locator('.source-toggle').click()
            page.get_by_role('button', name='Save draft', exact=True).click()
            page.get_by_role('link', name='Review & send', exact=True).click()
            page.get_by_role('button', name='Check campaign', exact=True).click()
            expect(page.get_by_text('1 recipients ready', exact=True)).to_be_visible()
            expect(page.get_by_role('textbox', name='Subject', exact=True)).to_have_value('Question papers: ' + ', '.join(['Professor Ada'] * 4))
            body = page.frame_locator('#page-review iframe[title="Email preview"]').locator('body')
            expect(body).to_contain_text('Algebra')
            expect(body).to_contain_text('Geometry')
            assert body.inner_text().count('Professor Ada') == 4
            for label in ['Recipients and email column', 'Merge fields and blank values', 'Subject and message', 'Attachment assignments']:
                page.get_by_label(label, exact=True).check()
            page.get_by_role('button', name='Send', exact=True).click()
            expect(page.locator('#send-terminal textarea')).to_have_value(re.compile('Campaign closed:'), timeout=15000)
            expect(page.locator('#campaign-outcome')).to_contain_text('Campaign complete')
            assert provider.sent == ['ada@example.com']
            original_id = application.campaign.id
            original_run = application.campaign.run_id
            # The same completion and a clear edit action survive reopening the app.
            page.reload()
            page.wait_for_function("document.querySelector('#mail-merger-shell')?.dataset.ready === 'true'")
            expect(page.locator('#campaign-outcome')).to_contain_text('Campaign complete')
            page.get_by_role('link', name='Compose', exact=True).click()
            expect(subject).to_be_disabled()
            page.get_by_role('button', name='Edit as new draft', exact=True).click()
            expect(subject).to_be_enabled()
            expect(subject).to_have_value('Question papers: ${Name}')
            subject.fill('Follow up: ${Name}')
            page.get_by_role('button', name='Save draft', exact=True).click()
            expect(page.locator('#campaign-outcome')).not_to_be_visible()
            assert application.campaign.id != original_id and not application.campaign.run_id
            assert application.store.load(original_id).run_id == original_run
            assert application.store.counts(original_run) == {'accepted': 1}
            page.screenshot(path='test-results/grouped-campaign.png', full_page=True)
            browser.close()
    finally:
        application.engine.shutdown()
        server.close()
