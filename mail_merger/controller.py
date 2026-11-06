from __future__ import annotations

import json
import threading
import math

from .attachments import copy_attachment, files_for
from .auth import AuthService
from .models import AttachmentAssignment, AttachmentGroup, Campaign
from .providers import GmailSMTPProvider
from .sending import SenderEngine
from .spreadsheets import GoogleSpreadsheet, LocalSpreadsheet, grouped_recipients, make_recipients, suggest_email_column
from .storage import Store
from .templates import address, fields_used, prepare, recipient_addresses, render, render_recipient_body, render_subject, sanitize


class Application:
    def __init__(self, store: Store | None = None, auth=None, engine=None):
        self.store = store or Store()
        self.auth = auth or AuthService(self.store.root)
        from .audit import AuditService
        self.audit = AuditService(self.store)
        self.sending_preferences = self.store.sending_preferences()
        self.engine = engine or SenderEngine(self.store, self._provider)
        self._default_engine = self.engine if engine is None else None
        self.lock = threading.RLock()
        campaigns = self.store.list_campaigns()
        self.campaign = self.store.load(campaigns[0][1]) if campaigns else Campaign()
        self.store.save(self.campaign)
        self.review_revision: int | None = None
        self.review_errors: list[str] = []
        self.review_blanks: list[str] = []
        self.review_sizes: dict[str, int] = {}
        self.source = ""
        self.source_kind = ""
        self.pending_snapshot = None
        self.migrate_attachment_library()

    def migrate_attachment_library(self):
        c = self.campaign
        if not c.run_id and (c.shared or c.groups):
            files = c.attachment_library + c.shared + [file for group in c.groups for file in group.files]
            c.attachment_library = list({file.digest: file for file in files}.values())
            c.shared, c.groups = [], []
            # Previous shared uploads must not silently remain assigned to everyone.
            self.changed()

    def editable(self):
        if self.engine.running:
            raise ValueError("Pause sending and wait for in-flight messages to finish first.")
        if self.campaign.run_id:
            raise ValueError("This campaign has a send history. Choose Edit as new draft to continue editing.")

    def run_summary(self):
        c = self.campaign
        counts = self.store.counts(c.run_id)
        running = self.engine.running and self.engine.campaign_id == c.id
        pending = counts.get("pending", 0) + counts.get("submitting", 0)
        if not c.run_id:
            title = "Ready to review"
        elif running:
            title = self.engine.state
        elif pending:
            title = (self.engine.state if self.engine.campaign_id == c.id and self.engine.state.startswith("Paused")
                     else "Campaign paused")
        elif counts.get("failed") or counts.get("unknown"):
            title = "Campaign finished with issues"
        elif counts.get("cancelled"):
            title = "Campaign closed"
        else:
            title = "Campaign complete"
        return {"title": title, "counts": counts, "running": running, "pending": pending,
                "has_history": bool(c.run_id)}

    def changed(self):
        self.campaign.revision += 1
        self.campaign.approved_revision = None
        self.review_revision = None
        self.review_errors = []
        self.review_blanks = []
        self.review_sizes = {}
        self.store.save(self.campaign)

    def switch(self, campaign_id: str):
        with self.lock:
            if self.engine.running:
                raise ValueError("Pause the current campaign before opening another one.")
            self.campaign = self.store.load(campaign_id)
            self.review_revision = None
            self.review_errors, self.review_blanks, self.review_sizes = [], [], {}
            self.source = ""
            self.source_kind = ""
            self.pending_snapshot = None
            self.migrate_attachment_library()

    def new(self, duplicate=False):
        with self.lock:
            if self.engine.running:
                raise ValueError("Pause sending before creating another campaign.")
            if duplicate:
                data = self.campaign.to_dict()
                fresh = Campaign()
                data.update(id=fresh.id, name=self.campaign.name + " (copy)", revision=0,
                            approved_revision=None, run_id=None)
                campaign = Campaign.from_dict(data)
                campaign.parent_id = self.campaign.id
            else:
                campaign = Campaign()
            self.campaign = campaign
            self.review_revision = None
            self.review_errors, self.review_blanks, self.review_sizes = [], [], {}
            self.source = ""
            self.source_kind = ""
            self.pending_snapshot = None
            self.store.save(campaign)
            self.migrate_attachment_library()

    def _provider(self, campaign):
        if campaign.provider != "gmail":
            raise ValueError("Microsoft sending has been removed. Create a Gmail draft to send.")
        return GmailSMTPProvider(campaign.sender, self.auth, workers=self.engine.workers)

    def reuse(self):
        with self.lock:
            self.new(duplicate=True)
            c = self.campaign
            c.name = c.name.removesuffix(" (copy)") + " (new send)"
            c.recipients, c.source_rows, c.source = [], [], ""
            c.requires_source = True
            c.shared, c.groups, c.attachment_library, c.attachment_assignments = [], [], [], []
            self.changed()

    def retry_draft(self, recipient_ids, acknowledge_unknown=False):
        with self.lock:
            if self.engine.running:
                raise ValueError("Pause sending before creating a retry draft.")
            states = self.store.deliveries(self.campaign.run_id)
            selected = set(recipient_ids)
            if selected & self.store.retry_claims(self.campaign.id).keys():
                raise ValueError("A retry draft already exists for these recipients. Open it from All campaigns.")
            allowed = {rid for rid, row in states.items() if row["state"] in {"failed", "cancelled", "unknown"}}
            # Invalid, never-submitted addresses in a draft may be corrected as well.
            if not self.campaign.run_id:
                allowed = {r.id for r in self.campaign.recipients if r.issues or not r.selected}
            if not selected or not selected <= allowed:
                raise ValueError("Select failed, cancelled, unknown, or invalid recipients. Accepted messages cannot be retried.")
            if any(states.get(rid, {}).get("state") == "unknown" for rid in selected) and not acknowledge_unknown:
                raise ValueError("Check Sent mail and confirm the unknown messages were not accepted before retrying.")
            parent = self.campaign.id
            self.new(duplicate=True)
            c = self.campaign
            c.name = c.name.removesuffix(" (copy)") + " (retry)"
            c.recipients = [r for r in c.recipients if r.id in selected]
            for recipient in c.recipients:
                recipient.selected = True
            c.parent_id, c.requires_source = parent, False
            self.changed()
            self.store.claim_retry(parent, selected, c.id)
            self.store.log(parent, "", f"Created retry draft {c.id} for {len(selected)} recipient(s); unknown outcomes explicitly checked: {bool(acknowledge_unknown)}.")

    def save_content(self, name, subject, body, cc, bcc, provider):
        with self.lock:
            if provider != "gmail":
                raise ValueError("Only Gmail sending is supported.")
            c = self.campaign
            account = self.auth.accounts().get(provider, "")
            new = (name.strip() or "Untitled campaign", subject, body, cc, bcc, provider, account)
            old = (c.name, c.subject, c.body, c.cc, c.bcc, c.provider, c.sender)
            if new == old:
                return
            self.editable()
            c.name, c.subject, c.body, c.cc, c.bcc, c.provider, c.sender = new
            self.changed()

    def rename_campaign(self, name: str):
        with self.lock:
            value = name.strip()
            if not value:
                raise ValueError("Enter a campaign name before saving.")
            if value != self.campaign.name:
                self.editable()
                self.campaign.name = value
                self.changed()
            else:
                self.store.save(self.campaign)

    def discover(self, source: str, kind: str):
        with self.lock:
            self.editable()
            self.pending_snapshot = None
            self.source = ""
            importer = LocalSpreadsheet() if kind == "file" else GoogleSpreadsheet()
            choices = importer.worksheets(source)
            self.source, self.source_kind = source, kind
            return choices

    def stage_rows(self, worksheet: str):
        with self.lock:
            self.editable()
            self.pending_snapshot = None
            if not self.source:
                raise ValueError("Choose a spreadsheet or fetch a Google Sheets link first.")
            importer = LocalSpreadsheet() if self.source_kind == "file" else GoogleSpreadsheet()
            headers, records = importer.read(self.source, worksheet)
            source = self.source if self.source_kind == "google" else "Local spreadsheet · " + worksheet
            self.pending_snapshot = (headers, records, source)
            return headers

    def build_recipients(self, uid_column: str, email_column: str, exclude_duplicates=None, collapse_columns=None):
        with self.lock:
            self.editable()
            if not self.pending_snapshot:
                raise ValueError("Load a worksheet before choosing its unique ID.")
            headers, records, source = self.pending_snapshot
            c = self.campaign
            policy = c.exclude_duplicate_emails if exclude_duplicates is None else bool(exclude_duplicates)
            old_fields = c.fields[:]
            old_alternate = c.alternate_email_column
            collapse = [h for h in headers if h in (c.collapse_columns if collapse_columns is None else collapse_columns) and h != email_column]
            recipients = grouped_recipients(records, headers, uid_column, email_column, policy, collapse)
            c.collapse_columns = collapse
            c.exclude_duplicate_emails = policy
            c.headers = headers
            c.fields = [h for h in headers if h in old_fields] if c.requires_source else headers.copy()
            c.email_column, c.uid_column = email_column, uid_column
            c.alternate_email_column = old_alternate if old_alternate in headers and old_alternate != email_column else ""
            c.source_rows, c.recipients, c.source = records, recipients, source
            c.groups = []
            c.attachment_assignments = []
            c.requires_source = False
            self.changed()
            self.pending_snapshot = None
            if self.source_kind == "file":
                from pathlib import Path
                uploaded = Path(self.source).resolve()
                cache = (self.store.root / "upload-cache").resolve()
                if cache in uploaded.parents and uploaded.is_file():
                    uploaded.unlink()
            self.source = ""

    def selection(self, selected: list[str], fields: list[str], email_column: str, alternate_modes=None):
        with self.lock:
            c = self.campaign
            if email_column not in c.headers:
                raise ValueError("Choose the recipient email column.")
            ids = set(selected)
            new_fields = [h for h in c.headers if h in fields]
            modes = alternate_modes or {}
            if any(mode not in {"cc", "bcc"} for mode in modes.values()):
                raise ValueError("Choose CC or BCC for alternate addresses.")
            modes_changed = any(r.id in modes and modes[r.id] != r.alternate_mode for r in c.recipients)
            if (ids == {r.id for r in c.recipients if r.selected} and new_fields == c.fields
                    and email_column == c.email_column and not modes_changed):
                return
            self.editable()
            c.email_column = email_column
            c.fields = new_fields
            for recipient in c.recipients:
                recipient.selected = recipient.id in ids
                recipient.alternate_mode = modes.get(recipient.id, recipient.alternate_mode)
            self.changed()

    def set_email_column(self, email_column):
        with self.lock:
            self.editable()
            if email_column == self.campaign.email_column:
                return
            if not self.campaign.source_rows and any(r.source_count > 1 for r in self.campaign.recipients):
                raise ValueError("Import the spreadsheet again before changing the primary email column for grouped recipients.")
            c = self.campaign
            modes = {r.id: r.alternate_mode for r in c.recipients}
            c.email_column = email_column
            if c.alternate_email_column == email_column:
                c.alternate_email_column = ""
            c.recipients = (grouped_recipients(c.source_rows, c.headers, c.uid_column, email_column, c.exclude_duplicate_emails, c.collapse_columns)
                            if c.uid_column and c.source_rows else
                            make_recipients([r.values for r in c.recipients], email_column, c.exclude_duplicate_emails))
            for recipient in c.recipients:
                recipient.alternate_mode = modes.get(recipient.id, "cc")
            self.changed()

    def set_collapse_columns(self, columns):
        with self.lock:
            self.editable()
            c = self.campaign
            if set(columns) - set(c.headers):
                raise ValueError('Choose available columns to collapse.')
            columns = [h for h in c.headers if h in columns and h != c.email_column]
            if columns == c.collapse_columns:
                return
            if not c.source_rows:
                raise ValueError('Reimport the spreadsheet to change which columns collapse. Saved snapshots cannot restore removed values.')
            previous = {r.id: r for r in c.recipients}
            c.recipients = grouped_recipients(c.source_rows, c.headers, c.uid_column, c.email_column, c.exclude_duplicate_emails, columns)
            for recipient in c.recipients:
                if recipient.id in previous:
                    old = previous[recipient.id]
                    recipient.selected = old.selected and not recipient.issues
                    recipient.alternate_mode = old.alternate_mode
                    if not recipient.issues:
                        recipient.values[c.email_column] = old.values[c.email_column]
            c.collapse_columns = columns
            self.changed()

    def set_duplicate_policy(self, enabled):
        with self.lock:
            enabled = bool(enabled)
            if self.campaign.exclude_duplicate_emails == enabled:
                return
            self.editable()
            self.campaign.exclude_duplicate_emails = enabled
            seen = set()
            for recipient in self.campaign.recipients:
                try:
                    email, _, _ = recipient_addresses(self.campaign, recipient)
                except ValueError:
                    continue
                key = email.casefold()
                if key in seen:
                    recipient.selected = not enabled and not recipient.issues
                seen.add(key)
            self.changed()

    def set_alternate_email_column(self, column):
        with self.lock:
            column = column or ""
            if column and (column not in self.campaign.headers or column == self.campaign.email_column):
                raise ValueError("Choose a different column for alternate email addresses.")
            if column == self.campaign.alternate_email_column:
                return
            self.editable()
            self.campaign.alternate_email_column = column
            self.changed()

    def correct_address(self, identifier, email):
        with self.lock:
            self.editable()
            recipient = next((r for r in self.campaign.recipients if r.id == identifier), None)
            if not recipient:
                raise ValueError("Select a recipient to correct.")
            recipient.values[self.campaign.email_column] = address(email)
            recipient.issues = []
            recipient.selected = True
            self.changed()

    def add_files(self, paths: list[str], group_name: str = "", members: list[str] | None = None):
        with self.lock:
            self.editable()
            if not paths:
                raise ValueError("Choose at least one attachment.")
            files = [copy_attachment(p, self.store.root, self.campaign.id) for p in paths]
            if group_name.strip():
                if not members:
                    raise ValueError("Choose at least one group member.")
                groups = self.campaign.groups
                groups[:] = [g for g in groups if g.name != group_name.strip()]
                groups.append(AttachmentGroup(group_name.strip(), list(members), files))
            else:
                self.campaign.shared = list({f.digest: f for f in self.campaign.shared + files}.values())
            self.changed()

    def upload_attachments(self, paths):
        with self.lock:
            self.editable()
            if not paths:
                raise ValueError("Choose at least one attachment.")
            files = [copy_attachment(path, self.store.root, self.campaign.id) for path in paths]
            self.campaign.attachment_library = list({file.digest: file for file in self.campaign.attachment_library + files}.values())
            self.changed()

    def attachment_targets(self, columns):
        if not columns or any(column not in self.campaign.headers for column in columns):
            return []
        if not self.campaign.source_rows and any(r.source_count > 1 for r in self.campaign.recipients):
            raise ValueError("Import the spreadsheet again to change column-based attachment assignments. Saved recipient assignments still work.")
        rows = self.campaign.source_rows or [r.values for r in self.campaign.recipients]
        return list(dict.fromkeys(tuple(row.get(column, "").strip() for column in columns) for row in rows))

    def assign_attachments(self, digests, everyone, columns, matches):
        with self.lock:
            self.editable()
            library = {file.digest for file in self.campaign.attachment_library}
            if not digests or not set(digests) <= library:
                raise ValueError("Select uploaded files to assign.")
            if not everyone:
                available = set(self.attachment_targets(columns))
                if not matches or not columns or any(tuple(value) not in available for value in matches):
                    raise ValueError("Choose columns and at least one matching value group.")
            self.campaign.attachment_assignments = [a for a in self.campaign.attachment_assignments if a.digest not in digests]
            self.campaign.attachment_assignments += [AttachmentAssignment(digest, everyone, [] if everyone else list(columns),
                                                                         [] if everyone else matches) for digest in digests]
            self.changed()

    def remove_library_files(self, digests):
        with self.lock:
            self.editable()
            self.campaign.attachment_library = [file for file in self.campaign.attachment_library if file.digest not in digests]
            self.campaign.attachment_assignments = [a for a in self.campaign.attachment_assignments if a.digest not in digests]
            self.changed()

    def clear_legacy_attachments(self):
        with self.lock:
            self.editable()
            self.campaign.shared, self.campaign.groups = [], []
            self.changed()

    def remove_files(self, shared_digests: list[str], group_names: list[str]):
        with self.lock:
            self.editable()
            self.campaign.shared = [f for f in self.campaign.shared if f.digest not in shared_digests]
            self.campaign.groups = [g for g in self.campaign.groups if g.name not in group_names]
            self.changed()

    def review(self):
        with self.lock:
            c = self.campaign
            if self.engine.running:
                raise ValueError("Pause sending before rebuilding review.")
            self.review_revision = None
            c.approved_revision = None
            self.review_errors, self.review_blanks, self.review_sizes = [], [], {}
            recipients = [r for r in c.recipients if r.selected]
            if not recipients:
                self.review_errors.append("Select at least one recipient.")
            assigned = {a.digest for a in c.attachment_assignments}
            unassigned = [file.name for file in c.attachment_library if file.digest not in assigned]
            if unassigned:
                self.review_errors.append("Assign or remove uploaded files: " + ", ".join(unassigned))
            try:
                self.auth.validate_sender(c.provider, c.sender)
            except Exception:
                self.review_errors.append("Sign in with the selected sender account before review.")
            try:
                used = fields_used(c.subject) | fields_used(c.body)
                unavailable = used - set(c.fields)
                if unavailable:
                    raise ValueError("Unconfirmed or missing merge fields: " + ", ".join(sorted(unavailable)))
            except ValueError as exc:
                self.review_errors.append(str(exc))
                used = set()
            for recipient in recipients:
                blanks = [field for field in used
                          if not recipient.values.get(field, "").strip()
                          or (recipient.source_count > 1 and any(not part.strip() for part in recipient.values.get(field, "").split("\n")))]
                if blanks:
                    self.review_blanks.append(f"Row {recipient.id}: {', '.join(sorted(blanks))}")
                try:
                    mail = prepare(c, recipient)
                    self.review_sizes[recipient.id] = len(mail.payload)
                except ValueError as exc:
                    self.review_errors.append(f"Row {recipient.id}: {exc}")
            self.review_revision = c.revision
            self.store.save(c)
            return self.review_errors, self.review_blanks

    def approve(self, checks: list[str], blanks_ack: bool):
        with self.lock:
            if self.review_revision != self.campaign.revision:
                raise ValueError("Check the campaign after your last change.")
            if self.review_errors:
                raise ValueError("Resolve the review errors before approving.")
            if len(set(checks)) != 4:
                raise ValueError("Confirm all four review checklist items.")
            if self.review_blanks and not blanks_ack:
                raise ValueError("Acknowledge the blank merge values before sending.")
            self.auth.validate_sender(self.campaign.provider, self.campaign.sender)
            self.campaign.approved_revision = self.campaign.revision
            self.store.save(self.campaign)

    def send(self):
        with self.lock:
            self.auth.validate_sender(self.campaign.provider, self.campaign.sender)
            if self.engine is self._default_engine:
                workers, rate = self.effective_sending_speed()
                self.engine.configure(workers, rate)
            self.engine.start(Campaign.from_dict(self.campaign.to_dict()))
            self.campaign = self.store.load(self.campaign.id)

    def effective_sending_speed(self):
        workers, rate = self.sending_preferences["workers"], self.sending_preferences["per_minute"]
        if self.campaign.provider == "outlook":
            return min(workers, 4), min(rate, 30)
        return workers, rate

    def configure_sending(self, workers, rate):
        with self.lock:
            try:
                workers, rate = float(workers), float(rate)
                if not all(math.isfinite(value) and value.is_integer() for value in (workers, rate)):
                    raise ValueError()
                if not 1 <= workers <= 8 or not 1 <= rate <= 300:
                    raise ValueError()
            except (ValueError, TypeError):
                raise ValueError("Choose 1–8 workers and 1–300 whole messages per minute.") from None
            if self.engine.running:
                raise ValueError("Pause sending and wait for in-flight messages before changing speed.")
            self.sending_preferences = {"workers": int(workers), "per_minute": int(rate)}
            effective_workers, effective_rate = self.effective_sending_speed()
            self.engine.configure(effective_workers, effective_rate)
            self.store.save_sending_preferences(self.sending_preferences)

    def sending_speed_info(self):
        workers, rate = self.effective_sending_speed()
        counts = self.store.counts(self.campaign.run_id)
        count = counts.get("pending", 0) if self.campaign.run_id else sum(r.selected for r in self.campaign.recipients)
        seconds = math.ceil(count * 60 / rate)
        estimate = f"{seconds // 60}m {seconds % 60:02d}s"
        return f"**{workers} workers · {rate} messages/minute.** {count:,} remaining: about {estimate} before provider delays."

    def preview(self, recipient_id: str) -> tuple[str, str, str]:
        with self.lock:
            c = self.campaign
            recipient = next((r for r in c.recipients if r.id == recipient_id), None)
            if not recipient:
                return "", "", "Import recipients to preview your message."
            try:
                subject = render_subject(c.subject, recipient.values, grouped=recipient.source_count > 1)
                body = render_recipient_body(c.body, recipient)
                files = files_for(c, recipient.id)
                info = "Attachments: " + (", ".join(f.name for f in files) or "None")
                if recipient.source_count > 1:
                    info += f" · {recipient.source_count} source rows combined; {len(files)} distinct matching attachments in this single email."
                size = self.review_sizes.get(recipient.id)
                if size is not None:
                    info += f" · Message size: {size:,} bytes"
                try:
                    to, cc, bcc = recipient_addresses(c, recipient)
                    info += "\n\nTo: " + to
                    if cc:
                        info += "\n\nCC: " + ", ".join(cc)
                    if bcc:
                        info += "\n\nBCC: " + ", ".join(bcc)
                except ValueError as exc:
                    info += "\n\nAddress needs correction: " + str(exc)
                return subject, body, info
            except ValueError as exc:
                return "", "", str(exc)

    def editor_value(self, recipient_id: str = "") -> str:
        c = self.campaign
        recipient = next((r for r in c.recipients if r.id == recipient_id), c.recipients[0] if c.recipients else None)
        return json.dumps({"campaign_id": c.id, "revision": c.revision,
                           "subject": c.subject, "body": sanitize(c.body), "fields": c.headers,
                           "values": recipient.values if recipient else {},
                           "samples": {h: [r.values.get(h, "") for r in c.recipients[:50]] for h in c.headers},
                           "locked": bool(c.run_id) or self.engine.running})
