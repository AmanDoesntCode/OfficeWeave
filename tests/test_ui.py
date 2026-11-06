from pathlib import Path
import re

import pytest
from playwright.sync_api import expect, sync_playwright

from mail_merger.desktop import LocalServer


def assert_readable(page, selectors):
    failures = page.evaluate('''selectors => {
        const rgb = value => (value.match(/[\\d.]+/g) || []).map(Number);
        const luminance = rgb => rgb.slice(0,3).map(v => {v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4;}).reduce((n,v,i)=>n+v*[.2126,.7152,.0722][i],0);
        const failures=[];
        for (const selector of selectors) for (const node of document.querySelectorAll(selector)) {
            if (!node.getClientRects().length) continue;
            if (node.matches('input[type=checkbox], input[type=radio], input[type=file]')) continue;
            const style=getComputedStyle(node);
            let parent=node,bg=[255,255,255];
            while (parent) {const c=rgb(getComputedStyle(parent).backgroundColor);if(c.length===3 || c[3]===1){bg=c;break;} parent=parent.parentElement;}
            const fg=rgb(style.color), a=luminance(fg), b=luminance(bg), contrast=(Math.max(a,b)+.05)/(Math.min(a,b)+.05);
            if (contrast < 4.5) failures.push({selector,text:(node.innerText||node.value||'').slice(0,50),fg:style.color,bg,contrast});
        }
        return failures;
    }''', selectors)
    assert not failures, failures


