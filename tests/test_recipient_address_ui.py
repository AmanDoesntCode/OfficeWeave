import json

import pytest
from playwright.sync_api import expect, sync_playwright

from mail_merger.desktop import LocalServer
from mail_merger.models import Recipient
from mail_merger.templates import prepare


@pytest.mark.ui
def test_alternate_email_mapping_and_per_row_bcc_with_sticky_header(application):
    c = application.campaign
    c.headers = ['UID','Email','Name','Alternate email id','Long\nmultiline\nsubject\nheading']
    c.fields = c.headers.copy()
    c.recipients = [Recipient(str(i), {'UID':str(i),'Email':f'user{i}@example.com','Name':f'Professor {i}',
                                      'Alternate email id':f'alt{i}@example.com',c.headers[-1]:'DA\nEE'}) for i in range(1,41)]
    c.recipients[2].values['Alternate email id'] = ''
    application.store.save(c)
    server = LocalServer(application)
    try:
        server.start()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            page.goto(server.url)
            page.wait_for_function("document.querySelector('#mail-merger-shell')?.dataset.ready === 'true'")
            page.get_by_role('link', name='Recipients', exact=True).click()
            page.get_by_role('combobox', name='Alternate email column (optional)', exact=True).click()
            page.get_by_role('option', name='Alternate email id', exact=True).click()
            route = page.get_by_label('Alternate delivery for row 1', exact=True)
            expect(route).to_have_value('cc')
            blank_route = page.get_by_label('Alternate delivery for row 3', exact=True)
            expect(blank_route).to_be_disabled()
            expect(blank_route).to_contain_text('No alternate address')
            expect(page.get_by_label('Select row 3', exact=True)).to_be_checked()
            route.select_option('bcc')
            expect(page.locator('#app-status')).to_have_text('Recipients updated')
            assert application.campaign.recipients[0].alternate_mode == 'bcc'
            assert application.campaign.recipients[1].alternate_mode == 'cc'
            mail = prepare(application.campaign, application.campaign.recipients[0])
            assert b'alt1@example.com' not in mail.payload
            assert mail.envelope_to == ['user1@example.com','alt1@example.com']
            # Put a row checkbox underneath the tall sticky header and hit-test it.
            result = page.locator('.grid-scroll').evaluate('''scroll => {
                scroll.scrollIntoView({block:'center'});
                const head=scroll.querySelector('thead'),box=scroll.querySelector('tbody input');
                const initial=box.getBoundingClientRect(), header=head.getBoundingClientRect();
                scroll.scrollTop=initial.top-header.top-header.height/2;
                const covered=box.getBoundingClientRect();
                const hit=document.elementFromPoint(covered.left+covered.width/2,covered.top+covered.height/2);
                return {scroll:scroll.scrollTop,coveredByHeader:!!hit?.closest('thead')};
            }''')
            assert result['scroll'] > 0 and result['coveredByHeader'], result
            page.screenshot(path='test-results/recipient-header-scroll.png', full_page=True)
            page.get_by_role('link', name='Compose', exact=True).click()
            expect(page.locator('#page-compose').get_by_label('CC', exact=True)).to_have_count(0)
            expect(page.locator('#page-compose').get_by_label('BCC', exact=True)).to_have_count(0)
            page.get_by_role('link', name='Review & send', exact=True).click()
            expect(page.get_by_text('BCC: alt1@example.com', exact=False)).to_be_visible()
            page.reload()
            page.wait_for_function("document.querySelector('#mail-merger-shell')?.dataset.ready === 'true'")
            page.get_by_role('link', name='Recipients', exact=True).click()
            expect(page.get_by_label('Alternate delivery for row 1', exact=True)).to_have_value('bcc')
            expect(page.get_by_label('Alternate delivery for row 2', exact=True)).to_have_value('cc')
            browser.close()
    finally:
        server.close()
