import pytest
import time

from mail_merger.models import Recipient
from mail_merger.models import Campaign
from mail_merger.spreadsheets import grouped_recipients


def test_distinct_uids_with_same_email_can_both_be_selected():
    headers = ['UID','Email','Subject']
    rows = [{'UID':'1','Email':'same@example.com','Subject':'DA'},
            {'UID':'2','Email':'SAME@example.com','Subject':'EE'},
            {'UID':'2','Email':'same@example.com','Subject':'CS'}]
    included = grouped_recipients(rows, headers, 'UID','Email', False)
    filtered = grouped_recipients(rows, headers, 'UID','Email', True)
    assert len(included) == len(filtered) == 2
    assert [r.selected for r in included] == [True,True]
    assert [r.selected for r in filtered] == [True,False]
    assert included[1].values['Subject'] == 'EE\nCS'


def test_toggle_preserves_manual_exclusions_and_invalid_rows(application):
    c = application.campaign
    c.recipients = [Recipient('1',{'Email':'same@example.com','Name':'Ada'}),
                    Recipient('2',{'Email':'SAME@example.com','Name':'Lin'}),
                    Recipient('3',{'Email':'other@example.com','Name':'Sam'},False),
                    Recipient('4',{'Email':'bad','Name':'Invalid'},False)]
    application.changed()
    assert not c.exclude_duplicate_emails
    application.set_duplicate_policy(True)
    assert [r.selected for r in c.recipients] == [True,False,False,False]
    assert application.store.load(c.id).exclude_duplicate_emails
    application.set_duplicate_policy(False)
    assert [r.selected for r in c.recipients] == [True,True,False,False]
    assert application.review_revision is None


def test_older_campaigns_expose_the_previous_filter_policy(campaign):
    data = campaign.to_dict()
    data.pop('exclude_duplicate_emails')
    assert Campaign.from_dict(data).exclude_duplicate_emails
    assert not Campaign().exclude_duplicate_emails


@pytest.mark.ui
def test_duplicate_filter_checkbox_and_select_valid_follow_user_policy(application):
    from playwright.sync_api import sync_playwright, expect
    from mail_merger.desktop import LocalServer
    c = application.campaign
    c.recipients[1].values['Email'] = 'one@example.com'
    application.store.save(c)
    server = LocalServer(application)
    try:
        server.start()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='msedge',headless=True)
            page = browser.new_page()
            page.goto(server.url)
            page.wait_for_function("document.querySelector('#mail-merger-shell')?.dataset.ready === 'true'")
            page.get_by_role('link', name='Recipients', exact=True).click()
            checkbox = page.get_by_label('Deselect duplicate email addresses',exact=True)
            expect(checkbox).not_to_be_checked()
            expect(page.get_by_label('Select row 2',exact=True)).to_be_checked()
            checkbox.check()
            expect(page.get_by_label('Select row 2',exact=True)).not_to_be_checked()
            page.get_by_role('button',name='Select valid',exact=True).click()
            expect(page.get_by_label('Select row 2',exact=True)).not_to_be_checked()
            checkbox.uncheck()
            expect(page.get_by_label('Select row 2',exact=True)).to_be_checked()
            page.get_by_role('button',name='Clear selection',exact=True).click()
            deadline = time.monotonic() + 8
            while application.campaign.recipients[1].selected and time.monotonic() < deadline:
                page.wait_for_timeout(20)
            assert not application.campaign.recipients[1].selected
            page.get_by_role('button',name='Select valid',exact=True).click()
            expect(page.get_by_label('Select row 2',exact=True)).to_be_checked()
            deadline = time.monotonic() + 8
            while not application.campaign.recipients[1].selected and time.monotonic() < deadline:
                page.wait_for_timeout(20)
            assert application.campaign.recipients[1].selected
            page.reload()
            page.wait_for_function("document.querySelector('#mail-merger-shell')?.dataset.ready === 'true'")
            expect(checkbox).not_to_be_checked()
            expect(page.get_by_label('Select row 2',exact=True)).to_be_checked()
            browser.close()
    finally:
        server.close()
