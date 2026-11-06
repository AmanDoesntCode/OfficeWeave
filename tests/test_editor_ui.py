import re
import time

import pytest
from playwright.sync_api import expect, sync_playwright

from mail_merger.desktop import LocalServer


def select_body_text(page, text):
    page.locator('.rich').evaluate('''(element, text) => {
        element.focus(); const walker=document.createTreeWalker(element,NodeFilter.SHOW_TEXT);
        let node; while(node=walker.nextNode()) { const start=node.textContent.indexOf(text);
            if(start>=0) { const range=document.createRange(); range.setStart(node,start);range.setEnd(node,start+text.length);
                const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);
                document.dispatchEvent(new Event('selectionchange'));return;
            }
        } throw new Error('Text not found: '+text);
    }''', text)


@pytest.mark.ui
def test_column_suggestions_work_across_formatting_lists_subject_and_source(application):
    # Suggestions remain available even if fields still need review confirmation.
    application.campaign.fields = []
    server = LocalServer(application)
    try:
        server.start()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page(viewport={'width': 1100, 'height': 720})
            errors = []
            page.on('pageerror', lambda exc: errors.append(str(exc)))
            page.goto(server.url)
            page.wait_for_function("document.querySelector('#mail-merger-shell')?.dataset.ready === 'true'")
            page.get_by_role('link', name='Compose', exact=True).click()
            subject = page.locator('.subject')
            subject.fill('Hello ${Na')
            expect(page.get_by_role('option', name='Name', exact=True)).to_be_visible()
            subject.press('Enter')
            expect(subject).to_have_value('Hello ${Name}')
            page.get_by_role('button', name='Insert subject field', exact=True).click()
            expect(page.get_by_role('option', name='Email', exact=True)).to_be_visible()
            subject.press('Escape')
            page.locator('.source-toggle').click()
            page.locator('.source').fill('<ol><li><span>$</span><b>{</b></li></ol>')
            page.locator('.source-toggle').click()
            rich = page.locator('.rich')
            rich.locator('b').evaluate('''node => {
                node.closest('.rich').focus();const range=document.createRange();range.selectNodeContents(node);range.collapse(false);
                const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);
            }''')
            rich.press('N')
            option = page.get_by_role('option', name='Name', exact=True)
            expect(option).to_be_visible()
            assert option.evaluate('node => {const r=node.getBoundingClientRect();return node.contains(document.elementFromPoint(r.left+10,r.top+10));}')
            rich.press('Enter')
            expect(rich.locator('li')).to_have_text('${Name}')
            page.locator('.source-toggle').click()
            source = page.locator('.source')
            source.fill('<p>${Em')
            expect(page.get_by_role('option', name='Email', exact=True)).to_be_visible()
            source.press('Enter')
            expect(source).to_have_value('<p>${Email}')
            assert not errors
            browser.close()
    finally:
        server.close()


@pytest.mark.ui
def test_compose_formatting_controls_persist_through_source_preview_and_reload(application):
    server = LocalServer(application)
    try:
        server.start()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            errors = []
            page.on('pageerror', lambda exc: errors.append(str(exc)))
            page.goto(server.url)
            page.wait_for_function("document.querySelector('#mail-merger-shell')?.dataset.ready === 'true'")
            page.get_by_role('link', name='Compose', exact=True).click()
            rich = page.locator('.rich')
            rich.fill('Dear ${Name}, please submit your papers.')
            select_body_text(page, 'please submit')
            page.get_by_role('button', name='Bold', exact=True).click()
            select_body_text(page, 'please submit')
            page.get_by_role('button', name='Text and background color', exact=True).click()
            page.get_by_role('button', name='Text color #cc0000', exact=True).click()
            select_body_text(page, 'please submit')
            page.get_by_role('button', name='Text and background color', exact=True).click()
            page.get_by_role('button', name='Background color #fff2cc', exact=True).click()
            select_body_text(page, 'please submit')
            page.get_by_label('Font', exact=True).select_option('Georgia')
            select_body_text(page, 'please submit')
            page.get_by_label('Text size', exact=True).select_option('5')
            page.get_by_label('Alignment', exact=True).select_option('justifyCenter')
            select_body_text(page, 'papers')
            page.get_by_role('button', name='Insert link', exact=True).click()
            page.get_by_label('Link URL', exact=True).fill('https://example.com/exams')
            page.get_by_role('button', name='Apply', exact=True).click()
            expect(rich.locator('a')).to_have_attribute('href', 'https://example.com/exams')
            page.locator('.source-toggle').click()
            source = page.locator('.source')
            expect(source).to_have_value(re.compile('color: rgb\\(204, 0, 0\\)'))
            expect(source).to_have_value(re.compile('background-color: rgb\\(255, 242, 204\\)'))
            expect(source).to_have_value(re.compile('font-family: Georgia'))
            expect(source).to_have_value(re.compile('text-align: center'))
            page.locator('.source-toggle').click()
            page.get_by_role('button', name='Save draft', exact=True).click()
            deadline = time.monotonic() + 8
            while 'example.com/exams' not in application.campaign.body and time.monotonic() < deadline:
                page.wait_for_timeout(50)
            assert 'example.com/exams' in application.campaign.body
            assert 'Georgia' in application.campaign.body
            assert re.search(r'(font-weight:\s*bold|<b>|<strong>)', application.campaign.body)
            assert '24px' in application.campaign.body or 'x-large' in application.campaign.body
            page.reload()
            page.wait_for_function("document.querySelector('#mail-merger-shell')?.dataset.ready === 'true'")
            page.get_by_role('link', name='Compose', exact=True).click()
            expect(page.locator('.rich a')).to_have_attribute('href', 'https://example.com/exams')
            assert 'Georgia' in page.locator('.rich').inner_html()
            assert not errors
            page.screenshot(path='test-results/compose-toolbar.png', full_page=True)
            browser.close()
    finally:
        server.close()
