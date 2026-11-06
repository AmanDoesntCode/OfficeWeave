# Build with: .venv\Scripts\python.exe -m PyInstaller Mail-Merger.spec
from PyInstaller.utils.hooks import collect_data_files, copy_metadata
from pathlib import Path
import re

version = re.search(r'^__version__ = "(\d+\.\d+\.\d+)"',
                    (Path(SPECPATH) / 'mail_merger' / '__init__.py').read_text('utf-8'), re.M)[1]
release_name = f'Mail-Merger-v{version}-Windows-x64'

datas = [("mail_merger/assets", "mail_merger/assets")]
for package in ("gradio", "gradio_client", "webview", "hf_gradio", "pythonnet", "clr_loader",
                "safehttpx", "groovy", "huggingface_hub", "google_auth_oauthlib",
                "openpyxl", "xlrd", "certifi"):
    # Gradio generates its event API by reading Python source at runtime.
    datas += collect_data_files(package, include_py_files=package in {"gradio", "gradio_client"})
datas += collect_data_files("mail_merger", include_py_files=True)
datas += copy_metadata("gradio", recursive=True)
for distribution in ("pywebview", "pythonnet", "google-auth", "google-auth-oauthlib"):
    datas += copy_metadata(distribution)

hiddenimports = [
    "webview.platforms.edgechromium", "webview.platforms.winforms",
    "google_auth_oauthlib.flow", "google.oauth2.credentials", "google.auth.transport.requests",
    "win32crypt", "openpyxl", "xlrd", "nh3", "filelock",
]
a = Analysis(["app.py"], pathex=[], binaries=[], datas=datas, hiddenimports=hiddenimports,
             hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=["playwright", "pytest", "tkinter"],
             noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Mail-Merger", debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=release_name)
