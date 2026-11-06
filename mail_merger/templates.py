from __future__ import annotations

import base64
import html as html_module
import json
import mimetypes
import re
from email.message import EmailMessage
from email.policy import SMTP
from html.parser import HTMLParser
from urllib.parse import urlsplit

import nh3
from lxml import etree
from lxml import html as lxml_html
from email_validator import EmailNotValidError, validate_email

from .attachments import MAX_REQUEST_BYTES, files_for, verify_file
from .models import Campaign, PreparedMail, Recipient

TOKEN = re.compile(r"\$\{([^{}]+)\}")
TAGS = {"p", "br", "div", "span", "strong", "b", "em", "i", "u", "s", "ul", "ol", "li",
        "a", "h1", "h2", "h3", "blockquote", "pre", "code", "table", "thead", "tbody", "tr", "th", "td"}
STYLE_RULES = {
    "color": r"(?:#[0-9a-f]{3,8}|rgba?\([\d.,%\s]+\)|black|white|red|blue|green|yellow|transparent)",
    "background-color": r"(?:#[0-9a-f]{3,8}|rgba?\([\d.,%\s]+\)|black|white|red|blue|green|yellow|transparent)",
    "font-family": r"(?:Arial|sans-serif|serif|monospace|Georgia|Tahoma|Verdana|Times New Roman|Courier New|Trebuchet MS)(?:,\s*(?:sans-serif|serif|monospace))?",
    "font-size": r"(?:[8-9]|[1-5][0-9]|6[0-4])(?:px|pt)|(?:small|medium|large|x-large|xx-large)",
    "font-weight": r"(?:normal|bold|[1-9]00)",
    "font-style": r"(?:normal|italic)",
    "text-decoration": r"(?:(?:underline|line-through|none)(?:\s+(?:underline|line-through))?)",
    "text-align": r"(?:left|center|right|justify)",
    "margin-left": r"(?:0|[1-9][0-9]?|1[0-9]{2}|200)px",
}


def safe_style(style: str) -> str:
    declarations = []
    for declaration in style.split(";"):
        name, separator, value = declaration.partition(":")
        name, value = name.strip().lower(), value.strip().replace('"', '').replace("'", "")
        if separator and name in STYLE_RULES and re.fullmatch(STYLE_RULES[name], value, re.I):
            declarations.append(f"{name}: {value}")
    return "; ".join(declarations)


def sanitize(source: str) -> str:
    def attribute(tag, key, value):
        if key == "style":
            return safe_style(value) or None
        if key in {"start", "value"}:
            return value if re.fullmatch(r"[0-9]{1,6}", value) else None
        return value
    return nh3.clean(source, tags=TAGS, attributes={"*": {"style"}, "a": {"href", "title"}, "ol": {"start"}, "li": {"value"}},
                     attribute_filter=attribute,
                     filter_style_properties=set(STYLE_RULES),
                     clean_content_tags={"script", "style", "iframe", "object"},
                     url_schemes={"https", "http", "mailto"}, link_rel="noopener noreferrer")


def address(value: str) -> str:
    try:
        return validate_email(value.strip(), check_deliverability=False, allow_smtputf8=False).normalized
    except EmailNotValidError as exc:
        raise ValueError(f"Invalid email address: {value!r}") from exc


def addresses(value: str) -> list[str]:
    return [address(v) for v in re.split(r"[,;\n]", value) if v.strip()]


def recipient_addresses(campaign: Campaign, recipient: Recipient) -> tuple[str, list[str], list[str]]:
    to = address(recipient.values.get(campaign.email_column, ""))
    cc, bcc = addresses(campaign.cc), addresses(campaign.bcc)
    if campaign.alternate_email_column:
        if campaign.alternate_email_column not in campaign.headers:
            raise ValueError("Choose an available alternate email column.")
        # Only the alternate column is optional. A blank or null cell adds no
        # copied destination and must not invalidate the primary recipient.
        value = recipient.values.get(campaign.alternate_email_column)
        alternate = addresses(value if value is not None else "")
        if alternate:
            if recipient.alternate_mode not in {"cc", "bcc"}:
                raise ValueError("Alternate email must use CC or BCC.")
            (bcc if recipient.alternate_mode == "bcc" else cc).extend(alternate)
    # One envelope destination per address. BCC takes precedence over visible CC.
    hidden = {item.casefold(): item for item in bcc if item.casefold() != to.casefold()}
    visible = {item.casefold(): item for item in cc if item.casefold() != to.casefold() and item.casefold() not in hidden}
    return to, list(visible.values()), list(hidden.values())


