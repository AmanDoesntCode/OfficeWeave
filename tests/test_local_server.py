import httpx

from mail_merger.desktop import LocalServer


def test_local_only_session_and_private_files(application):
    server = LocalServer(application)
    try:
        server.start()
        with httpx.Client(follow_redirects=True, trust_env=False) as client:
            assert client.get(server.origin + '/config').status_code == 401
            assert client.get(server.url).status_code == 200
            config = client.get(server.origin + '/config')
            assert config.status_code == 200
            assert config.json()['analytics_enabled'] is False
            assert client.get(server.origin + '/config', headers={'Origin': 'https://evil.example'}).status_code == 403
            file_url = server.origin + '/gradio_api/file=' + str(application.store.path)
            assert client.get(file_url).status_code in {403, 404}
    finally:
        server.close()


def test_account_logout_revokes_browser_session(application):
    import time
    application.account_scoped = True
    signed_out = []
    application.auth.sign_out = lambda: signed_out.append(True)
    server = LocalServer(application)
    try:
        server.start()
        with httpx.Client(follow_redirects=True, trust_env=False) as client:
            client.get(server.url)
            assert client.post(server.origin + '/_account/sign-out').json() == {'ok': True}
            assert signed_out == [True]
            assert client.get(server.origin + '/config').status_code == 401
        deadline = time.monotonic() + 8
        while server.thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.05)
        assert not server.thread.is_alive()
    finally:
        server.close()  # Idempotent after the logout callback already closed it.