@pytest.mark.ui
@pytest.mark.parametrize('color_scheme', ['light', 'dark'])
def test_import_editor_review_and_simulated_send(application, tmp_path, color_scheme):
    from mail_merger.sending import SenderEngine
    from test_sending import SimulatedProvider
    provider = SimulatedProvider()
    application.engine = SenderEngine(application.store, lambda c: provider, interval=0)
    server = LocalServer(application)
    errors = []
    approvals = []
    original_approve = application.approve
    def record_approval(checks, acknowledge):
        approvals.append((list(checks), application.review_revision, application.campaign.revision))
        return original_approve(checks, acknowledge)
    application.approve = record_approval
    try:
        server.start()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page(viewport={'width': 1280, 'height': 1000}, color_scheme=color_scheme)
            page.on('pageerror', lambda exc: errors.append(str(exc)))
            page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(server.origin) else route.abort())
            page.goto(server.url)
            if color_scheme == 'dark':
                page.goto(server.origin + '/?__theme=dark')
            page.wait_for_function("document.querySelector('#mail-merger-shell')?.dataset.ready === 'true'")
            page.get_by_role('link', name='Recipients', exact=True).click()
            expect(page.get_by_role('heading', name='Recipients', exact=True)).to_be_visible()
            expect(page.locator('.page-eyebrow, .editor-help, .menu-section-label')).to_have_count(0)
            assert_readable(page, ['.workspace-menu a span', '.sidebar-brand > span:last-child', '#page-recipients label', '#page-recipients button', '#page-recipients input', '.grid-count', '.recipient-grid td'])
            page.screenshot(path=f'test-results/recipients-{color_scheme}.png', full_page=True)
            expect(page.locator('.focused-page:visible')).to_have_count(1)
            cards = page.locator('.source-card').evaluate_all('(nodes) => nodes.map(node => {const r=node.getBoundingClientRect();return {width:r.width,height:r.height};})')
            assert len(cards) == 2
            assert abs(cards[0]['width'] - cards[1]['width']) <= 1
            assert abs(cards[0]['height'] - cards[1]['height']) <= 1
            expect(page.get_by_role('link', name='Recipients', exact=True)).to_have_attribute('aria-current', 'page')
            path = tmp_path / 'people.csv'
            path.write_text('Email,Name\none@example.com,Ada\ntwo@example.com,Lin\none@example.com,Duplicate\ninvalid,Invalid\n')
            page.locator('#recipient-upload input[type=file]').set_input_files(str(path))
            expect(page.get_by_text('Worksheets loaded', exact=True)).to_be_visible()
            page.get_by_label('Deselect duplicate email addresses', exact=True).check()
            page.get_by_role('button', name='Import', exact=True).click()
            expect(page.get_by_label('Unique ID column', exact=True)).to_be_visible()
            expect(page.locator('#recipient-results')).not_to_be_visible()
            page.get_by_role('combobox', name='Unique ID column', exact=True).click()
            page.get_by_role('option', name='Name', exact=True).click()
            page.get_by_role('button', name='Create recipients', exact=True).click()
            expect(page.get_by_text('4 recipients created from 4 rows', exact=True)).to_be_visible()
            expect(page.locator('.grid-count').first).to_have_text('2 selected / 4 recipients')
            page.get_by_role('link', name='Next: Compose', exact=False).click()
            expect(page.get_by_role('link', name='Compose', exact=True)).to_have_attribute('aria-current', 'page')
            expect(page.locator('#page-recipients')).not_to_be_visible()
            assert_readable(page, ['.editor-label', '.subject', '.rich', '.toolbar button', '.field-chips button'])
            subject = page.locator('.subject')
            subject.fill('Welcome ${Na')
            expect(page.locator('.suggestions')).to_be_visible()
            subject.press('Enter')
            expect(subject).to_have_value('Welcome ${Name}')
            rich = page.locator('.rich')
            rich.fill('Dear ${Na')
            rich.press('Enter')
            expect(rich).to_have_text('Dear ${Name}')
            page.locator('.field-chips button', has_text='${Name}').hover()
            expect(page.locator('.field-tooltip')).to_contain_text('Ada')
            page.locator('.source-toggle').click()
            expect(page.locator('.source')).to_be_visible()
            page.locator('.source').fill('<p>Hello <strong>${Name}</strong></p>')
            page.locator('.source-toggle').click()
            expect(rich.locator('strong')).to_have_text('${Name}')
            page.get_by_role('button', name='Save draft', exact=True).click()
            page.get_by_role('link', name='Recipients', exact=True).click()
            expect(page.locator('#page-compose')).not_to_be_visible()
            page.go_back()
            expect(subject).to_have_value('Welcome ${Name}')
            expect(rich.locator('strong')).to_have_text('${Name}')
            bounds = subject.bounding_box()
            assert bounds['x'] >= 224 and bounds['x'] + bounds['width'] <= 1280
            assert page.locator('#page-workspace').evaluate('(element) => element.scrollWidth <= element.clientWidth')
            screenshot = Path('test-results') / 'mail-merger-compose.png'
            screenshot.parent.mkdir(exist_ok=True)
            page.screenshot(path=str(screenshot), full_page=True)
            page.get_by_role('link', name='Next: Attachments', exact=False).click()
            shared_path = tmp_path / 'shared.txt'
            shared_path.write_text('Shared with everyone')
            group_path = tmp_path / 'group.txt'
            group_path.write_text('Only for Ada')
            page.get_by_label('Local attachment paths', exact=True).fill(str(shared_path) + '\n' + str(group_path))
            page.get_by_role('button', name='Add files', exact=True).click()
            expect(page.get_by_text('Files added. Assign each file before sending.', exact=True)).to_be_visible()
            page.get_by_role('checkbox', name=re.compile('^shared.txt')).check()
            page.get_by_label('Everyone selected', exact=True).check()
            page.get_by_role('button', name='Save file assignments', exact=True).click()
            expect(page.get_by_text('File assignments saved', exact=True)).to_be_visible()
            page.get_by_role('checkbox', name=re.compile('^group.txt')).check()
            page.get_by_label('Matching column values', exact=True).check()
            page.locator('#page-attachments').get_by_label('Name', exact=True).check()
            page.get_by_label('Name: Ada', exact=True).check()
            page.get_by_role('button', name='Save file assignments', exact=True).click()
            expect(page.locator('.assignment-table')).to_contain_text('Name = Ada')
            page.get_by_role('link', name='Review & send', exact=True).click()
            expect(page.get_by_text('Attachments: shared.txt, group.txt', exact=False)).to_be_visible()
            page.get_by_role('button', name='Check campaign', exact=True).click()
            expect(page.get_by_text('2 recipients ready', exact=True)).to_be_visible()
            expect(page.frame_locator('#page-review iframe[title="Email preview"]').locator('body')).to_contain_text('Hello Ada')
            for label in ['Recipients and email column', 'Merge fields and blank values', 'Subject and message', 'Attachment assignments']:
                page.get_by_label(label, exact=True).check()
            send = page.get_by_role('button', name='Send', exact=True)
            try:
                expect(send).to_be_enabled(timeout=10000)
            except AssertionError as exc:
                raise AssertionError({'approvals': approvals, 'status': page.locator('#app-status').inner_text(), 'browser_errors': errors}) from exc
            assert_readable(page, ['#page-review label', '#page-review button', '#page-review input', '#page-review textarea'])
            send.click()
            expect(page.locator('#send-terminal textarea')).to_have_value(re.compile('Finished'), timeout=20000)
            expect(page.locator('#campaign-outcome')).to_contain_text('Campaign complete')
            sizes = page.locator('.progress-card').evaluate_all('(nodes) => nodes.map(node => {const r=node.getBoundingClientRect();return [r.width,r.height];})')
            assert max(size[0] for size in sizes) - min(size[0] for size in sizes) <= 1
            assert max(size[1] for size in sizes) - min(size[1] for size in sizes) <= 1
            assert set(provider.sent) == {'one@example.com', 'two@example.com'}
            expect(page.get_by_text('2 recipients ready', exact=True)).to_be_visible()
            expect(page.frame_locator('#page-review iframe[title="Email preview"]').locator('body')).to_contain_text('Hello Ada')
            screenshot = Path('test-results') / 'mail-merger-review.png'
            screenshot.parent.mkdir(exist_ok=True)
            page.screenshot(path=str(screenshot), full_page=True)
            assert not errors, errors
            browser.close()
    finally:
        server.close()


