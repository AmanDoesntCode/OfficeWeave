from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Protocol
from uuid import uuid4


@dataclass
class Recipient:
    id: str
    values: dict[str, str]
    selected: bool = True
    source_count: int = 1
    issues: list[str] = field(default_factory=list)
    alternate_mode: str = "cc"
    uid_value: str = ""
    field_rows: dict[str, list[int]] = field(default_factory=dict)


@dataclass
class Attachment:
    name: str
    path: str
    digest: str
    size: int


@dataclass
class AttachmentGroup:
    name: str
    members: list[str] = field(default_factory=list)
    files: list[Attachment] = field(default_factory=list)


@dataclass
class AttachmentAssignment:
    digest: str
    everyone: bool = False
    columns: list[str] = field(default_factory=list)
    matches: list[list[str]] = field(default_factory=list)
    recipient_ids: list[str] | None = None


@dataclass
class Campaign:
    id: str = field(default_factory=lambda: uuid4().hex)
    name: str = "Untitled campaign"
    headers: list[str] = field(default_factory=list)
    fields: list[str] = field(default_factory=list)
    recipients: list[Recipient] = field(default_factory=list)
    email_column: str = ""
    alternate_email_column: str = ""
    exclude_duplicate_emails: bool = False
    source: str = ""
    uid_column: str = ""
    collapse_columns: list[str] = field(default_factory=list)
    source_rows: list[dict[str, str]] = field(default_factory=list)
    subject: str = ""
    body: str = "<p>Hello ${Name},</p><p></p>"
    cc: str = ""
    bcc: str = ""
    provider: str = ""
    sender: str = ""
    shared: list[Attachment] = field(default_factory=list)
    groups: list[AttachmentGroup] = field(default_factory=list)
    attachment_library: list[Attachment] = field(default_factory=list)
    attachment_assignments: list[AttachmentAssignment] = field(default_factory=list)
    revision: int = 0
    approved_revision: int | None = None
    run_id: str | None = None
    parent_id: str | None = None
    requires_source: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Campaign:
        data = dict(data)
        # Older drafts automatically excluded duplicate addresses. Reflect that
        # policy in the new checkbox so users can explicitly turn it off.
        data.setdefault("exclude_duplicate_emails", True)
        data["recipients"] = [Recipient(**r) for r in data.get("recipients", [])]
        data["shared"] = [Attachment(**a) for a in data.get("shared", [])]
        data["attachment_library"] = [Attachment(**a) for a in data.get("attachment_library", [])]
        data["attachment_assignments"] = [AttachmentAssignment(**a) for a in data.get("attachment_assignments", [])]
        data["groups"] = [
            AttachmentGroup(g["name"], g["members"], [Attachment(**a) for a in g["files"]])
            for g in data.get("groups", [])
        ]
        return cls(**data)


@dataclass
class PreparedMail:
    recipient_id: str
    to: str
    subject: str
    html: str
    attachments: list[Attachment]
    payload: bytes
    envelope_to: list[str] = field(default_factory=list)


@dataclass
class Submission:
    state: str
    detail: str = ""
    retry_after: float = 0
    network_interrupted: bool = False


class SpreadsheetImporter(Protocol):
    def worksheets(self, source: str) -> list[str]: ...
    def read(self, source: str, worksheet: str) -> tuple[list[str], list[dict[str, str]]]: ...


class TemplateRenderer(Protocol):
    def render(self, template: str, values: dict[str, str], html: bool = False) -> str: ...


class CredentialStore(Protocol):
    def read(self, key: str) -> dict | None: ...
    def write(self, key: str, value: dict) -> None: ...
    def delete(self, key: str) -> None: ...


class CampaignRepository(Protocol):
    def save(self, campaign: Campaign) -> None: ...
    def load(self, campaign_id: str) -> Campaign: ...


class MailProvider(Protocol):
    async def submit(self, mail: PreparedMail) -> Submission: ...
    async def aclose(self) -> None: ...
