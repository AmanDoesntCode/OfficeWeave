import re

import pytest
from playwright.sync_api import expect, sync_playwright

from mail_merger.desktop import LocalServer


@pytest.mark.ui
def test_new_campaign_clears_uploads_fields_editor_and_previous_recipient_events(application, tmp_path):
    path = tmp_path / 'recipients.csv'
    path.write_text('UID,Receiver,Name,Subject\n1,one@example.com,Ada,DA\n2,two@example.com,Lin,EE\n')
    attachment = tmp_path / 'syllabus.pdf'
    attachment.write_bytes(b'pdf')
    server = LocalServer(application)
    failures = []
    try:
        server.start()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page(viewport={'width': 1280, 'height': 900})
            def response_error(response):
                if '/gradio_api/' in response.url and response.status >= 400:
                    failures.append((response.status, response.url))
            page.on('response', response_error)
            page.goto(server.url)
            page.wait_for_function("document.querySelector('#mail-merger-shell')?.dataset.ready === 'true'")
            page.get_by_role('link', name='Recipients', exact=True).click()
            page.locator('#recipient-upload input[type=file]').set_input_files(str(path))
            expect(page.get_by_text('Worksheets loaded', exact=True)).to_be_visible()
            page.get_by_label('Google Sheets sharing link', exact=True).fill('https://docs.google.com/spreadsheets/d/old-source/edit')
            page.get_by_role('button', name='Import', exact=True).click()
            page.get_by_role('combobox', name='Unique ID column', exact=True).click()
            page.get_by_role('option', name='UID', exact=True).click()
            page.get_by_role('button', name='Create recipients', exact=True).click()
            expect(page.get_by_text('2 recipients created from 2 rows', exact=True)).to_be_visible()
            original_id = application.campaign.id
            page.get_by_role('link', name='Attachments', exact=True).click()
            page.get_by_label('Local attachment paths', exact=True).fill(str(attachment))
            page.get_by_role('button', name='Add files', exact=True).click()
            expect(page.get_by_role('checkbox', name=re.compile('^syllabus.pdf'))).to_be_visible()
            page.get_by_label('Everyone selected', exact=True).check()
            page.get_by_role('link', name='Compose', exact=True).click()
            page.locator('.subject').fill('Old subject')
            page.locator('.source-toggle').click()
            page.locator('.source').fill('<p>Old content ${Name}</p>')
            page.get_by_role('button', name='Save draft', exact=True).click()
            expect(page.locator('#app-status')).to_have_text('Saved')
            page.get_by_role('link', name='Campaign', exact=True).click()
            page.get_by_label('Campaign name', exact=True).fill('Old campaign')
            page.get_by_role('button', name='Save', exact=True).click()
            expect(page.locator('#app-status')).to_have_text('Saved Old campaign')
            # The previous Receiver choice used to generate the reported [] error.
            page.locator('#page-campaigns').get_by_role('button', name='New campaign', exact=True).click()
            expect(page.get_by_label('Campaign name', exact=True)).to_have_value('Untitled campaign')
            page.get_by_label('Campaign name', exact=True).fill('Fresh campaign')
            page.get_by_role('button', name='Save', exact=True).click()
            expect(page.locator('#app-status')).to_have_text('Saved Fresh campaign')
            expect(page.get_by_label('Campaign name', exact=True)).to_have_value('Fresh campaign')
            page.get_by_role('link', name='Recipients', exact=True).click()
            expect(page.locator('#recipient-upload')).not_to_contain_text('recipients.csv')
            expect(page.get_by_label('Google Sheets sharing link', exact=True)).to_have_value('')
            expect(page.get_by_role('combobox', name='Worksheet', exact=True)).to_have_value('')
            expect(page.locator('#column-mapping')).not_to_be_visible()
            expect(page.locator('#recipient-results')).not_to_be_visible()
            page.get_by_role('link', name='Attachments', exact=True).click()
            expect(page.locator('#attachment-upload')).not_to_contain_text('syllabus.pdf')
            expect(page.get_by_role('checkbox', name=re.compile('^syllabus.pdf'))).to_have_count(0)
            expect(page.get_by_label('Matching column values', exact=True)).to_be_checked()
            page.get_by_role('link', name='Compose', exact=True).click()
            expect(page.locator('.source')).not_to_be_visible()
            expect(page.locator('.rich')).to_be_visible()
            expect(page.locator('.subject')).to_have_value('')
            expect(page.locator('.rich')).not_to_contain_text('Old content')
            expect(page.locator('.field-chips button')).to_have_count(0)
            page.get_by_role('link', name='Review & send', exact=True).click()
            expect(page.locator('#send-terminal textarea')).to_have_value('')
            # Flush a timer tick and any callbacks caused by resetting controls.
            expect(page.locator('#sidebar-campaign')).to_have_text('Fresh campaign')
            page.wait_for_timeout(1200)
            expect(page.get_by_text(re.compile('Value: Receiver is not in the list of choices'))).to_have_count(0)
            expect(page.locator('.toast-body').filter(has_text='Error')).to_have_count(0)
            assert not failures
            fresh = application.campaign
            assert fresh.id != original_id and fresh.name == 'Fresh campaign'
            assert not fresh.recipients and not fresh.headers and not fresh.fields
            assert not fresh.attachment_library and not fresh.attachment_assignments
            assert not application.source and not application.source_kind and application.pending_snapshot is None
            assert application.review_revision is None and not application.review_sizes
            old = application.store.load(original_id)
            assert old.email_column == 'Receiver' and old.attachment_library
            browser.close()
    finally:
        server.close()