def fields_used(template: str) -> set[str]:
    if "${" in TOKEN.sub("", template):
        raise ValueError("Incomplete merge field. Use ${Column Name}.")
    return set(TOKEN.findall(template))


def _substitute(template: str, values: dict[str, str], escape: bool) -> str:
    fields_used(template)
    def replacement(match):
        key = match[1]
        if key not in values:
            raise ValueError(f"Unresolved merge field: {key}")
        value = str(values[key])
        return html_module.escape(value, quote=True).replace("\r\n", "\n").replace("\n", "<br>") if escape else value
    return TOKEN.sub(replacement, template)


class _HTMLRenderer(HTMLParser):
    def __init__(self, values: dict[str, str]):
        super().__init__(convert_charrefs=False)
        self.values = values
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        rendered = []
        for key, value in attrs:
            value = value or ""
            if "${" in value and (tag != "a" or key not in {"href", "title"}):
                raise ValueError("Merge fields in HTML attributes are only supported in link href/title.")
            resolved = _substitute(value, self.values, False)
            if tag == "a" and key == "href" and resolved:
                if urlsplit(resolved.strip()).scheme.lower() not in {"https", "http", "mailto"}:
                    raise ValueError("Links must use http, https, or mailto.")
            rendered.append(f' {key}="{html_module.escape(resolved, quote=True)}"')
        self.parts.append(f"<{tag}{''.join(rendered)}>")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        self.parts.append(f"</{tag}>")

    def handle_data(self, data):
        self.parts.append(_substitute(data, self.values, True))

    def handle_entityref(self, name):
        self.parts.append(f"&{name};")

    def handle_charref(self, name):
        self.parts.append(f"&#{name};")


def render(template: str, values: dict[str, str], html: bool = False) -> str:
    if not html:
        return _substitute(template, values, False)
    if re.search(r"<(script|style)\b[^>]*>[^<]*\$\{", template, re.I):
        raise ValueError("Merge fields cannot appear in scripts or styles.")
    # A merge field inside a list item repeats that item, retaining formatting
    # and letting the surrounding ol/ul continue numbering naturally.
    tree = lxml_html.fragment_fromstring(template or "", create_parent="div")
    values = dict(values)
    for item in list(tree.iter("li")):
        if item.find(".//ol") is not None or item.find(".//ul") is not None:
            continue
        markup = etree.tostring(item, encoding="unicode", method="html", with_tail=False)
        fields = TOKEN.findall(markup)
        lines = {key: str(values[key]).replace("\r\n", "\n").split("\n") for key in fields if key in values}
        count = max((len(parts) for parts in lines.values()), default=1)
        if count <= 1:
            continue
        if any(len(parts) not in {1, count} for parts in lines.values()):
            raise ValueError("List fields have different numbers of values. Use a separate list item for each column.")
        parent, position = item.getparent(), item.getparent().index(item)
        for index in range(count):
            def token(match):
                key = match[1]
                if key not in lines:
                    return match[0]
                generated = f"__list_{len(values)}"
                while generated in values:
                    generated += "_"
                values[generated] = lines[key][index if len(lines[key]) > 1 else 0]
                return "${" + generated + "}"
            clone = lxml_html.fragment_fromstring(TOKEN.sub(token, markup))
            if index:
                clone.attrib.pop("value", None)
            clone.tail = item.tail if index == count - 1 else None
            parent.insert(position + index, clone)
        parent.remove(item)
    template = html_module.escape(tree.text or "") + "".join(etree.tostring(child, encoding="unicode", method="html") for child in tree)
    parser = _HTMLRenderer(values)
    parser.feed(template)
    parser.close()
    return sanitize("".join(parser.parts))


def render_subject(template: str, values: dict[str, str], grouped: bool = False) -> str:
    # Grouped values are lines in the body, but email subjects must stay on one line.
    resolved = {key: str(value).replace("\n", ", ") for key, value in values.items()} if grouped else values
    return render(template, resolved)


