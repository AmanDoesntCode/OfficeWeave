import re
import json

import pytest
from playwright.sync_api import expect, sync_playwright

from mail_merger.desktop import LocalServer


@pytest.mark.ui
@pytest.mark.parametrize('stale_editor', [False, True])
def test_save_renamed_campaign_then_open_the_selected_existing_campaign(application, stale_editor):
    if stale_editor:
        editor_value = application.editor_value
        def delayed_editor(*args):
            data = json.loads(editor_value(*args))
            data['campaign_id'] = 'previous-campaign-editor-not-yet-updated'
            return json.dumps(data)
        application.editor_value = delayed_editor
    server = LocalServer(application)
    try:
        server.start()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page(viewport={'width': 1280, 'height': 900})
            page.goto(server.url)
            page.wait_for_function("document.querySelector('#mail-merger-shell')?.dataset.ready === 'true'")
            page.get_by_role('link', name='Campaign', exact=True).click()
            first_id = application.campaign.id
            page.get_by_label('Campaign name', exact=True).fill('Semester papers')
            page.get_by_role('button', name='Save', exact=True).click()
            expect(page.locator('#app-status')).to_have_text('Saved Semester papers')
            expect(page.get_by_label('Campaign name', exact=True)).to_have_value('Semester papers')
            expect(page.get_by_role('combobox', name='Saved campaigns', exact=True)).to_have_value(re.compile('Semester papers'))
            assert application.store.load(first_id).name == 'Semester papers'
            assert len(application.store.list_campaigns()) == 1
            page.get_by_role('button', name='Open', exact=True).click()
            expect(page.get_by_label('Campaign name', exact=True)).to_have_value('Semester papers')
            assert application.campaign.id == first_id
            page.locator('#page-campaigns').get_by_role('button', name='New campaign', exact=True).click()
            expect(page.get_by_label('Campaign name', exact=True)).to_have_value('Untitled campaign')
            second_id = application.campaign.id
            assert second_id != first_id
            page.get_by_label('Campaign name', exact=True).fill('Second campaign')
            page.get_by_role('button', name='Save', exact=True).click()
            expect(page.locator('#app-status')).to_have_text('Saved Second campaign')
            picker = page.get_by_role('combobox', name='Saved campaigns', exact=True)
            picker.click()
            page.get_by_role('option', name=re.compile('^Semester papers')).click()
            page.get_by_role('button', name='Open', exact=True).click()
            expect(page.get_by_label('Campaign name', exact=True)).to_have_value('Semester papers')
            assert application.campaign.id == first_id
            assert len(application.store.list_campaigns()) == 2
            assert application.store.load(second_id).name == 'Second campaign'
            if not stale_editor:
                # Background content saves must not persist an old/unsaved name.
                page.get_by_label('Campaign name', exact=True).fill('Untitled campaign')
                page.get_by_role('link', name='Compose', exact=True).click()
                page.locator('.subject').fill('Name remains Semester papers')
                page.get_by_role('button', name='Save draft', exact=True).click()
                expect(page.locator('#app-status')).to_have_text('Saved')
                assert application.store.load(first_id).name == 'Semester papers'
            page.reload()
            page.wait_for_function("document.querySelector('#mail-merger-shell')?.dataset.ready === 'true'")
            expect(page.get_by_label('Campaign name', exact=True)).to_have_value('Semester papers')
            browser.close()
    finally:
        server.close()


def test_blank_name_cannot_replace_an_existing_campaign_name(application):
    old = application.campaign.name
    with pytest.raises(ValueError, match='campaign name'):
        application.rename_campaign('   ')
    assert application.store.load(application.campaign.id).name == old
