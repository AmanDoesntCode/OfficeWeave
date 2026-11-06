"""Account-specific history folders and shared installation settings."""
import hashlib
import json
from pathlib import Path


def installation_root(root):
    root = Path(root)
    if root.parent.name == "accounts" and len(root.name) == 64 and all(c in "0123456789abcdef" for c in root.name):
        return root.parent.parent
    return root


def history_root(root, email):
    from .templates import address
    from .vault import Vault, atomic_write
    root = Path(root).resolve()
    email = address(email).casefold()
    digest = hashlib.sha256(email.encode()).hexdigest()
    profile = root / "accounts" / digest
    if (profile / "mail-merger.sqlite3").exists() or (profile / "protection.json").exists():
        return profile
    legacy = Vault(root)
    if legacy.enabled and legacy.owner.casefold() == email:
        return root
    marker = root / "history-owner.json"
    if not legacy.enabled and marker.exists():
        if json.loads(marker.read_text("utf-8")).get("email", "").casefold() == email:
            return root
    elif not legacy.enabled and (root / "mail-merger.sqlite3").exists():
        # Assign legacy plaintext history only when its recorded senders prove
        # ownership. Unknown drafts are retained, never exposed to a new account.
        import sqlite3
        from contextlib import closing
        senders = set()
        with closing(sqlite3.connect((root / "mail-merger.sqlite3").as_uri() + "?mode=ro", uri=True)) as db:
            for identifier, raw in db.execute("SELECT id,data FROM campaigns").fetchall():
                path = root / "campaigns" / (identifier + ".json")
                data = json.loads(raw) if raw else json.loads(path.read_text("utf-8"))
                if data.get("sender"):
                    senders.add(data["sender"].casefold())
        if senders == {email}:
            atomic_write(marker, json.dumps({"email": email}).encode())
            return root
    return profile
