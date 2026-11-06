import re

import pytest
from playwright.sync_api import expect, sync_playwright

from mail_merger.desktop import LocalServer
from test_attachment_matching import setup_subjects


@pytest.mark.ui
def test_single_added_file_is_selected_and_save_tracks_selection(application, tmp_path):
    setup_subjects(application)
    attachment = tmp_path / 'syllabus.pdf'
    attachment.write_bytes(b'syllabus')
    server = LocalServer(application)
    try:
        server.start()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            page.goto(server.url)
            page.wait_for_function("document.querySelector('#mail-merger-shell')?.dataset.ready === 'true'")
            page.get_by_role('link', name='Attachments', exact=True).click()
            save = page.get_by_role('button', name='Save file assignments', exact=True)
            expect(save).to_be_disabled()
            page.get_by_label('Local attachment paths', exact=True).fill(str(attachment))
            page.get_by_role('button', name='Add files', exact=True).click()
            file = page.get_by_role('checkbox', name=re.compile(r'^syllabus\.pdf'))
            expect(file).to_be_checked()
            expect(save).to_be_enabled()
            file.uncheck()
            expect(save).to_be_disabled()
            file.check()
            page.locator('#page-attachments').get_by_label('Subject', exact=True).check()
            page.get_by_label('Subject: EE', exact=True).check()
            save.click()
            expect(page.locator('.assignment-table')).to_contain_text('Subject = EE')
            expect(save).to_be_disabled()
            expect(file).not_to_be_checked()
            assert application.campaign.attachment_assignments[0].matches == [['EE']]
            from mail_merger.attachments import files_for
            assert files_for(application.campaign, '1')
            assert not files_for(application.campaign, '2')
            assert files_for(application.campaign, '3')
            browser.close()
    finally:
        server.close()


@pytest.mark.ui
def test_single_library_assigns_subject_files_to_grouped_and_single_recipients(application, tmp_path):
    setup_subjects(application)
    paths = []
    for name in ('DA.pdf', 'EE.pdf'):
        path = tmp_path / name
        path.write_bytes(name.encode())
        paths.append(str(path))
    server = LocalServer(application)
    try:
        server.start()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            page.goto(server.url)
            page.wait_for_function("document.querySelector('#mail-merger-shell')?.dataset.ready === 'true'")
            page.get_by_role('link', name='Attachments', exact=True).click()
            expect(page.locator('#page-attachments input[type=file]')).to_have_count(0)
            expect(page.get_by_role('button', name='Choose files from this computer')).to_be_visible()
            page.get_by_label('Local attachment paths', exact=True).fill('\n'.join(paths))
            page.get_by_role('button', name='Add files', exact=True).click()
            expect(page.locator('.assignment-table')).to_contain_text('Not assigned')
            expect(page.locator('#attachment-notice')).to_contain_text('1 grouped recipient')
            save = page.get_by_role('button', name='Save file assignments', exact=True)
            expect(save).to_be_disabled()
            for subject in ('DA', 'EE'):
                expect(page.get_by_role('checkbox', name=re.compile('^' + subject + r'\.pdf'))).to_be_visible()
                page.get_by_role('checkbox', name=re.compile('^' + subject + r'\.pdf')).check()
                expect(save).to_be_enabled()
                page.locator('#page-attachments').get_by_label('Subject', exact=True).check()
                page.get_by_label('Subject: ' + subject, exact=True).check()
                page.get_by_role('button', name='Save file assignments', exact=True).click()
                expect(page.locator('.assignment-table')).to_contain_text('Subject = ' + subject)
                expect(save).to_be_disabled()
            expect(page.locator('.assignment-table')).not_to_contain_text('Not assigned')
            assert not application.campaign.shared
            page.screenshot(path='test-results/column-attachment-assignments.png', full_page=True)
            page.get_by_role('link', name='Review & send', exact=True).click()
            page.get_by_role('button', name='Check campaign', exact=True).click()
            expect(page.get_by_text('3 recipients ready', exact=True)).to_be_visible()
            body = page.frame_locator('#page-review iframe[title="Email preview"]').locator('body')
            expect(body.locator('ol > li')).to_have_count(3)
            expect(body.locator('ol > li').nth(0)).to_have_text('DA')
            expect(body.locator('ol > li').nth(1)).to_have_text('EE')
            expect(page.get_by_text('Attachments: DA.pdf, EE.pdf', exact=False)).to_be_visible()
            page.get_by_role('combobox', name='Preview recipient', exact=True).click()
            page.get_by_role('option', name=re.compile('da@example.com')).click()
            expect(page.get_by_text('Attachments: DA.pdf', exact=False)).to_be_visible()
            expect(page.get_by_text('Attachments: DA.pdf, EE.pdf', exact=False)).to_have_count(0)
            page.get_by_role('combobox', name='Preview recipient', exact=True).click()
            page.get_by_role('option', name=re.compile('ee@example.com')).click()
            expect(page.get_by_text('Attachments: EE.pdf', exact=False)).to_be_visible()
            page.reload()
            page.wait_for_function("document.querySelector('#mail-merger-shell')?.dataset.ready === 'true'")
            page.get_by_role('link', name='Attachments', exact=True).click()
            expect(page.locator('.assignment-table')).to_contain_text('Subject = DA')
            expect(page.locator('.assignment-table')).to_contain_text('Subject = EE')
            browser.close()
    finally:
        server.close()
