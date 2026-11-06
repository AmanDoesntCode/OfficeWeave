from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from playwright.sync_api import expect, sync_playwright

from mail_merger.closing import CloseBridge, CloseController
from mail_merger.ui import asset


def close_controller():
    application = SimpleNamespace(engine=Mock(running=False), store=Mock(), auth=Mock(), campaign=object())
    controller = CloseController(application)
    controller.window = Mock()
    controller.pending = True
    return controller, application


@pytest.mark.parametrize('choice,sign_out', [('stay_signed_in', False), ('sign_out', True)])
def test_close_waits_and_saves_before_optional_signout(choice, sign_out):
    controller, app = close_controller()
    order = []
    app.engine.shutdown.side_effect = lambda: order.append('pause')
    app.store.save.side_effect = lambda _: order.append('save')
    app.auth.sign_out.side_effect = lambda: order.append('signout')
    controller.window.destroy.side_effect = lambda: order.append('close')
    assert CloseBridge(controller).close_application(choice) == {'ok': True}
    assert order == ['pause', 'save'] + (['signout'] if sign_out else []) + ['close']
    assert controller.on_closing() is True


def test_cancel_keeps_app_and_credentials():
    controller, app = close_controller()
    assert controller.choose('cancel')['ok']
    assert not controller.pending
    app.auth.sign_out.assert_not_called()
    app.engine.shutdown.assert_not_called()
    controller.window.destroy.assert_not_called()
    assert not controller.choose('sign_out')['ok']


def test_inflight_timeout_never_removes_credentials_or_closes():
    controller, app = close_controller()
    app.engine.running = True
    assert not controller.choose('sign_out')['ok']
    assert not controller.allowed
    app.auth.sign_out.assert_not_called()
    controller.window.destroy.assert_not_called()


def test_failed_signout_keeps_window_open_for_retry():
    controller, app = close_controller()
    app.auth.sign_out.side_effect = OSError('dummy internal detail')
    result = controller.choose('sign_out')
    assert not result['ok'] and 'dummy' not in result['error']
    assert not controller.allowed and not controller.busy
    controller.window.destroy.assert_not_called()


@pytest.mark.ui
def test_dimmed_close_dialog_choices_keyboard_and_error():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='msedge', headless=True)
        page = browser.new_page(viewport={'width': 1100, 'height': 800})
        page.set_content('<button id="work">Work</button>')
        page.evaluate("window.choices=[]; window.pywebview={api:{close_application:async choice=>{choices.push(choice); return {ok:true}}}}")
        page.evaluate(asset('close-dialog.js'))
        dialog = page.get_by_role('dialog')
        expect(dialog).to_be_visible()
        expect(page.get_by_role('button', name='Stay signed in and close application')).to_be_focused()
        page.keyboard.press('Shift+Tab')
        expect(page.get_by_role('button', name='Keep working')).to_be_focused()
        page.keyboard.press('Escape')
        expect(dialog).not_to_be_visible()
        assert page.evaluate('choices') == ['cancel']
        page.evaluate(asset('close-dialog.js'))
        page.evaluate(asset('close-dialog.js'))
        expect(dialog).to_have_count(1)
        page.get_by_role('button', name='Sign out and close application').click()
        page.wait_for_function("choices.includes('sign_out')")
        page.evaluate("document.getElementById('mail-merger-close').remove(); window.pywebview.api.close_application=async()=>({ok:false,error:'Wait for sending to pause.'})")
        page.evaluate(asset('close-dialog.js'))
        page.get_by_role('button', name='Stay signed in and close application').click()
        expect(page.get_by_role('alert')).to_have_text('Wait for sending to pause.')
        expect(page.get_by_role('button', name='Keep working')).to_be_enabled()
        page.screenshot(path='test-results/close-dialog.png')
        browser.close()