def validate_list_alignment(template: str, recipient: Recipient):
    """Reject unequal list lengths and equal lengths drawn from different rows."""
    tree = lxml_html.fragment_fromstring(template or "", create_parent="div")
    for item in tree.iter('li'):
        if item.find('.//ol') is not None or item.find('.//ul') is not None:
            continue
        fields = list(dict.fromkeys(TOKEN.findall(etree.tostring(item, encoding='unicode', method='html', with_tail=False))))
        columns = []
        for field in fields:
            parts = str(recipient.values.get(field, '')).replace('\r\n', '\n').split('\n')
            if len(parts) > 1:
                columns.append((field, len(parts), recipient.field_rows.get(field, list(range(len(parts))))))
        if len(columns) > 1 and any((count, rows) != (columns[0][1], columns[0][2]) for _, count, rows in columns[1:]):
            details = ', '.join(f'{field}: {count} values' for field, count, _ in columns)
            raise ValueError(f'List fields no longer match their source rows ({details}). Keep these columns uncollapsed, or use separate list items.')


def render_recipient_body(template: str, recipient: Recipient) -> str:
    validate_list_alignment(template, recipient)
    return render(template, recipient.values, html=True)


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
    def handle_data(self, data):
        self.parts.append(data)
    def handle_endtag(self, tag):
        if tag in {"p", "div", "li", "tr", "h1", "h2", "h3"}:
            self.parts.append("\n")
    def handle_starttag(self, tag, attrs):
        if tag == "br":
            self.parts.append("\n")


def plain_text(source: str) -> str:
    parser = _PlainText()
    parser.feed(source)
    return "".join(parser.parts).strip()


def prepare(campaign: Campaign, recipient: Recipient, content_cache: dict | None = None) -> PreparedMail:
    if recipient.issues:
        raise ValueError("; ".join(recipient.issues))
    values = recipient.values
    to, cc, bcc = recipient_addresses(campaign, recipient)
    subject = render_subject(campaign.subject, values, grouped=recipient.source_count > 1)
    if not subject.strip() or "\r" in subject or "\n" in subject:
        raise ValueError("Subject must be nonempty and cannot contain line breaks.")
    body = render_recipient_body(campaign.body, recipient)
    if not plain_text(body).strip():
        raise ValueError("Email body is empty.")
    files = files_for(campaign, recipient.id)
    cache = content_cache if content_cache is not None else {}
    def content(file):
        if file.digest not in cache:
            cache[file.digest] = verify_file(file)
        return cache[file.digest]
    if campaign.provider == "gmail":
        message = EmailMessage(policy=SMTP)
        message["From"] = address(campaign.sender)
        message["To"] = to
        message["Subject"] = subject
        if cc:
            message["Cc"] = ", ".join(cc)
        message.set_content(plain_text(body), cte="quoted-printable")
        message.add_alternative(body, subtype="html", cte="quoted-printable")
        for file in files:
            mime = (mimetypes.guess_type(file.name)[0] or "application/octet-stream").split("/", 1)
            message.add_attachment(content(file), maintype=mime[0], subtype=mime[1], filename=file.name)
        # SMTP envelope includes BCC; the message headers must never expose it.
        payload = message.as_bytes()
    elif campaign.provider == "outlook":
        def recipients(items):
            return [{"emailAddress": {"address": item}} for item in items]
        message = {"subject": subject, "body": {"contentType": "HTML", "content": body},
                   "toRecipients": recipients([to]), "ccRecipients": recipients(cc), "bccRecipients": recipients(bcc),
                   "attachments": [{"@odata.type": "#microsoft.graph.fileAttachment", "name": f.name,
                                    "contentType": mimetypes.guess_type(f.name)[0] or "application/octet-stream",
                                    "contentBytes": base64.b64encode(content(f)).decode()} for f in files]}
        payload = json.dumps({"message": message, "saveToSentItems": True}, separators=(",", ":")).encode()
    else:
        raise ValueError("Sign in and choose a sender before review.")
    request_size = len(payload)
    if campaign.provider == "gmail":
        request_size += len(re.findall(br"(?m)^\.", payload)) + 3  # SMTP dot-stuffing and DATA terminator
    if request_size >= MAX_REQUEST_BYTES:
        raise ValueError(f"Encoded request for {to} is {request_size:,} bytes; it must be below 4,000,000 bytes.")
    return PreparedMail(recipient.id, to, subject, body, files, payload, list(dict.fromkeys([to, *cc, *bcc])))