@pytest.mark.ui
def test_signed_out_start_page_and_small_desktop_navigation(application):
    application.auth.accounts = lambda: {}
    server = LocalServer(application)
    try:
        server.start()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page(viewport={'width': 860, 'height': 720}, color_scheme='dark')
            page.goto(server.url)
            expect(page.get_by_role('heading', name='Accounts', exact=True)).to_be_visible(timeout=30000)
            expect(page.get_by_role('button', name='Sign in with Google', exact=True)).to_be_visible()
            expect(page.get_by_role('button', name='Sign in with Outlook', exact=True)).to_have_count(0)
            expect(page.get_by_role('button', name='Connect Google Sheets', exact=True)).to_have_count(0)
            expect(page.get_by_label('Google Sheets OAuth JSON', exact=True)).to_have_count(0)
            expect(page.get_by_role('link', name='Accounts', exact=True)).to_have_attribute('aria-current', 'page')
            assert_readable(page, ['.workspace-menu a span', '.sidebar-brand > span:last-child', '#page-accounts label', '#page-accounts button'])
            page.get_by_role('link', name='Compose', exact=True).click()
            expect(page.locator('.subject')).to_be_visible()
            expect(page.locator('#page-accounts')).not_to_be_visible()
            expect(page.locator('.focused-page:visible')).to_have_count(1)
            bounds = page.locator('.subject').bounding_box()
            assert bounds['x'] + bounds['width'] <= 860
            assert page.locator('#page-workspace').evaluate('(element) => element.scrollWidth <= element.clientWidth')
            page.go_back()
            expect(page.get_by_role('heading', name='Accounts', exact=True)).to_be_visible()
            browser.close()
    finally:
        server.close()


