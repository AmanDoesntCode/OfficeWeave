from __future__ import annotations

import json
import os
import sqlite3
import threading
import weakref
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .models import Campaign
from .vault import Vault, VaultLocked, atomic_write


def data_directory() -> Path:
    if os.name == "nt":
        # SHGetFolderPath honours redirected/OneDrive Documents folders.
        import ctypes
        buffer = ctypes.create_unicode_buffer(32768)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buffer) == 0:
            return Path(buffer.value) / "Mail-Merger"
    return Path.home() / "Documents" / "Mail-Merger"


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    """One bounded SQLite connection; short transactions serialized by a lock."""

    def __init__(self, root: Path | None = None, vault=None):
        self.root = root or data_directory()
        self.root.mkdir(parents=True, exist_ok=True)
        self.vault = vault or Vault(self.root)
        if self.vault.locked:
            raise VaultLocked("Unlock encrypted history first.")
        self.records = self.root / "campaigns"
        self.records.mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.path = self.root / "mail-merger.sqlite3"
        # One bounded connection, serialized across UI and sender transactions.
        # Keeping WAL open avoids checkpointing the database on every log entry.
        self._connection = sqlite3.connect(self.path, timeout=15, check_same_thread=False)
        self._finalizer = weakref.finalize(self, self._connection.close)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS campaigns (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, data TEXT NOT NULL, updated TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS deliveries (
                    run TEXT NOT NULL, recipient TEXT NOT NULL, state TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0, detail TEXT NOT NULL DEFAULT '',
                    updated TEXT NOT NULL, PRIMARY KEY(run, recipient)
                );
                CREATE TABLE IF NOT EXISTS logs (
                    id INTEGER PRIMARY KEY, campaign TEXT NOT NULL, at TEXT NOT NULL,
                    recipient TEXT NOT NULL, message TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS logs_campaign ON logs(campaign, id);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS retry_claims (
                    campaign TEXT NOT NULL, recipient TEXT NOT NULL, child TEXT NOT NULL,
                    PRIMARY KEY(campaign,recipient)
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL, sent INTEGER NOT NULL DEFAULT 0
                );
            """)
            # A crash after dispatch may have happened after provider acceptance.
            db.execute("UPDATE deliveries SET state='unknown', detail=? WHERE state='submitting'",
                       (self.vault.seal("Application interrupted during submission. Inspect provider Sent mail before retrying.", "delivery-detail"),))
            # Upgrade old JSON-in-SQLite records without retaining duplicate rows.
            for identifier, data in db.execute("SELECT id,data FROM campaigns WHERE data!=''").fetchall():
                campaign = Campaign.from_dict(json.loads(data))
                self._write_record(campaign)
                db.execute("UPDATE campaigns SET data='' WHERE id=?", (identifier,))
        if self.vault.enabled and self.vault.config.get("migration_pending"):
            self._encrypt_existing()

    @contextmanager
    def connect(self):
        if self.vault.locked:
            raise VaultLocked("Unlock encrypted history first.")
        with self.lock, self._connection:
            yield self._connection

    def close(self):
        with self.lock:
            self._finalizer()

    def sending_preferences(self):
        with self.connect() as db:
            row = db.execute("SELECT data FROM settings WHERE key='sending'").fetchone()
            return json.loads(row[0]) if row else {"workers": 2, "per_minute": 30}

    def save_sending_preferences(self, preferences):
        with self.connect() as db:
            db.execute("INSERT INTO settings VALUES ('sending',?) ON CONFLICT(key) DO UPDATE SET data=excluded.data",
                       (json.dumps(preferences),))

    def save(self, campaign: Campaign) -> None:
        with self.lock, self.connect() as db:
            self._write_record(campaign)
            db.execute("INSERT INTO campaigns VALUES (?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                       "name=excluded.name, data=excluded.data, updated=excluded.updated",
                       (campaign.id, self.vault.seal(campaign.name, "campaign-name"), "", timestamp()))

    def _record_path(self, identifier):
        if not identifier or any(c not in "0123456789abcdef" for c in identifier):
            raise ValueError("Invalid campaign identifier.")
        return self.records / (identifier + ".json")

    def _write_record(self, campaign):
        from .attachments import files_for
        data = campaign.to_dict()
        # Keep only the grouped recipient values and raw templates, not the source
        # workbook or a second copy of the source rows. Materialise attachment
        # memberships while the source rows are still available.
        needs_memberships = any(not a.everyone and a.recipient_ids is None for a in campaign.attachment_assignments)
        try:
            memberships = {r.id: {f.digest for f in files_for(campaign, r.id)} for r in campaign.recipients} if needs_memberships else None
        except ValueError:
            memberships = None  # Review surfaces invalid assignments; saving must remain possible.
        for assignment in data["attachment_assignments"]:
            if not assignment["everyone"] and assignment["recipient_ids"] is None and memberships is not None:
                assignment["recipient_ids"] = [rid for rid, digests in memberships.items() if assignment["digest"] in digests]
        data.pop("source_rows", None)
        data.pop("source", None)
        value = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        if self.vault.enabled:
            value = json.dumps({"vault_id": self.vault.config["id"], "owner": self.vault.owner,
                                "encrypted": self.vault.seal(value, "campaign:" + campaign.id)})
        atomic_write(self._record_path(campaign.id), value.encode("utf-8"))

    def load(self, campaign_id: str) -> Campaign:
        with self.lock, self.connect() as db:
            row = db.execute("SELECT id FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
        if not row:
            raise ValueError("Campaign not found.")
        data = json.loads(self._record_path(campaign_id).read_text("utf-8"))
        if "encrypted" in data:
            data = json.loads(self.vault.open(data["encrypted"], "campaign:" + campaign_id))
        return Campaign.from_dict(data)

    def list_campaigns(self) -> list[tuple[str, str]]:
        with self.lock, self.connect() as db:
            rows = db.execute("SELECT name,id FROM campaigns ORDER BY updated DESC").fetchall()
            return [(self.vault.open(name, "campaign-name") + " · " + identifier[:6], identifier) for name, identifier in rows]

    def create_run(self, campaign: Campaign, recipient_ids: list[str]) -> str:
        run = uuid4().hex
        campaign.run_id = run
        with self.lock, self.connect() as db:
            db.executemany("INSERT INTO deliveries(run,recipient,state,updated) VALUES (?,?,'pending',?)",
                           [(run, rid, timestamp()) for rid in recipient_ids])
            self._write_record(campaign)
            db.execute("UPDATE campaigns SET updated=? WHERE id=?", (timestamp(), campaign.id))
        return run

    def delivery(self, run: str, recipient: str) -> dict:
        with self.lock, self.connect() as db:
            cursor = db.execute("SELECT * FROM deliveries WHERE run=? AND recipient=?", (run, recipient))
            row = cursor.fetchone()
            result = dict(zip((column[0] for column in cursor.description), row)) if row else {}
            if result:
                result["detail"] = self.vault.open(result["detail"], "delivery-detail")
            return result

    def set_delivery(self, run: str, recipient: str, state: str, detail: str = "", attempt: bool = False):
        with self.lock, self.connect() as db:
            db.execute("UPDATE deliveries SET state=?,detail=?,attempts=attempts+?,updated=? "
                       "WHERE run=? AND recipient=?", (state, self.vault.seal(detail, "delivery-detail"), int(attempt), timestamp(), run, recipient))

    def counts(self, run: str | None) -> dict[str, int]:
        with self.lock, self.connect() as db:
            return dict(db.execute("SELECT state,count(*) FROM deliveries WHERE run=? GROUP BY state", (run,)).fetchall())

    def cancel_pending(self, run: str):
        with self.lock, self.connect() as db:
            db.execute("UPDATE deliveries SET state='cancelled',updated=? WHERE run=? AND state='pending'",
                       (timestamp(), run))

    def log(self, campaign: str, recipient: str, message: str):
        # Only application-created summaries are logged, never HTTP headers/responses/tokens.
        with self.lock, self.connect() as db:
            db.execute("INSERT INTO logs(campaign,at,recipient,message) VALUES (?,?,?,?)",
                       (campaign, timestamp(), self.vault.seal(recipient, "log-recipient"), self.vault.seal(message, "log-message")))

    def logs(self, campaign: str, after: int = 0, limit: int = 300) -> list[tuple]:
        with self.lock, self.connect() as db:
            if after:
                rows = db.execute("SELECT id,at,recipient,message FROM logs WHERE campaign=? AND id>? "
                                  "ORDER BY id LIMIT ?", (campaign, after, limit)).fetchall()
            else:
                rows = list(reversed(db.execute("SELECT id,at,recipient,message FROM logs WHERE campaign=? "
                                            "ORDER BY id DESC LIMIT ?", (campaign, limit)).fetchall()))
            return [(identifier, at, self.vault.open(recipient, "log-recipient"), self.vault.open(message, "log-message"))
                    for identifier, at, recipient, message in rows]

    def log_page(self, campaign, page=1, size=250):
        with self.connect() as db:
            total = db.execute("SELECT count(*) FROM logs WHERE campaign=?", (campaign,)).fetchone()[0]
            pages = max(1, (total + size - 1) // size)
            page = max(1, min(int(page or 1), pages))
            rows = list(reversed(db.execute("SELECT id,at,recipient,message FROM logs WHERE campaign=? ORDER BY id DESC LIMIT ? OFFSET ?",
                                           (campaign, size, (page - 1) * size)).fetchall()))
            rows = [(identifier, at, self.vault.open(recipient, "log-recipient"), self.vault.open(message, "log-message"))
                    for identifier, at, recipient, message in rows]
            return rows, page, pages, total

    def deliveries(self, run):
        with self.connect() as db:
            return {rid: {"state": state, "attempts": attempts, "detail": self.vault.open(detail, "delivery-detail"), "updated": updated}
                    for rid, state, attempts, detail, updated in db.execute(
                        "SELECT recipient,state,attempts,detail,updated FROM deliveries WHERE run=?", (run,)).fetchall()}

    def dashboard(self):
        result = []
        for label, identifier in self.list_campaigns():
            campaign = self.load(identifier)
            counts = self.counts(campaign.run_id)
            result.append({"id": identifier, "name": campaign.name, "recipients": len(campaign.recipients),
                           "state": "Draft" if not campaign.run_id else "Paused" if counts.get("pending") else "Finished",
                           **{key: counts.get(key, 0) for key in ("accepted", "pending", "failed", "unknown", "cancelled")}})
        return result

    def retry_claims(self, campaign):
        with self.connect() as db:
            return dict(db.execute("SELECT recipient,child FROM retry_claims WHERE campaign=?", (campaign,)).fetchall())

    def claim_retry(self, campaign, recipients, child):
        with self.connect() as db:
            if any(db.execute("SELECT 1 FROM retry_claims WHERE campaign=? AND recipient=?", (campaign, rid)).fetchone() for rid in recipients):
                raise ValueError("A retry draft already exists for one of these recipients. Open that draft instead.")
            db.executemany("INSERT INTO retry_claims VALUES (?,?,?)", [(campaign, rid, child) for rid in recipients])

    def enable_encryption(self, owner, passphrase, sender, password):
        with self.lock:
            self.vault.enable(owner, passphrase, sender, password)
            self._encrypt_existing()

    def _encrypt_existing(self):
        """Restartable conversion with at most one campaign / 250 logs in memory."""
        with self.lock:
            identifiers = [identifier for _, identifier in self.list_campaigns()]
            for identifier in identifiers:
                self.save(self.load(identifier))
            with self.connect() as db:
                cursor = db.execute("SELECT id,recipient,message FROM logs")
                while batch := cursor.fetchmany(250):
                    for identifier, recipient, message in batch:
                        db.execute("UPDATE logs SET recipient=?,message=? WHERE id=?",
                                   (self.vault.seal(self.vault.open(recipient, "log-recipient"), "log-recipient"),
                                    self.vault.seal(self.vault.open(message, "log-message"), "log-message"), identifier))
                cursor = db.execute("SELECT run,recipient,detail FROM deliveries")
                while batch := cursor.fetchmany(250):
                    for run, rid, detail in batch:
                        db.execute("UPDATE deliveries SET detail=? WHERE run=? AND recipient=?",
                                   (self.vault.seal(self.vault.open(detail, "delivery-detail"), "delivery-detail"), run, rid))
                cursor = db.execute("SELECT id,payload FROM audit_events")
                while batch := cursor.fetchmany(250):
                    for identifier, payload in batch:
                        db.execute("UPDATE audit_events SET payload=? WHERE id=?",
                                   (self.vault.seal(self.vault.open(payload, "audit-event"), "audit-event"), identifier))
            # Remove superseded plaintext pages from this database after conversion.
            self._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._connection.execute("VACUUM")
            self._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self.vault.config["migration_pending"] = False
            atomic_write(self.vault.path, json.dumps(self.vault.config).encode("utf-8"))

    def add_audit(self, event):
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO audit_events(id,payload) VALUES (?,?)",
                       (event["event_id"], self.vault.seal(json.dumps(event), "audit-event")))

    def pending_audit(self, limit=25):
        with self.connect() as db:
            return [json.loads(self.vault.open(row[0], "audit-event")) for row in db.execute(
                "SELECT payload FROM audit_events WHERE sent=0 ORDER BY rowid LIMIT ?", (limit,)).fetchall()]

    def mark_audit_sent(self, identifier):
        with self.connect() as db:
            db.execute("UPDATE audit_events SET sent=1 WHERE id=?", (identifier,))

    def audit_rows(self):
        with self.connect() as db:
            cursor = db.execute("SELECT payload FROM audit_events ORDER BY rowid")
            while batch := cursor.fetchmany(100):
                for row in batch:
                    yield json.loads(self.vault.open(row[0], "audit-event"))
