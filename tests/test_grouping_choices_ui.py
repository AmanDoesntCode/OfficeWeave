import pytest
from playwright.sync_api import expect, sync_playwright

from mail_merger.desktop import LocalServer


@pytest.mark.ui
def test_column_choices_hover_cards_and_review_alignment(application, tmp_path):
    source = tmp_path / 'papers.csv'
    source.write_text('UID,Email,Name,Code,Paper\n'
                      '09013,professor@example.com,Professor,AI26509,Machine Learning\n'
                      '09013,professor@example.com,Professor,CS24303,Data Mining\n'
                      '09013,professor@example.com,Professor,IT335,Data Mining\n')
    server = LocalServer(application)
    try:
        server.start()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            page.goto(server.url)
            page.wait_for_function("document.querySelector('#mail-merger-shell')?.dataset.ready === 'true'")
            page.get_by_role('link', name='Recipients', exact=True).click()
            page.locator('#recipient-upload input[type=file]').set_input_files(str(source))
            expect(page.get_by_text('Worksheets loaded', exact=True)).to_be_visible()
            page.get_by_role('button', name='Import', exact=True).click()
            page.get_by_role('combobox', name='Unique ID column', exact=True).click()
            page.get_by_role('option', name='UID', exact=True).click()
            settings = page.locator('#grouping-settings')
            settings.get_by_label('Name', exact=True).check()
            page.get_by_role('button', name='Create recipients', exact=True).click()
            expect(page.get_by_text('1 recipients created from 3 rows', exact=True)).to_be_visible()
            cards = page.locator('#grouping-analytics .analytics-card')
            expect(cards).to_have_count(4)
            expect(cards.nth(0).locator('.analytics-count')).to_have_text('3')
            expect(cards.nth(2).locator('.analytics-count')).to_have_text('2')
            expect(cards.nth(3).locator('.analytics-count')).to_have_text('0')
            settings.get_by_label('Paper', exact=True).check()
            page.get_by_role('button', name='Apply grouping choices', exact=True).click()
            expect(cards.nth(3).locator('.analytics-count')).to_have_text('3')
            cards.nth(3).hover()
            expect(cards.nth(3).locator('.analytics-examples')).to_be_visible()
            expect(cards.nth(3).locator('.analytics-examples')).to_contain_text('IT335')
            expect(cards.nth(3).locator('.analytics-examples')).to_contain_text('Data Mining')
            page.get_by_role('link', name='Compose', exact=True).click()
            page.locator('.subject').fill('Papers')
            page.locator('.source-toggle').click()
            page.locator('.source').fill('<p>Hello ${Name}</p><ol><li>${Code}: ${Paper}</li></ol>')
            page.get_by_role('button', name='Save draft', exact=True).click()
            page.get_by_role('link', name='Review & send', exact=True).click()
            page.get_by_role('button', name='Check campaign', exact=True).click()
            expect(page.locator('#page-review').get_by_text('no longer match their source rows', exact=False)).to_be_visible()
            page.get_by_role('link', name='Recipients', exact=True).click()
            settings.get_by_label('Paper', exact=True).uncheck()
            page.get_by_role('button', name='Apply grouping choices', exact=True).click()
            expect(cards.nth(3).locator('.analytics-count')).to_have_text('0')
            page.get_by_role('link', name='Review & send', exact=True).click()
            page.get_by_role('button', name='Check campaign', exact=True).click()
            expect(page.get_by_text('1 recipients ready', exact=True)).to_be_visible()
            body = page.frame_locator('#page-review iframe[title="Email preview"]').locator('body')
            expect(body.locator('li')).to_have_count(3)
            assert body.inner_text().count('Professor') == 1
            expect(body.locator('li').nth(2)).to_have_text('IT335: Data Mining')
            page.get_by_role('link', name='Recipients', exact=True).click()
            page.screenshot(path='test-results/grouping-analytics.png', full_page=True)
            browser.close()
    finally:
        server.close()