@pytest.mark.ui
def test_shared_google_sheet_link_imports_without_sign_in(application, monkeypatch):
    import httpx
    from contextlib import closing
    from test_spreadsheets import shared_workbook
    content = shared_workbook()
    application.auth.accounts = lambda: {}
    def no_oauth(*args, **kwargs):
        raise AssertionError('A shared Google Sheet must not request authentication')
    application.auth.token = application.auth.begin = no_oauth
    original_stream = httpx.stream
    downloads = []
    def download(method, url, **kwargs):
        if not url.startswith('https://docs.google.com/'):
            return original_stream(method, url, **kwargs)
        assert method == 'GET' and kwargs['params']['format'] == 'xlsx'
        assert 'headers' not in kwargs and 'auth' not in kwargs
        downloads.append(url)
        return closing(httpx.Response(200, content=content))
    monkeypatch.setattr(httpx, 'stream', download)
    server = LocalServer(application)
    try:
        server.start()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page(viewport={'width': 1280, 'height': 900})
            page.goto(server.url)
            page.wait_for_function("document.querySelector('#mail-merger-shell')?.dataset.ready === 'true'")
            page.get_by_role('link', name='Recipients', exact=True).click()
            page.get_by_label('Google Sheets sharing link', exact=True).fill('https://docs.google.com/spreadsheets/d/example/edit?usp=sharing')
            page.get_by_role('button', name='Load worksheets', exact=True).click()
            expect(page.get_by_text('Worksheets loaded', exact=True)).to_be_visible()
            page.get_by_role('combobox', name='Worksheet', exact=True).click()
            page.get_by_role('option', name="People's contacts", exact=True).click()
            page.get_by_role('button', name='Import', exact=True).click()
            expect(page.get_by_label('Unique ID column', exact=True)).to_be_visible()
            page.get_by_role('combobox', name='Unique ID column', exact=True).click()
            page.get_by_role('option', name='Email', exact=True).click()
            page.get_by_role('button', name='Create recipients', exact=True).click()
            expect(page.get_by_text('1 recipients created from 1 rows', exact=True)).to_be_visible()
            expect(page.locator('#page-recipients .recipient-grid td', has_text='00042')).to_be_visible()
            expect(page.locator('#page-recipients .recipient-grid td', has_text='01/11/2026')).to_be_visible()
            assert len(downloads) == 2
            assert not application.auth.accounts()
            browser.close()
    finally:
        server.close()


@pytest.mark.ui
def test_gmail_app_password_form_connects_without_oauth(application, monkeypatch):
    import smtplib
    from conftest import MemoryVault
    from test_gmail_app_password import FakeSMTP
    from mail_merger.auth import AuthService
    FakeSMTP.instances = []
    FakeSMTP.login_error = FakeSMTP.send_error = None
    FakeSMTP.refused = {}
    monkeypatch.setattr(smtplib, 'SMTP_SSL', FakeSMTP)
    application.auth = AuthService(application.store.root, MemoryVault())
    server = LocalServer(application)
    try:
        server.start()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page(viewport={'width': 1280, 'height': 900})
            page.goto(server.url)
            page.wait_for_function("document.querySelector('#mail-merger-shell')?.dataset.ready === 'true'")
            page.get_by_role('button', name='Sign in with Google', exact=True).click()
            email = page.get_by_label('Email address', exact=True)
            password = page.get_by_label('App password', exact=True)
            expect(email).to_be_visible()
            expect(password).to_have_attribute('type', 'password')
            email.fill('sender@example.com')
            password.fill('abcd efgh ijkl mnop')
            page.get_by_role('button', name='Sign in', exact=True).click()
            expect(page.get_by_text('Gmail connected: sender@example.com', exact=True)).to_be_visible()
            expect(page.locator('#gmail-sign-in')).not_to_be_visible()
            assert application.auth.accounts()['gmail'] == 'sender@example.com'
            assert not FakeSMTP.instances[0].messages
            expect(page.get_by_role('button', name='Sign in with Google', exact=True)).not_to_be_visible()
            expect(page.get_by_role('button', name='Sign in with Outlook', exact=True)).not_to_be_visible()
            expect(page.locator('#connection-status')).to_contain_text('Gmail: sender@example.com')
            page.screenshot(path='test-results/gmail-app-password.png', full_page=True)
            page.get_by_role('button', name='Sign out', exact=True).click()
            expect(page.get_by_role('button', name='Sign in with Google', exact=True)).to_be_visible()
            page.get_by_role('button', name='Sign in with Google', exact=True).click()
            expect(email).to_have_value('')
            expect(password).to_have_value('')
            expect(page.get_by_role('button', name='Sign in with Outlook', exact=True)).to_have_count(0)
            expect(page.get_by_text('OAuth settings', exact=True)).to_have_count(0)
            assert len(FakeSMTP.instances) == 1
            assert not application.auth.accounts()
            browser.close()
    finally:
        server.close()
