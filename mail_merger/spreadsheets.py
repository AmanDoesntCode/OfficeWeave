from __future__ import annotations

import csv
import datetime
import io
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zipfile import BadZipFile, ZipFile

import httpx

from .models import Recipient
from .templates import address

MAX_RECIPIENTS = 1000


def column_letter(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def normalized_rows(rows) -> tuple[list[str], list[dict[str, str]]]:
    rows = iter(rows)
    headers = [str(v).strip() if v is not None else "" for v in next(rows, [])]
    columns = [(i, header) for i, header in enumerate(headers) if header]
    if not columns:
        raise ValueError("The first row has no column headings. Put the column names in row 1.")
    names = [header for _, header in columns]
    if len(set(names)) != len(names):
        raise ValueError("Column headers must be unique after trimming whitespace.")
    if any("{" in h or "}" in h for h in names):
        raise ValueError("Column headers cannot contain braces; these delimit merge fields.")
    records = []
    for row_number, row in enumerate(rows, 2):
        if not any(str(v).strip() for v in row if v is not None):
            continue
        for i, value in enumerate(row):
            if (i >= len(headers) or not headers[i]) and value is not None and str(value).strip():
                letter = column_letter(i + 1)
                raise ValueError(f"Column {letter} has data at {letter}{row_number} but is missing its header. "
                                 f"Add a heading in {letter}1. Entirely empty spacer columns are allowed.")
        records.append({h: str(row[i]).replace("\r\n", "\n").replace("\r", "\n") if i < len(row) and row[i] is not None else ""
                        for i, h in columns})
        if len(records) > MAX_RECIPIENTS:
            raise ValueError("The first release supports up to 1,000 recipients per campaign.")
    if not records:
        raise ValueError("The worksheet contains headers but no recipient rows.")
    return names, records


def suggest_email_column(headers: list[str]) -> str:
    for candidate in ("receiver", "email", "email address"):
        for header in headers:
            if header.casefold() == candidate:
                return header
    return headers[0] if headers else ""


def make_recipients(records: list[dict[str, str]], email_column: str, deselect_duplicates: bool = True) -> list[Recipient]:
    seen = set()
    recipients = []
    for i, values in enumerate(records, 1):
        try:
            email = address(values.get(email_column, "")).casefold()
            selected = not deselect_duplicates or email not in seen
            seen.add(email)
        except ValueError:
            selected = False
        recipients.append(Recipient(str(i), values, selected))
    return recipients


def grouped_recipients(records: list[dict[str, str]], headers: list[str], uid_column: str,
                       email_column: str, deselect_duplicates: bool = True,
                       collapse_columns: list[str] | None = None) -> list[Recipient]:
    if uid_column not in headers:
        raise ValueError("Select a unique ID column before creating recipients.")
    if email_column not in headers:
        raise ValueError("Select the recipient email column.")
    collapse = set(collapse_columns or []) - {email_column}
    if collapse - set(headers):
        raise ValueError("Choose available columns to collapse.")
    groups: dict[str, list[dict[str, str]]] = {}
    for index, row in enumerate(records, 2):
        uid = row.get(uid_column, "").strip()
        if not uid:
            raise ValueError(f"Missing unique ID on spreadsheet row {index}. Fill it before importing.")
        groups.setdefault(uid, []).append(row)
    merged, problems, positions = [], [], []
    for uid, rows in groups.items():
        values = {}
        issues = []
        field_rows = {}
        for header in headers:
            # Keep each source row in order, including repeated and blank cells.
            # Deduplicating columns independently destroys code/name pairings.
            cells = [row.get(header, "").strip() for row in rows]
            if header in collapse:
                first = {}
                for index, value in enumerate(cells):
                    first.setdefault(value, index)
                values[header] = "\n".join(first)
                field_rows[header] = list(first.values())
            else:
                values[header] = "\n".join(cells)
        emails = {}
        for row in rows:
            raw = row.get(email_column, "").strip()
            try:
                normalized = address(raw).casefold()
                emails.setdefault(normalized, normalized)
            except ValueError:
                issues.append("Invalid or missing email in this ID's source rows")
        if len(emails) > 1:
            issues.append("Conflicting email addresses for this unique ID")
        if len(emails) == 1:
            values[email_column] = next(iter(emails))
        merged.append(values)
        positions.append(field_rows)
        problems.append((len(rows), list(dict.fromkeys(issues))))
    recipients = make_recipients(merged, email_column, deselect_duplicates)
    for recipient, uid, field_rows, (count, issues) in zip(recipients, groups, positions, problems):
        recipient.uid_value = uid
        recipient.field_rows = field_rows
        recipient.source_count, recipient.issues = count, issues
        if issues:
            recipient.selected = False
    return recipients


def sheet_id(url: str) -> str:
    parsed = urlparse(url)
    match = re.fullmatch(r"/spreadsheets/d/([a-zA-Z0-9_-]+)(?:/.*)?", parsed.path)
    if parsed.scheme != "https" or parsed.hostname != "docs.google.com" or not match:
        raise ValueError("Paste an https://docs.google.com/spreadsheets/d/... URL.")
    return match[1]


def displayed_value(value, fmt="General") -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    fmt = fmt or "General"
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        if fmt == "General":
            return value.isoformat(sep=" ") if isinstance(value, datetime.datetime) else value.isoformat()
        pattern = re.sub(r"\[[^]]+\]", "", fmt.split(";")[0])
        parts = re.findall(r'"[^"]*"|\\.|AM/PM|yyyy|yy|mmmm|mmm|mm|m|dddd|ddd|dd|d|hh|h|ss|s|.', pattern, re.I)
        twelve_hour = bool(re.search("AM/PM", pattern, re.I))
        minute = bool(re.search(r"[hs]", pattern, re.I) and not re.search(r"[yd]", pattern, re.I))
        rendered = []
        for part in parts:
            token = part.casefold()
            if token.startswith('"') or token.startswith('\\'):
                rendered.append(part.strip('"').lstrip('\\'))
                continue
            if token in {"h", "hh"}:
                minute = True
                hour = getattr(value, "hour", 0)
                hour = (hour % 12 or 12) if twelve_hour else hour
                rendered.append(str(hour).zfill(2) if token == "hh" else str(hour))
            elif token in {"m", "mm"}:
                number = getattr(value, "minute", 0) if minute else getattr(value, "month", 1)
                rendered.append(str(number).zfill(2) if token == "mm" else str(number))
            elif token in {"s", "ss"}:
                number = getattr(value, "second", 0)
                rendered.append(str(number).zfill(2) if token == "ss" else str(number))
            elif token == "am/pm":
                rendered.append("AM" if getattr(value, "hour", 0) < 12 else "PM")
            elif token in {"yyyy", "yy", "mmm", "mmmm", "d", "dd", "ddd", "dddd"}:
                minute = False
                formats = {"yyyy": "%Y", "yy": "%y", "mmm": "%b", "mmmm": "%B", "d": "%d",
                           "dd": "%d", "ddd": "%a", "dddd": "%A"}
                text = value.strftime(formats[token])
                rendered.append(text.lstrip("0") if token == "d" else text)
            else:
                rendered.append(part)
        return "".join(rendered)
    if isinstance(value, (int, float)):
        if re.fullmatch(r"0+", fmt):
            sign = "-" if value < 0 else ""
            return sign + str(abs(int(value))).zfill(len(fmt))
        numeric = re.fullmatch(r'(\$|€|£)?(#,##0|0)(?:\.(0+))?', fmt.replace('"', ''))
        if numeric:
            precision = len(numeric[3] or "")
            grouping = "," if numeric[2] == "#,##0" else ""
            return (numeric[1] or "") + format(value, f"{grouping}.{precision}f")
        percent = re.fullmatch(r"0(?:\.(0+))?%", fmt)
        if percent:
            return f"{value * 100:.{len(percent[1] or '')}f}%"
        return format(value, ".15g") if isinstance(value, float) else str(value)
    return str(value)


def displayed_cell(cell) -> str:
    return displayed_value(cell.value, cell.number_format)


class LocalSpreadsheet:
    def worksheets(self, source: str) -> list[str]:
        path = Path(source)
        if path.suffix.lower() == ".csv":
            return ["CSV"]
        if path.suffix.lower() == ".xlsx":
            import openpyxl
            book = openpyxl.load_workbook(path, read_only=True, data_only=True)
            try:
                return book.sheetnames
            finally:
                book.close()
        if path.suffix.lower() == ".xls":
            import xlrd
            with xlrd.open_workbook(str(path), on_demand=True) as book:
                return book.sheet_names()
        raise ValueError("Choose a CSV, XLSX, or XLS file.")

    def read(self, source: str, worksheet: str) -> tuple[list[str], list[dict[str, str]]]:
        path = Path(source)
        if path.stat().st_size > 20_000_000:
            raise ValueError("Spreadsheet files must be under 20 MB.")
        if path.suffix.lower() == ".csv":
            with path.open(encoding="utf-8-sig", newline="") as stream:
                sample = stream.read(4096)
                stream.seek(0)
                try:
                    dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
                except csv.Error:
                    dialect = csv.excel
                return normalized_rows(csv.reader(stream, dialect))
        if path.suffix.lower() == ".xlsx":
            import openpyxl
            book = openpyxl.load_workbook(path, read_only=True, data_only=True)
            try:
                return normalized_rows(([displayed_cell(cell) for cell in row]
                                        for row in book[worksheet].iter_rows()))
            finally:
                book.close()
        if path.suffix.lower() == ".xls":
            import xlrd
            with xlrd.open_workbook(str(path), on_demand=True, formatting_info=True) as book:
                sheet = book.sheet_by_name(worksheet)
                def rows():
                    for row in sheet.get_rows():
                        values = []
                        for cell in row:
                            fmt = book.format_map[book.xf_list[cell.xf_index].format_key].format_str
                            if cell.ctype == xlrd.XL_CELL_DATE:
                                values.append(displayed_value(xlrd.xldate_as_datetime(cell.value, book.datemode), fmt))
                            elif cell.ctype == xlrd.XL_CELL_NUMBER:
                                values.append(displayed_value(cell.value, fmt))
                            elif cell.ctype == xlrd.XL_CELL_BOOLEAN:
                                values.append("TRUE" if cell.value else "FALSE")
                            else:
                                values.append(str(cell.value))
                        yield values
                return normalized_rows(rows())
        raise ValueError("Choose a CSV, XLSX, or XLS file.")


class GoogleSpreadsheet:
    """Read a link-shared workbook anonymously; never request edit access or tokens."""

    def _workbook(self, source: str):
        import openpyxl

        identifier = sheet_id(source)
        params = {"format": "xlsx"}
        resource_key = parse_qs(urlparse(source).query).get("resourcekey", [""])[0]
        if resource_key:
            if not re.fullmatch(r"[a-zA-Z0-9_-]{1,256}", resource_key):
                raise ValueError("The Google Sheets link has an invalid resource key.")
            params["resourcekey"] = resource_key
        denied = ('This sheet cannot be downloaded. In Google Sheets, set Share > General access '
                  'to "Anyone with the link" and "Viewer", and allow viewers to download. '
                  'Alternatively, import a local Excel or CSV file.')
        content = bytearray()
        try:
            with httpx.stream("GET", f"https://docs.google.com/spreadsheets/d/{identifier}/export",
                              params=params, timeout=30, follow_redirects=True) as response:
                if response.status_code in {401, 403, 404}:
                    raise ValueError(denied)
                if response.status_code != 200:
                    raise ValueError(f"Google Sheets could not be read (HTTP {response.status_code}).")
                if "text/html" in response.headers.get("content-type", "").lower():
                    raise ValueError(denied)
                for chunk in response.iter_bytes():
                    if len(content) + len(chunk) > 20_000_000:
                        raise ValueError("The Google Sheets snapshot exceeds the 20 MB import limit.")
                    content.extend(chunk)
        except httpx.RequestError as exc:
            raise ValueError("Could not reach Google Sheets. Check your connection.") from exc
        if not content.startswith(b"PK\x03\x04"):
            raise ValueError(denied)
        snapshot = io.BytesIO(content)
        try:
            with ZipFile(snapshot) as archive:
                if sum(item.file_size for item in archive.infolist()) > 200_000_000:
                    raise ValueError("The expanded Google Sheets workbook is too large to import.")
            snapshot.seek(0)
            return openpyxl.load_workbook(snapshot, read_only=True, data_only=True, keep_links=False)
        except (BadZipFile, KeyError, OSError) as exc:
            raise ValueError("Google Sheets returned an unreadable workbook. Try downloading it as Excel.") from exc

    def worksheets(self, source: str) -> list[str]:
        book = self._workbook(source)
        try:
            return book.sheetnames
        finally:
            book.close()

    def read(self, source: str, worksheet: str) -> tuple[list[str], list[dict[str, str]]]:
        book = self._workbook(source)
        try:
            if worksheet not in book.sheetnames:
                raise ValueError("This worksheet no longer exists. Load worksheets again.")
            # Read all used rows: gaps must not silently discard later recipients.
            def rows():
                for row in book[worksheet].iter_rows():
                    values = [displayed_cell(cell) for cell in row]
                    # Sheets exports include empty cells with formatting past the
                    # actual table. Keep internal gaps so missing headers still fail.
                    while values and values[-1] == "":
                        values.pop()
                    yield values
            return normalized_rows(rows())
        finally:
            book.close()


def public_sheet_records(url: str) -> list[dict[str, str]]:
    """Legacy public-sheet CLI helper; validates the host and preserves the selected gid."""
    identifier = sheet_id(url)
    parsed = urlparse(url)
    gid = parse_qs(parsed.query + "&" + parsed.fragment).get("gid", ["0"])[0]
    response = httpx.get(f"https://docs.google.com/spreadsheets/d/{identifier}/export",
                         params={"format": "csv", "gid": gid}, timeout=30, follow_redirects=True)
    response.raise_for_status()
    if "text/html" in response.headers.get("content-type", ""):
        raise ValueError("The legacy CLI requires a publicly readable Google Sheet.")
    return normalized_rows(csv.reader(io.StringIO(response.text.lstrip("\ufeff"))))[1]
