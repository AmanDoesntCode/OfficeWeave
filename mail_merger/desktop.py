from __future__ import annotations

import os
import secrets
import socket
import sys
import threading
import time
from pathlib import Path


def migrate_legacy_data(root):
    """Import the previous local database once; leave original files untouched."""
    import shutil
    import sqlite3
    from contextlib import closing
    from filelock import FileLock, Timeout
    legacy = Path(os.environ.get("LOCALAPPDATA", "")) / "Mail-Merger"
    if not legacy.is_absolute() or legacy.resolve() == root.resolve() or (root / "mail-merger.sqlite3").exists():
        return
    previous = legacy / "mail-merger.sqlite3"
    if not previous.is_file():
        return
    root.mkdir(parents=True, exist_ok=True)
    lock = FileLock(legacy / "desktop.lock", timeout=0)
    try:
        lock.acquire()
    except Timeout:
        raise ValueError("Close the older Mail-Merger instance before importing its history.") from None
    try:
        staged = root / "migration.sqlite3.tmp"
        with closing(sqlite3.connect(previous.as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(staged)) as target:
                source.backup(target)
        for directory in ("credentials", "config", "campaigns"):
            if (legacy / directory).is_dir():
                shutil.copytree(legacy / directory, root / directory, dirs_exist_ok=True)
        if (legacy / "protection.json").is_file():
            shutil.copy2(legacy / "protection.json", root / "protection.json")
        staged.replace(root / "mail-merger.sqlite3")
    finally:
        lock.release()
    # Old attachment copies have no reliable original source path. Existing
    # records keep those paths until users replace them; no new copies are made.


def configure_environment(root: Path):
    # PyInstaller's windowed bootloader has no console streams. Uvicorn and
    # third-party logging formatters still inspect isatty()/write() on them.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"
    os.environ["GRADIO_SHARE"] = "False"
    os.environ["GRADIO_SERVER_NAME"] = "127.0.0.1"
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["GRADIO_TEMP_DIR"] = str(root / "upload-cache")


def cleanup_upload_cache(root):
    """Remove only files generated inside this application's private upload cache."""
    root = Path(root).resolve()
    cache = (root / "upload-cache").resolve()
    if cache.parent != root or not cache.is_dir():
        return
    for entry in cache.rglob("*"):
        if not entry.is_symlink() and entry.is_file() and cache in entry.resolve().parents:
            try:
                entry.unlink()
            except OSError:
                pass  # An open upload can be cleaned at the next application start.


class LocalServer:
    def __init__(self, application):
        configure_environment(application.store.root)
        import gradio as gr
        import uvicorn
        from fastapi import FastAPI, Request
        from fastapi.responses import HTMLResponse, JSONResponse
        from .ui import ASSETS, asset, build_ui
        from .theme import application_theme

        self.application = application
        self._close_lock = threading.Lock()
        self._closed = False
        self.secret = secrets.token_urlsafe(32)
        self.cookie = secrets.token_urlsafe(32)
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.bind(("127.0.0.1", 0))
        self.port = self.socket.getsockname()[1]
        self.origin = f"http://127.0.0.1:{self.port}"
        self.url = self.origin + "/_bootstrap/" + self.secret
        api = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

        @api.middleware("http")
        async def local_session(request: Request, call_next):
            if request.url.path.startswith("/_bootstrap/"):
                return await call_next(request)
            if not secrets.compare_digest(request.cookies.get("mail_merger", ""), self.cookie):
                return JSONResponse({"detail": "Open Mail-Merger from its desktop launcher."}, status_code=401)
            origin = request.headers.get("origin")
            if origin and origin != self.origin:
                return JSONResponse({"detail": "Local session origin mismatch."}, status_code=403)
            response = await call_next(request)
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response

        @api.get("/_bootstrap/{token}")
        async def bootstrap(token: str):
            if not secrets.compare_digest(token, self.secret):
                return JSONResponse({"detail": "Invalid launch session."}, status_code=403)
            # Commit a same-origin document before navigating. A redirect directly
            # from the about:blank unlock screen can withhold a Strict cookie on
            # the redirected request, leaving the successfully unlocked app at 401.
            response = HTMLResponse('<!doctype html><html><head><meta charset="utf-8"><title>Mail-Merger</title></head><body><script>location.replace("/")</script></body></html>')
            response.set_cookie("mail_merger", self.cookie, httponly=True, samesite="strict")
            response.headers["Cache-Control"] = "no-store"
            return response

        @api.post("/_account/sign-out")
        async def sign_out_browser():
            if not getattr(application, "account_scoped", False):
                return JSONResponse({"ok": False}, status_code=400)
            if application.engine.running:
                return JSONResponse({"ok": False, "detail": "Pause sending first."}, status_code=409)
            application.audit.record("sign_out", application.auth.accounts().get("gmail", ""))
            application.auth.sign_out()
            self.cookie = secrets.token_urlsafe(32)
            timer = threading.Timer(.5, self.close)
            timer.daemon = True
            timer.start()
            return JSONResponse({"ok": True})

        self.demo = build_ui(application)
        self.api = gr.mount_gradio_app(api, self.demo, path="/", server_name="127.0.0.1", server_port=self.port,
                                       allowed_paths=[str(ASSETS.resolve())],
                                       blocked_paths=[str(application.store.root.resolve())],
                                       show_error=False, max_file_size="20mb", css=asset("app.css"),
                                       ssr_mode=False, mcp_server=False, footer_links=[], run_history=False,
                                       theme=application_theme())
        self.server = uvicorn.Server(uvicorn.Config(self.api, host="127.0.0.1", port=self.port,
                                                   access_log=False, log_level="error"))
        self.thread = threading.Thread(target=self.server.run, kwargs={"sockets": [self.socket]},
                                       daemon=True, name="mail-merger-local-ui")

    def start(self):
        self.thread.start()
        deadline = time.monotonic() + 30
        while not self.server.started:
            if not self.thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("The local interface could not start.")
            time.sleep(0.05)
        self.application.audit.record("app_open", self.application.auth.accounts().get("gmail", ""))
        self.application.audit.start()
        return self.url

    def close(self):
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        self.application.engine.shutdown()
        self.application.audit.record("app_close", self.application.auth.accounts().get("gmail", ""))
        self.application.audit.close()
        self.server.should_exit = True
        if self.thread.is_alive():
            self.thread.join(5)
        self.demo.close()
        self.socket.close()
        self.application.store.close()
        cleanup_upload_cache(self.application.store.root)


def main(argv=None):
    import argparse
    import json
    from filelock import FileLock, Timeout
    from .storage import Store, data_directory

    parser = argparse.ArgumentParser(description="Mail-Merger local desktop application")
    parser.add_argument("--browser", action="store_true", help="Open the local interface in a browser for development")
    parser.add_argument("--data-dir", type=Path, help="Override the local data directory")
    parser.add_argument("--smoke-test", action="store_true", help="Validate packaged startup in a hidden window, write smoke-result.json, and exit")
    args = parser.parse_args(argv)
    root = (args.data_dir or data_directory()).resolve()
    root.mkdir(parents=True, exist_ok=True)
    configure_environment(root)
    lock = FileLock(root / "desktop.lock", timeout=0)
    try:
        lock.acquire()
    except Timeout:
        raise SystemExit("Mail-Merger is already running for this data directory.")
    server = None
    result = {}
    try:
        from .controller import Application
        cleanup_upload_cache(root)
        from .vault import Vault
        if args.data_dir is None:
            migrate_legacy_data(root)
        if not args.browser and not args.smoke_test:
            from .startup import run_account_desktop
            run_account_desktop(root)
            return 0
        browser_auth = None
        history_directory = root
        if args.browser:
            from .startup import browser_account_session
            history_directory, vault, browser_auth = browser_account_session(root)
        else:
            vault = Vault(root)
        if vault.locked:
            if args.smoke_test:
                raise ValueError("Encrypted history must be unlocked interactively.")
            raise ValueError("Verify Gmail and the email code before opening history.")
        application = Application(Store(history_directory, vault=vault), browser_auth)
        application.account_scoped = bool(args.browser)
        server = LocalServer(application)
        url = server.start()
        if args.smoke_test:
            import httpx
            import webview
            with httpx.Client(follow_redirects=True, trust_env=False) as client:
                response = client.get(url)
                response.raise_for_status()
                config = client.get(server.origin + "/config")
                config.raise_for_status()
                result["components"] = len(config.json()["components"])
            window = webview.create_window("Mail-Merger", url, width=1000, height=800, hidden=True)
            def check_window():
                deadline = time.monotonic() + 30
                try:
                    while time.monotonic() < deadline:
                        ready = window.evaluate_js("!!document.querySelector('.recipient-grid .grid-search')")
                        if ready:
                            result["native_window"] = True
                            result["title"] = window.evaluate_js("document.title")
                            break
                        time.sleep(0.1)
                    if not result.get("native_window"):
                        result["error"] = "The native frontend did not become ready."
                except Exception as exc:
                    result["error"] = type(exc).__name__ + ": " + str(exc)
                finally:
                    window.destroy()
            window.events.loaded += check_window
            webview.start(gui="edgechromium", private_mode=True, debug=False)
            result["ok"] = bool(result.get("native_window")) and not result.get("error")
        elif args.browser:
            import webbrowser
            webbrowser.open(url)
            print("Mail-Merger is running locally. Press Ctrl+C to close.")
            while server.thread.is_alive():
                time.sleep(0.25)
        else:
            import webview
            from .closing import CloseBridge, CloseController
            closing = CloseController(application)
            window = webview.create_window("Mail-Merger", url, width=1240, height=900, min_size=(860, 650),
                                          background_color="#f5f7fb", text_select=True,
                                          js_api=CloseBridge(closing))
            closing.window = window
            window.events.closing += closing.on_closing
            from .otp_setup import attach_setup_prompt
            attach_setup_prompt(window, root)
            webview.start(gui="edgechromium", private_mode=True, debug=False)
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        if not args.smoke_test:
            raise
        result = {"ok": False, "error": type(exc).__name__ + ": " + str(exc)}
    finally:
        if server:
            server.close()
        lock.release()
        if args.smoke_test:
            (root / "smoke-result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0 if not args.smoke_test or result.get("ok") else 1
