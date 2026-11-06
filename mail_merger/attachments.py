from __future__ import annotations

import hashlib
from pathlib import Path

from .models import Attachment, Campaign

MAX_REQUEST_BYTES = 4_000_000
MAX_ATTACHMENT_BYTES = 3_000_000


def copy_attachment(source: str, root: Path, campaign_id: str) -> Attachment:
    # Kept as an import-compatible name; attachments are references, never copies.
    path = Path(source).expanduser().resolve()
    if not path.is_file():
        raise ValueError("Attachment does not exist or is not a file.")
    if path.stat().st_size >= MAX_ATTACHMENT_BYTES:
        raise ValueError(f"{path.name}: each attachment must be smaller than 3 MB.")
    content = path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    return Attachment(path.name, str(path), digest, len(content))


def files_for(campaign: Campaign, recipient_id: str) -> list[Attachment]:
    files = list(campaign.shared)
    for group in campaign.groups:
        if recipient_id in group.members:
            files.extend(group.files)
    recipient = next((r for r in campaign.recipients if r.id == recipient_id), None)
    if recipient:
        source = ([row for row in campaign.source_rows
                   if row.get(campaign.uid_column, "").strip() == (recipient.uid_value or recipient.values.get(campaign.uid_column, "").strip())]
                  if campaign.uid_column and campaign.source_rows else [recipient.values])
        library = {file.digest: file for file in campaign.attachment_library}
        for assignment in campaign.attachment_assignments:
            if assignment.recipient_ids is not None:
                if recipient_id in assignment.recipient_ids:
                    if assignment.digest not in library:
                        raise ValueError("An assigned attachment is missing from the file library.")
                    files.append(library[assignment.digest])
                continue
            matches = {tuple(values) for values in assignment.matches}
            if assignment.everyone or any(tuple(row.get(column, "").strip() for column in assignment.columns) in matches
                                         for row in source):
                if assignment.digest not in library:
                    raise ValueError("An assigned attachment is missing from the file library.")
                files.append(library[assignment.digest])
    return list({file.digest: file for file in files}.values())


def verify_file(file: Attachment) -> bytes:
    path = Path(file.path)
    if not path.is_file():
        raise ValueError(f"Missing attachment: {file.name}")
    content = path.read_bytes()
    if len(content) != file.size or hashlib.sha256(content).hexdigest() != file.digest:
        raise ValueError(f"Attachment changed: {file.name}. Add it again and review.")
    return content
