"""Recipient statistics, sampled hover cards, and a complete risk listing."""
import html

from .templates import validate_list_alignment


def grouping_statistics(campaign):
    collapsed, issues = [], []
    for recipient in campaign.recipients:
        removed = set()
        partial = []
        for column, kept in recipient.field_rows.items():
            removed.update(set(range(recipient.source_count)) - set(kept))
            if 1 < len(kept) < recipient.source_count:
                partial.append(column)
        if removed:
            collapsed.append((recipient, len(removed), 'Collapsed: ' + ', '.join(recipient.field_rows)))
        try:
            validate_list_alignment(campaign.body, recipient)
            reason = 'Partial collapse may change row pairings: ' + ', '.join(partial) if partial else ''
        except ValueError as exc:
            reason = str(exc)
        if reason:
            issues.append((recipient, recipient.source_count, reason))
    people = [(r, r.source_count, '') for r in campaign.recipients]
    return [
        ('Source rows', sum(r.source_count for r in campaign.recipients), people, False),
        ('Recipients', len(campaign.recipients), people, False),
        ('Rows with collapsed values', sum(count for _, count, _ in collapsed), collapsed, False),
        ('Rows with pairing risks', sum(count for _, count, _ in issues), issues, True),
    ]


def analytics_html(campaign):
    escape = lambda value: html.escape(str(value), quote=True)
    source_by_uid = {}
    for row in campaign.source_rows:
        source_by_uid.setdefault(row.get(campaign.uid_column, '').strip(), []).append(row)
    cards = []
    for label, count, affected, warning in grouping_statistics(campaign):
        examples = []
        for recipient, _, reason in (affected if warning else affected[:3]):
            uid = recipient.uid_value or recipient.values.get(campaign.uid_column, '').strip()
            rows = source_by_uid.get(uid, [])
            if not warning:
                rows = rows[:3]
            if not rows:
                rows = [recipient.values]
            headers = campaign.headers
            table = '<table><thead><tr>' + ''.join('<th>' + escape(h) + '</th>' for h in headers) + '</tr></thead><tbody>'
            table += ''.join('<tr>' + ''.join('<td>' + escape(row.get(h, '')) + '</td>' for h in headers) + '</tr>' for row in rows)
            table += '</tbody></table>'
            examples.append('<section><strong>UID ' + escape(uid) + '</strong><p>' + escape(reason) + '</p><div class="analytics-sample-scroll">' + table + '</div></section>')
        content = ''.join(examples) or '<p>No affected rows.</p>'
        heading = 'all affected rows' if warning else 'sample rows'
        if warning and affected and not campaign.source_rows:
            heading = 'all affected recipients'
            content = '<p>Saved grouped values are shown. Reimport the sheet to inspect the original rows.</p>' + content
        style = ' warning' if warning and count else ''
        cards.append('<article class="analytics-card' + style + '" tabindex="0"><strong class="analytics-count">' + f'{count:,}' + '</strong><span>' + label + '</span><div class="analytics-examples" role="tooltip"><b>' + label + ': ' + heading + '</b>' + content + '</div></article>')
    return '<div class="recipient-analytics">' + ''.join(cards) + '</div>'
