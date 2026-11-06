from __future__ import annotations

import html
import json
import threading
from collections import deque
from datetime import datetime
from pathlib import Path

import gradio as gr

from .templates import address, recipient_addresses, sanitize
from .spreadsheets import suggest_email_column
from .page_layout import create_layout
from .attachments import files_for
from .grouping_analytics import analytics_html

ASSETS = Path(__file__).parent / "assets"
CHECKS = ["Recipients and email column", "Merge fields and blank values", "Subject and message", "Attachment assignments"]


def asset(name: str) -> str:
    return (ASSETS / name).read_text(encoding="utf-8")


def table_value(application, members=False, selected=None):
    c = application.campaign
    seen = set()
    rows = []
    for r in c.recipients:
        warning = ""
        invalid, duplicate = False, False
        try:
            email = address(r.values.get(c.email_column, "")).casefold()
            duplicate = email in seen
            seen.add(email)
            if duplicate:
                warning = "Duplicate address"
        except ValueError:
            invalid, warning = True, "Invalid email address"
        if not invalid:
            try:
                recipient_addresses(c, r)
            except ValueError:
                invalid, warning = True, "Invalid alternate or copied email address"
        if r.issues:
            invalid, warning = True, "; ".join(r.issues)
        elif r.source_count > 1 and not warning:
            warning = f"{r.source_count} rows combined"
        if members and not r.selected:
            continue
        rows.append({"id": r.id, "values": r.values, "selected": r.id in (selected or []) if members else r.selected,
                     "warning": warning, "invalid": invalid, "duplicate": duplicate, "alternate_mode": r.alternate_mode})
    return json.dumps({"campaign_id": c.id, "headers": c.headers, "rows": rows,
                       "alternate_column": c.alternate_email_column,
                       "exclude_duplicate_emails": c.exclude_duplicate_emails,
                       "locked": bool(c.run_id) or application.engine.running})


def frame(body: str) -> str:
    document = '<!doctype html><html><head><meta charset="utf-8"><style>body{font:15px/1.8 Segoe UI,Arial,sans-serif;color:#26364b;padding:18px;overflow-wrap:anywhere}a{color:#5866cc}table{border-collapse:collapse}td,th{border:1px solid #ddd;padding:8px}</style></head><body>' + sanitize(body) + '</body></html>'
    return '<iframe title="Email preview" sandbox="" referrerpolicy="no-referrer" style="width:100%;height:350px;border:1px solid #e2e7ef;border-radius:10px;background:white" srcdoc="' + html.escape(document, quote=True) + '"></iframe>'


def build_ui(application):
    app = application
    with gr.Blocks(title="Mail-Merger", analytics_enabled=False, delete_cache=(3600, 86400)) as demo:
        controls = create_layout(app, asset=asset, table_value=table_value, frame=frame, checks=CHECKS)
        status = controls["status"]
        auth_status = controls["auth_status"]
        gmail = controls["gmail"]
        gmail_form = controls["gmail_form"]
        gmail_email = controls["gmail_email"]
        gmail_password = controls["gmail_password"]
        gmail_connect = controls["gmail_connect"]
        signin_methods = controls["signin_methods"]
        sign_out = controls["sign_out"]
        campaign_picker = controls["campaign_picker"]
        open_campaign = controls["open_campaign"]
        save_campaign = controls["save_campaign"]
        new_campaign = controls["new_campaign"]
        duplicate_campaign = controls["duplicate_campaign"]
        name = controls["name"]
        sender = controls["sender"]
        upload = controls["upload"]
        sheet_url = controls["sheet_url"]
        fetch = controls["fetch"]
        worksheet = controls["worksheet"]
        import_button = controls["import_button"]
        exclude_duplicates = controls["exclude_duplicates"]
        email_column = controls["email_column"]
        alternate_email_column = controls["alternate_email_column"]
        recipient_table = controls["recipient_table"]
        fields = controls["fields"]
        collapse_columns = controls["collapse_columns"]
        grouping_settings = controls["grouping_settings"]
        apply_grouping = controls["apply_grouping"]
        grouping_analytics = controls["grouping_analytics"]
        editor = controls["editor"]
        cc = controls["cc"]
        bcc = controls["bcc"]
        save_draft = controls["save_draft"]
        attachment_upload, add_attachments, library_files, attachment_scope, attachment_columns, attachment_targets, assign_attachments, assignment_summary, attachment_notice, clear_legacy = (
            controls[key] for key in ("attachment_upload", "add_attachments", "library_files", "attachment_scope", "attachment_columns", "attachment_targets", "assign_attachments", "assignment_summary", "attachment_notice", "clear_legacy"))
        remove_files = controls["remove_files"]
        preview_row = controls["preview_row"]
        preview_subject = controls["preview_subject"]
        preview_body = controls["preview_body"]
        preview_info = controls["preview_info"]
        build_review = controls["build_review"]
        report = controls["report"]
        review_checks = controls["review_checks"]
        blank_ack = controls["blank_ack"]
        send_button = controls["send_button"]
        pause_button = controls["pause_button"]
        resume_button = controls["resume_button"]
        cancel_button = controls["cancel_button"]
        progress = controls["progress"]
        terminal = controls["terminal"]
        campaign_summary = controls["campaign_summary"]
        worker_count, sending_rate, apply_speed, speed_info = (controls[key] for key in ("worker_count", "sending_rate", "apply_speed", "speed_info"))
        dashboard = controls["dashboard"]
        history_outputs = [controls[key] for key in ("history_title", "history_table", "retry_people", "history_preview_recipient", "history_log", "history_column", "history_log_page", "history_log_info", "unknown_checked", "history_subject", "history_body", "history_files", "history_state", "history_search")]

        form = [name, editor, cc, bcc, sender]
        column_mapping, uid_column, target_email, build_recipients, recipient_results = (
            controls[key] for key in ("column_mapping", "uid_column", "target_email", "build_recipients", "recipient_results"))
        campaign_outcome, outcome_text, continue_draft, next_campaign = (
            controls[key] for key in ("campaign_outcome", "outcome_text", "continue_draft", "next_campaign"))
        reset_outputs = [status, report, review_checks, blank_ack, send_button]

        def history_log_page(page):
            rows, current, pages, total = app.store.log_page(app.campaign.id, page)
            return ("\n".join(f"{at}  {recipient or 'SYSTEM'}  {message}" for _, at, recipient, message in rows),
                    gr.Number(value=current), f"Page {current} of {pages} · {total:,} total log entries")
        controls["history_log_page"].input(history_log_page, inputs=controls["history_log_page"],
                                            outputs=[controls["history_log"], controls["history_log_page"], controls["history_log_info"]])

        def history_data(state="All", column=None, search=""):
            c = app.campaign
            column = column if column in c.headers else c.uid_column or c.email_column
            states = app.store.deliveries(c.run_id)
            claimed = app.store.retry_claims(c.id)
            rows, retry = [], []
            for r in c.recipients:
                row = states.get(r.id, {})
                invalid = bool(r.issues)
                try:
                    recipient_addresses(c, r)
                except ValueError:
                    invalid = True
                outcome = row.get("state", "invalid" if invalid else "draft")
                value = r.values.get(column, "")
                email = r.values.get(c.email_column, "")
                if (state == "All" or outcome == state.lower()) and (not search or search.casefold() in value.casefold()):
                    rows.append([email, value, "Accepted by provider" if outcome == "accepted" else outcome.title(), row.get("attempts", 0), row.get("detail", "; ".join(r.issues))])
                if outcome in {"failed", "unknown", "cancelled", "invalid"} and r.id not in claimed:
                    retry.append((f"{email or r.id} · {outcome.title()} · {value}", r.id))
            log, log_number, log_info = history_log_page(1)
            return ("### " + c.name, gr.Dataframe(value=rows, headers=["Recipient", column or "Column value", "Outcome", "Attempts", "Details"]),
                    gr.CheckboxGroup(choices=retry, value=[]),
                    gr.Dropdown(choices=[(r.values.get(c.email_column, r.id), r.id) for r in c.recipients], value=None),
                    log, gr.Dropdown(choices=c.headers, value=column or None), log_number, log_info,
                    False, "", frame(""), "", state, search)

        def refresh_history(state, column, search):
            return history_data(state, column, search)
        controls["history_refresh"].input(lambda _: history_data(), inputs=controls["history_refresh"], outputs=history_outputs,
                                            concurrency_id="campaign", concurrency_limit=1)
        for component in (controls["history_state"], controls["history_column"], controls["history_search"]):
            component.input(refresh_history, inputs=[controls["history_state"], controls["history_column"], controls["history_search"]],
                            outputs=history_outputs, concurrency_id="campaign", concurrency_limit=1, preprocess=False)
        controls["history_preview_recipient"].input(lambda rid: (lambda preview: (preview[0], frame(preview[1]), preview[2]))(app.preview(rid or "")),
                                                    inputs=controls["history_preview_recipient"],
                                                    outputs=[controls["history_subject"], controls["history_body"], controls["history_files"]])
        controls["refresh_dashboard"].click(lambda: json.dumps(app.store.dashboard()), outputs=dashboard, queue=False)
        controls["history_pause"].click(lambda: (app.engine.pause(), "Sending is pausing. Progress is saved.")[1], outputs=status, queue=False)
        def resume_history():
            try:
                if not app.campaign.run_id:
                    raise ValueError("Review and approve this draft before sending.")
                app.send()
                return "Resuming pending recipients. Accepted and unknown submissions are excluded."
            except Exception as exc:
                return failure(exc)
        controls["history_resume"].click(resume_history, outputs=status, concurrency_id="campaign", concurrency_limit=1)
        demo.load(lambda: json.dumps(app.store.dashboard()), outputs=dashboard)
        demo.load(history_data, outputs=history_outputs)

        def failure(exc):
            # Surface validation messages only; never expose provider exceptions or credentials.
            return str(exc) if isinstance(exc, ValueError) else "The operation could not finish. Check your input and try again."

        def reset(message):
            return message, "", [], False, gr.Button(interactive=False)

        def sync_form(campaign_name, value, cc_value, bcc_value, provider):
            data = json.loads(value)
            if data.get("campaign_id") != app.campaign.id:
                return
            app.save_content(app.campaign.name, str(data.get("subject", "")), str(data.get("body", "")),
                             cc_value, bcc_value, provider)

        def autosave(*values):
            try:
                before = app.campaign.revision
                sync_form(*values)
                if before == app.campaign.revision:
                    return (gr.skip(),) * len(reset_outputs)
                return reset("Saved")
            except Exception as exc:
                return reset(failure(exc))

        for component in [editor, cc, bcc, sender]:
            component.input(autosave, inputs=form, outputs=reset_outputs, concurrency_id="campaign", concurrency_limit=1, show_progress="hidden", trigger_mode="always_last").then(lambda: analytics_html(app.campaign), outputs=grouping_analytics, show_progress="hidden")
        save_draft.click(autosave, inputs=form, outputs=reset_outputs, concurrency_id="campaign", concurrency_limit=1).then(lambda: analytics_html(app.campaign), outputs=grouping_analytics, show_progress="hidden")

        def save_current_campaign(*values):
            try:
                app.rename_campaign(values[0])
                sync_form(*values)
                return (f"Saved {app.campaign.name}", gr.Dropdown(choices=app.store.list_campaigns(), value=app.campaign.id),
                        html.escape(app.campaign.name))
            except Exception as exc:
                return failure(exc), gr.skip(), gr.skip()
        save_campaign.click(save_current_campaign, inputs=form, outputs=[status, campaign_picker, campaign_summary], concurrency_id="campaign", concurrency_limit=1)

        def update_speed(workers, rate):
            try:
                app.configure_sending(workers, rate)
                return "Sending speed saved", app.sending_speed_info()
            except Exception as exc:
                return failure(exc), gr.skip()
        apply_speed.click(update_speed, inputs=[worker_count, sending_rate], outputs=[status, speed_info],
                          concurrency_id="campaign", concurrency_limit=1)

        def choices():
            c = app.campaign
            return [(f"Row {r.id} · {r.values.get(c.email_column, '')}", r.id) for r in c.recipients if r.selected]

        refresh_outputs = [campaign_picker, name, sender, recipient_table, email_column, fields, editor, cc, bcc,
                           preview_row, library_files, assignment_summary, attachment_notice, preview_subject, preview_body, preview_info,
                           status, report, review_checks, blank_ack, send_button, column_mapping, recipient_results,
                           attachment_columns, attachment_targets, clear_legacy,
                           upload, sheet_url, worksheet, uid_column, target_email, attachment_upload, attachment_scope, alternate_email_column,
                           worker_count, sending_rate, exclude_duplicates, controls["correction_recipient"], controls["correction_email"], assign_attachments,
                           collapse_columns, grouping_settings, apply_grouping, grouping_analytics]

        def attachment_state():
            c = app.campaign
            assignments = {a.digest: a for a in c.attachment_assignments}
            recipient_files = [{file.digest for file in files_for(c, r.id)} for r in c.recipients if r.selected]
            rows = []
            for file in c.attachment_library:
                assignment = assignments.get(file.digest)
                target = ("Everyone selected" if assignment and assignment.everyone else
                          "; ".join(" / ".join(f"{column} = {value or '(blank)'}" for column, value in zip(assignment.columns, match))
                                    for match in assignment.matches) if assignment else "Not assigned")
                count = sum(file.digest in digests for digests in recipient_files)
                rows.append(f'<tr><td>{html.escape(file.name)}</td><td>{html.escape(target)}</td><td>{count}</td></tr>')
            summary = '<div class="assignment-table"><table><thead><tr><th>File</th><th>Assigned to</th><th>Recipients</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>' if rows else ''
            grouped = sum(r.selected and r.source_count > 1 for r in c.recipients)
            notice = f"{grouped} grouped recipient(s): attachments matching any of their original rows are combined into one email. Review each recipient's files before sending." if grouped else ""
            if any(file.digest not in assignments for file in c.attachment_library):
                notice += "\n\n**Unassigned files are not sent.** Assign every file before review. Previous uploads also need explicit assignments in this library."
            if c.shared or c.groups:
                names = ", ".join(file.name for file in c.shared)
                notice += f"\n\n**Previous attachment assignments are still active.** Files for everyone: {names or 'None'}. Remove previous assignments below to replace them with column matching."
            return summary, notice

        def refresh(message="Loaded", reset_sources=False, attachment_selection=None):
            c = app.campaign
            selected_files = [f.digest for f in c.attachment_library if f.digest in (attachment_selection or [])]
            row_choices = choices()
            first = row_choices[0][1] if row_choices else None
            subject, body, info = app.preview(first or "")
            summary, notice = attachment_state()
            return [gr.Dropdown(choices=app.store.list_campaigns(), value=c.id), c.name, "gmail",
                    table_value(app), gr.Dropdown(choices=c.headers, value=c.email_column or None),
                    gr.CheckboxGroup(choices=c.headers, value=c.fields), app.editor_value(first or ""), c.cc, c.bcc,
                    gr.Dropdown(choices=row_choices, value=first),
                    gr.CheckboxGroup(choices=[(f"{f.name} · {f.size:,} bytes", f.digest) for f in c.attachment_library], value=selected_files),
                    summary, notice, subject, frame(body), info,
                    message, "", [], False, gr.Button(interactive=False, visible=not bool(c.run_id)), gr.Column(visible=False),
                    gr.Column(visible=bool(c.recipients)), gr.CheckboxGroup(choices=c.headers, value=[]),
                    gr.CheckboxGroup(choices=[], value=[]), gr.Button(visible=bool(c.shared or c.groups)),
                    None if reset_sources else gr.skip(), "" if reset_sources else gr.skip(),
                    gr.Dropdown(choices=[], value=None) if reset_sources else gr.skip(),
                    gr.Dropdown(choices=[], value=None) if reset_sources else gr.skip(),
                    gr.Dropdown(choices=[], value=None) if reset_sources else gr.skip(),
                    None if reset_sources else gr.skip(), "Matching column values" if reset_sources else gr.skip(),
                    gr.Dropdown(choices=[("None", ""), *[(h,h) for h in c.headers if h != c.email_column]], value=c.alternate_email_column),
                    app.sending_preferences["workers"], app.sending_preferences["per_minute"], c.exclude_duplicate_emails,
                    gr.Dropdown(choices=[(r.values.get(c.email_column, r.id), r.id) for r in c.recipients], value=None), "",
                    gr.Button(interactive=bool(selected_files) and not bool(c.run_id)),
                    gr.CheckboxGroup(choices=[h for h in c.headers if h != c.email_column], value=c.collapse_columns),
                    gr.Column(visible=bool(c.headers)), gr.Button(visible=bool(c.recipients), interactive=not bool(c.run_id)), analytics_html(c)]

        def load_campaign(identifier, *values):
            try:
                if not app.campaign.run_id:
                    sync_form(*values)
                app.switch(identifier)
                return refresh(reset_sources=True)
            except Exception as exc:
                gr.Warning(failure(exc))
                return [gr.skip()] * len(refresh_outputs)

        def create_campaign(duplicate, *values):
            try:
                if not app.campaign.run_id:
                    sync_form(*values)
                app.reuse() if duplicate else app.new()
                return refresh("Campaign duplicated." if duplicate else "New campaign created.", reset_sources=True)
            except Exception as exc:
                gr.Warning(failure(exc))
                return [gr.skip()] * len(refresh_outputs)

        open_campaign.click(load_campaign, inputs=[campaign_picker, *form], outputs=refresh_outputs, concurrency_id="campaign", concurrency_limit=1).then(history_data, outputs=history_outputs)
        new_campaign.click(lambda *v: create_campaign(False, *v), inputs=form, outputs=refresh_outputs, concurrency_id="campaign", concurrency_limit=1)
        duplicate_campaign.click(lambda *v: create_campaign(True, *v), inputs=form, outputs=refresh_outputs, concurrency_id="campaign", concurrency_limit=1)
        continue_draft.click(lambda *v: create_campaign(True, *v), inputs=form, outputs=refresh_outputs, concurrency_id="campaign", concurrency_limit=1)
        next_campaign.click(lambda *v: create_campaign(False, *v), inputs=form, outputs=refresh_outputs, concurrency_id="campaign", concurrency_limit=1)

        def correct_address(identifier, email):
            try:
                app.correct_address(identifier, email)
                return refresh("Address corrected. Review the retry draft before sending.")
            except Exception as exc:
                gr.Warning(failure(exc))
                return [gr.skip()] * len(refresh_outputs)
        controls["correct_address"].click(correct_address, inputs=[controls["correction_recipient"], controls["correction_email"]],
                                            outputs=refresh_outputs, concurrency_id="campaign", concurrency_limit=1)

        def use_existing(value):
            try:
                action = json.loads(value)
                if action["action"] == "refresh":
                    return [gr.skip()] * (len(refresh_outputs) + len(history_outputs)) + ["", json.dumps(app.store.dashboard())]
                app.switch(action["id"])
                if action["action"] == "reuse":
                    app.reuse()
                return [*refresh("Import a new spreadsheet to reuse these templates." if action["action"] == "reuse" else "Campaign opened", reset_sources=True),
                        *history_data(), "reuse" if action["action"] == "reuse" else "view", json.dumps(app.store.dashboard())]
            except Exception as exc:
                gr.Warning(failure(exc))
                return [gr.skip()] * (len(refresh_outputs) + len(history_outputs)) + ["", gr.skip()]
        navigate_result = gr.Textbox(visible=False)
        dashboard.input(use_existing, inputs=dashboard, outputs=[*refresh_outputs, *history_outputs, navigate_result, dashboard], concurrency_id="campaign", concurrency_limit=1).then(
            fn=None, inputs=navigate_result, js="value => { if(value) window.location.hash = value === 'reuse' ? '#/recipients' : '#/history'; }")

        def reuse_current():
            try:
                app.reuse()
                return refresh("Import a new spreadsheet. Your templates and column choices are retained.", reset_sources=True)
            except Exception as exc:
                gr.Warning(failure(exc))
                return [gr.skip()] * len(refresh_outputs)
        controls["reuse_campaign"].click(reuse_current, outputs=refresh_outputs, concurrency_id="campaign", concurrency_limit=1).then(
            fn=None, js="() => {window.location.hash='#/recipients';}")

        def make_retry(ids, checked):
            try:
                app.retry_draft(ids, checked)
                return refresh("Retry draft created. Correct any invalid addresses, review, and approve before sending.", reset_sources=True)
            except Exception as exc:
                gr.Warning(failure(exc))
                return [gr.skip()] * len(refresh_outputs)
        controls["retry_draft"].click(make_retry, inputs=[controls["retry_people"], controls["unknown_checked"]], outputs=refresh_outputs,
                                       concurrency_id="campaign", concurrency_limit=1).then(fn=None, js="() => {window.location.hash='#/recipients';}")

        def discover_file(path):
            try:
                if not path:
                    return gr.Dropdown(choices=[], value=None), "Choose a local spreadsheet."
                worksheets = app.discover(path, "file")
                return gr.Dropdown(choices=worksheets, value=worksheets[0]), "Worksheets loaded"
            except Exception as exc:
                return gr.Dropdown(choices=[], value=None), failure(exc)

        def discover_google(url):
            try:
                worksheets = app.discover(url.strip(), "google")
                return gr.Dropdown(choices=worksheets, value=worksheets[0]), "Worksheets loaded"
            except Exception as exc:
                return gr.Dropdown(choices=[], value=None), failure(exc)

        upload.upload(discover_file, inputs=upload, outputs=[worksheet, status], concurrency_id="campaign", concurrency_limit=1)
        fetch.click(discover_google, inputs=sheet_url, outputs=[worksheet, status], concurrency_id="campaign", concurrency_limit=1)

        def import_rows(selected_worksheet, *values):
            try:
                sync_form(*values)
                headers = app.stage_rows(selected_worksheet)
                email = app.campaign.email_column if app.campaign.email_column in headers else suggest_email_column(headers)
                return (gr.Column(visible=True), gr.Dropdown(choices=headers, value=app.campaign.uid_column if app.campaign.uid_column in headers else None),
                        gr.Dropdown(choices=headers, value=email),
                        gr.Column(visible=False), "Choose a unique ID, the email column, and which columns collapse.",
                        gr.CheckboxGroup(choices=[h for h in headers if h != email], value=[h for h in app.campaign.collapse_columns if h in headers and h != email]),
                        gr.Column(visible=True), gr.Button(visible=False))
            except Exception as exc:
                return gr.skip(), gr.skip(), gr.skip(), gr.skip(), failure(exc), gr.skip(), gr.skip(), gr.skip()

        import_button.click(import_rows, inputs=[worksheet, *form],
                            outputs=[column_mapping, uid_column, target_email, recipient_results, status, collapse_columns, grouping_settings, apply_grouping],
                            concurrency_id="campaign", concurrency_limit=1)

        def group_rows(uid, email, duplicate_policy, collapse):
            try:
                app.build_recipients(uid, email, duplicate_policy, collapse)
                count = len(app.campaign.recipients)
                return refresh(f"{count:,} recipients created from {len(app.campaign.source_rows):,} rows", reset_sources=True)
            except Exception as exc:
                gr.Warning(failure(exc))
                return [gr.skip()] * len(refresh_outputs)
        build_recipients.click(group_rows, inputs=[uid_column, target_email, exclude_duplicates, collapse_columns], outputs=refresh_outputs,
                               concurrency_id="campaign", concurrency_limit=1)

        def apply_collapse(columns):
            try:
                app.set_collapse_columns(columns or [])
                return refresh("Grouping choices applied. Review the updated recipients and message.")
            except Exception as exc:
                gr.Warning(failure(exc))
                return refresh(failure(exc))
        apply_grouping.click(apply_collapse, inputs=collapse_columns, outputs=refresh_outputs,
                             concurrency_id="campaign", concurrency_limit=1)

        def primary_grouping_column(column, selected):
            headers = app.pending_snapshot[0] if app.pending_snapshot else app.campaign.headers
            return gr.CheckboxGroup(choices=[h for h in headers if h != column], value=[h for h in (selected or []) if h in headers and h != column])
        target_email.input(primary_grouping_column, inputs=[target_email, collapse_columns], outputs=collapse_columns,
                           concurrency_id="campaign", concurrency_limit=1)

        def update_duplicate_policy(enabled):
            try:
                app.set_duplicate_policy(enabled)
                rows = choices()
                first = rows[0][1] if rows else ""
                subject, body, info = app.preview(first)
                return [table_value(app), app.editor_value(first), gr.Dropdown(choices=rows, value=first or None),
                        subject, frame(body), info, attachment_state()[1],
                        *reset("Duplicate email filtering enabled" if enabled else "Distinct recipients sharing an email are included")]
            except Exception as exc:
                gr.Warning(failure(exc))
                return [gr.skip()] * 12
        exclude_duplicates.input(update_duplicate_policy, inputs=exclude_duplicates,
                                  outputs=[recipient_table, editor, preview_row, preview_subject, preview_body, preview_info, attachment_notice, *reset_outputs],
                                  concurrency_id="campaign", concurrency_limit=1)

        selection_outputs = [editor, preview_row, attachment_notice, status, report, review_checks, blank_ack, send_button,
                             preview_subject, preview_body, preview_info]
        def select_rows(value, confirmed_fields, column, *values):
            try:
                table = json.loads(value)
                if table.get("campaign_id") != app.campaign.id or table.get("headers") != app.campaign.headers or not column:
                    return [gr.skip()] * len(selection_outputs)
                before = app.campaign.revision
                sync_form(*values)
                app.selection([r["id"] for r in table.get("rows", []) if r["selected"]], confirmed_fields, column,
                              {r["id"]: r.get("alternate_mode", "cc") for r in table.get("rows", [])})
                if before == app.campaign.revision:
                    return [gr.skip()] * len(selection_outputs)
                row_choices = choices()
                subject, body, info = app.preview(row_choices[0][1] if row_choices else "")
                return [app.editor_value(), gr.Dropdown(choices=row_choices, value=row_choices[0][1] if row_choices else None),
                        attachment_state()[1], *reset("Recipients updated"), subject, frame(body), info]
            except Exception as exc:
                gr.Warning(failure(exc))
                return [gr.skip()] * len(selection_outputs)

        def select_recipients(value, *values):
            table = json.loads(value)
            # A programmatic table refresh can arrive before the field checklist.
            # Recipient selection must never overwrite field confirmation.
            if table.get("campaign_id") != app.campaign.id or table.get("headers") != app.campaign.headers:
                return [gr.skip()] * len(selection_outputs)
            return select_rows(value, app.campaign.fields, app.campaign.email_column, *values)
        recipient_table.input(select_recipients, inputs=[recipient_table, *form], outputs=selection_outputs,
                               concurrency_id="campaign", concurrency_limit=1, trigger_mode="always_last")
        fields.input(lambda table, confirmed, *values: select_rows(table, confirmed, app.campaign.email_column, *values),
                     inputs=[recipient_table, fields, *form], outputs=selection_outputs,
                     concurrency_id="campaign", concurrency_limit=1, preprocess=False)

        def change_column(column, *values):
            try:
                data = json.loads(values[1])
                if data.get("campaign_id") != app.campaign.id or column not in app.campaign.headers:
                    return [gr.skip()] * len(refresh_outputs)
                sync_form(*values)
                app.set_email_column(column)
                return refresh("Email column updated")
            except Exception as exc:
                gr.Warning(failure(exc))
                return [gr.skip()] * len(refresh_outputs)
        email_column.input(change_column, inputs=[email_column, *form], outputs=refresh_outputs, concurrency_id="campaign", concurrency_limit=1, preprocess=False)

        def change_alternate_column(column, *values):
            try:
                data = json.loads(values[1])
                if data.get("campaign_id") != app.campaign.id:
                    return [gr.skip()] * len(refresh_outputs)
                sync_form(*values)
                app.set_alternate_email_column(column)
                return refresh("Alternate email column updated")
            except Exception as exc:
                gr.Warning(failure(exc))
                return [gr.skip()] * len(refresh_outputs)
        alternate_email_column.input(change_alternate_column, inputs=[alternate_email_column, *form], outputs=refresh_outputs,
                                      concurrency_id="campaign", concurrency_limit=1, preprocess=False)

        def upload_files(paths, *values):
            try:
                sync_form(*values)
                previous = {file.digest for file in app.campaign.attachment_library}
                app.upload_attachments([path.strip().strip('"') for path in (paths or "").splitlines() if path.strip()])
                added = [file.digest for file in app.campaign.attachment_library if file.digest not in previous]
                selection = added if len(added) == 1 else []
                return refresh("Files added. Select files and their recipients, then save the assignment.", attachment_selection=selection)
            except Exception as exc:
                gr.Warning(failure(exc))
                return [gr.skip()] * len(refresh_outputs)
        add_attachments.click(upload_files, inputs=[attachment_upload, *form], outputs=refresh_outputs, concurrency_id="campaign", concurrency_limit=1)
        controls["browse_attachments"].click(fn=None, outputs=attachment_upload,
            js="async () => { if (!window.pywebview?.api?.select_attachment_files) { alert('Enter full file paths below when using browser mode.'); return ''; } return (await window.pywebview.api.select_attachment_files()).join('\\n'); }")

        def target_choices(columns):
            return gr.CheckboxGroup(choices=[(" / ".join(f"{column}: {value or '(blank)'}" for column, value in zip(columns, values)), json.dumps(values))
                                             for values in app.attachment_targets(columns)], value=[])
        attachment_columns.input(target_choices, inputs=attachment_columns, outputs=attachment_targets, concurrency_id="campaign", concurrency_limit=1)

        library_files.change(lambda digests: gr.Button(interactive=bool(digests) and not bool(app.campaign.run_id)),
                             inputs=library_files, outputs=assign_attachments, queue=False, show_progress="hidden")

        def save_assignments(digests, scope, columns, targets, *values):
            try:
                sync_form(*values)
                app.assign_attachments(digests, scope == "Everyone selected", columns, [json.loads(target) for target in targets])
                grouped = sum(r.selected and r.source_count > 1 for r in app.campaign.recipients)
                if grouped:
                    gr.Info(f"{grouped} grouped recipient(s) will receive the distinct files matching their original rows in one email.")
                return refresh("File assignments saved. Select files to make another assignment.")
            except Exception as exc:
                gr.Warning(failure(exc))
                return [gr.skip()] * len(refresh_outputs)
        assign_attachments.click(save_assignments, inputs=[library_files, attachment_scope, attachment_columns, attachment_targets, *form], outputs=refresh_outputs, concurrency_id="campaign", concurrency_limit=1)

        def remove(digests):
            try:
                app.remove_library_files(digests)
                return refresh("Attachments removed")
            except Exception as exc:
                gr.Warning(failure(exc))
                return [gr.skip()] * len(refresh_outputs)
        remove_files.click(remove, inputs=library_files, outputs=refresh_outputs, concurrency_id="campaign", concurrency_limit=1)
        def remove_previous():
            try:
                app.clear_legacy_attachments()
                return refresh("Previous assignments removed")
            except Exception as exc:
                gr.Warning(failure(exc))
                return [gr.skip()] * len(refresh_outputs)
        clear_legacy.click(remove_previous, outputs=refresh_outputs, concurrency_id="campaign", concurrency_limit=1)

        def preview(identifier):
            subject, body, info = app.preview(identifier or "")
            return subject, frame(body), info, app.editor_value(identifier or "")
        preview_row.input(preview, inputs=preview_row, outputs=[preview_subject, preview_body, preview_info, editor], concurrency_id="campaign", concurrency_limit=1, show_progress="hidden")

        def review(*values):
            try:
                sync_form(*values)
                errors, blanks = app.review()
                if errors:
                    text = "**Review errors**\n" + "\n".join("- " + html.escape(e) for e in errors[:30])
                    if len(errors) > 30:
                        text += f"\n…and {len(errors) - 30} more issues."
                else:
                    text = f"**{sum(r.selected for r in app.campaign.recipients):,} recipients ready**"
                if blanks:
                    text += "\n\n**Blank merge values — acknowledgement required:**\n" + "\n".join("- " + html.escape(v) for v in blanks[:30])
                    if len(blanks) > 30:
                        text += f"\n…and {len(blanks) - 30} more rows with blank values."
                return "Campaign checked", text, [], False, gr.Button(interactive=False)
            except Exception as exc:
                return reset(failure(exc))
        build_review.click(review, inputs=form, outputs=reset_outputs, concurrency_id="campaign", concurrency_limit=1).then(
            preview, inputs=preview_row, outputs=[preview_subject, preview_body, preview_info, editor],
            concurrency_id="campaign", concurrency_limit=1)

        def approve(checks, acknowledge):
            try:
                app.approve(checks, acknowledge)
                return gr.Button(interactive=True), "Approved"
            except Exception as exc:
                if len(checks) == 4:
                    return gr.Button(interactive=False), failure(exc)
                return gr.Button(interactive=False), gr.skip()
        for control in (review_checks, blank_ack):
            control.input(approve, inputs=[review_checks, blank_ack], outputs=[send_button, status], concurrency_id="campaign", concurrency_limit=1, trigger_mode="always_last")

        def send(checks, acknowledge, *values):
            try:
                sync_form(*values)
                app.approve(checks, acknowledge)
                app.send()
                return "Sending", gr.Button(interactive=False), app.editor_value(), table_value(app)
            except Exception as exc:
                return failure(exc), gr.Button(interactive=False), gr.skip(), gr.skip()
        send_button.click(send, inputs=[review_checks, blank_ack, *form], outputs=[status, send_button, editor, recipient_table], concurrency_id="campaign", concurrency_limit=1)
        pause_button.click(lambda: (app.engine.pause(), "Pausing. In-flight messages are allowed to finish.")[1], outputs=status, queue=False)

        def resume():
            try:
                app.send()
                return "Resuming pending recipients. Accepted and unknown submissions are skipped."
            except Exception as exc:
                return failure(exc)
        resume_button.click(resume, outputs=status, concurrency_id="campaign", concurrency_limit=1)

        def cancel():
            if app.engine.running:
                app.engine.cancel()
            elif app.campaign.run_id:
                app.store.cancel_pending(app.campaign.run_id)
            return "Pending recipients cancelled; in-flight submissions may still finish."
        cancel_button.click(cancel, outputs=status, queue=False)

        def show_gmail_form():
            if any(k in app.auth.accounts() for k in ("gmail",)):
                return gr.Column(visible=False), "", "", "Sign out of your current account first."
            email = app.auth.accounts().get("gmail", "")
            return gr.Column(visible=True), email, "", ""
        gmail.click(show_gmail_form, outputs=[gmail_form, gmail_email, gmail_password, status], queue=False)

        def connect_gmail(email, password):
            try:
                if getattr(app, "account_scoped", False):
                    raise ValueError("Sign out and reopen Mail-Merger to authenticate another account with its email code.")
                if app.store.vault.enabled and email.strip().casefold() != app.store.vault.owner.casefold():
                    raise ValueError("This encrypted history belongs to " + app.store.vault.owner + ". Sign in with that account.")
                if app.engine.running:
                    raise ValueError("Pause sending before changing accounts.")
                message = app.auth.connect_gmail(email, password)
                app.audit.record("sign_in", email.strip())
                selected_sender = "gmail" if not app.campaign.run_id else gr.skip()
                return message, "", gr.Column(visible=False), selected_sender
            except Exception as exc:
                app.audit.record("sign_in", "", "failed")
                return failure(exc), "", gr.Column(visible=True), gr.skip()
        gmail_connect.click(connect_gmail, inputs=[gmail_email, gmail_password],
                            outputs=[status, gmail_password, gmail_form, sender], concurrency_limit=1)
        def logout():
            if app.engine.running:
                return "Pause sending and wait for in-flight messages before signing out."
            if app.store.vault.enabled or getattr(app, "account_scoped", False):
                return "Closing encrypted history..."
            app.audit.record("sign_out", app.auth.accounts().get("gmail", ""))
            app.auth.sign_out()
            return app.auth.status
        sign_out.click(logout, outputs=status, queue=False).then(fn=None, inputs=status,
            js="async message => { if(message === 'Closing encrypted history...') { if(window.pywebview?.api?.sign_out_and_lock) await window.pywebview.api.sign_out_and_lock(); else { const response=await fetch('/_account/sign-out',{method:'POST'}); if(response.ok){document.body.textContent='Signed out. Close this tab and relaunch Mail-Merger to sign in again.';} } } }")

        def encrypt_history(code_sender, code_password):
            try:
                if app.engine.running or app.auth.busy:
                    raise ValueError("Pause sending and finish sign-in before encrypting history.")
                if app.pending_snapshot or app.source:
                    raise ValueError("Finish importing the current spreadsheet before enabling encryption.")
                owner = app.auth.accounts().get("gmail")
                if not owner:
                    raise ValueError("Sign in with the Gmail account that will own this encrypted history.")
                _, app_password = app.auth.gmail_credentials(owner)
                app.store.enable_encryption(owner, app_password, code_sender, code_password)
                app.store.vault.use_app_password(app_password)
                from .desktop import cleanup_upload_cache
                cleanup_upload_cache(app.store.root)
                return "History encrypted for " + owner, "", gr.Button(interactive=False)
            except Exception as exc:
                return failure(exc), "", gr.skip()
        controls["enable_encryption"].click(encrypt_history,
            inputs=[controls[k] for k in ("otp_sender", "otp_password")],
            outputs=[controls[k] for k in ("encryption_status", "otp_password", "enable_encryption")],
            concurrency_id="campaign", concurrency_limit=1)
        controls["export_recovery"].click(fn=None, outputs=status,
            js="async () => { if (!window.pywebview?.api?.export_recovery_key) return 'Recovery export is available in the desktop window.'; try { return await window.pywebview.api.export_recovery_key(); } catch { return 'Enable and unlock encryption before exporting a recovery key.'; } }")
        controls["import_otp_setup"].click(fn=None, outputs=status,
            js="async () => { if(!window.pywebview?.api?.import_otp_setup) return 'Open the desktop window to import private settings.'; const result=await window.pywebview.api.import_otp_setup(); if(result.ok)window.location.reload(); return result.message; }")
        controls["export_otp_setup"].click(fn=None, outputs=status,
            js="async () => { if(!window.pywebview?.api?.export_otp_setup) return 'Open the desktop window to export private settings.'; try{return await window.pywebview.api.export_otp_setup();}catch{return 'Configure the OTP sender before exporting its private setup TXT.';} }")
        controls["export_login_audit"].click(fn=None, outputs=status,
            js="async () => { if(!window.pywebview?.api?.export_login_audit) return 'Open the desktop window to export the login audit.'; try{return await window.pywebview.api.export_login_audit();}catch{return 'Could not export the login audit.';} }")

        log_cache = {"campaign": None, "cursor": 0, "lines": deque(maxlen=300)}
        log_lock = threading.Lock()

        def poll():
            accounts = app.auth.accounts()
            labels = [f"{provider.title()}: {html.escape(email)}" for provider, email in accounts.items() if provider in {"gmail"}]
            auth_text = " · ".join(labels) if labels else "Not signed in"
            if app.auth.busy:
                auth_text += " · Connecting to Gmail…" if getattr(app.auth, "pending_provider", None) == "gmail" else " · Complete sign-in in your browser."
            elif app.auth.status.startswith("Sign-in did not"):
                auth_text += " · " + app.auth.status
            c = app.campaign
            summary = app.run_summary()
            counts = summary["counts"]
            labels = [("Accepted", "accepted"), ("Pending", "pending"), ("Submitting", "submitting"),
                      ("Failed", "failed"), ("Unknown", "unknown"), ("Cancelled", "cancelled")]
            cards = ''.join(f'<div class="progress-card"><strong>{counts.get(key, 0)}</strong><span>{label}</span></div>' for label, key in labels)
            state = summary["title"]
            progress_html = f'<div class="queue-state">{html.escape(state)}</div><div class="progress-cards">{cards}</div>'
            total = sum(counts.values())
            result_text = f'{counts.get("accepted", 0)} of {total} accepted by provider'
            for key in ("failed", "unknown", "cancelled"):
                if counts.get(key):
                    result_text += f' · {counts[key]} {key}'
            if summary["pending"]:
                result_text += f' · {summary["pending"]} remaining'
            if counts.get("unknown"):
                result_text += ' · Check Sent mail for unknown outcomes.'
            mark = "&#10003;" if not summary["pending"] and not (counts.get("failed") or counts.get("unknown")) else "&#9679;"
            outcome_html = f'<div class="outcome-heading"><span class="outcome-mark" aria-hidden="true">{mark}</span><div><h2>{html.escape(state)}</h2><p>{html.escape(result_text)}</p></div></div>'
            with log_lock:
                if log_cache["campaign"] != c.id:
                    log_cache.update(campaign=c.id, cursor=0, lines=deque(maxlen=300))
                lines = app.store.logs(c.id, after=log_cache["cursor"])
                for identifier, at, recipient, message in lines:
                    local_time = datetime.fromisoformat(at).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
                    log_cache["lines"].append(f"{local_time}  {recipient or 'SYSTEM'}  {message}")
                    log_cache["cursor"] = identifier
                log = "\n".join(log_cache["lines"])
            connected = any(k in accounts for k in ("gmail",))
            return (auth_text, progress_html, log, html.escape(c.name), gr.Row(visible=not connected),
                    gr.Button(value="Sign out and close history" if app.store.vault.enabled or getattr(app, "account_scoped", False) else "Sign out", visible=bool(accounts)), gr.Column(visible=False) if connected else gr.skip(),
                    gr.Column(visible=summary["has_history"]), outcome_html,
                    gr.Button(interactive=not summary["running"]), gr.Button(interactive=not summary["running"]),
                    gr.Button(visible=summary["has_history"], interactive=summary["running"]),
                    gr.Button(visible=summary["has_history"], interactive=not summary["running"] and bool(summary["pending"])),
                    gr.Button(visible=summary["has_history"], interactive=bool(summary["pending"])),
                    gr.Button(visible=False, interactive=False) if c.run_id else gr.skip(),
                    gr.Button(interactive=not bool(c.run_id)), state if c.run_id else gr.skip(), app.sending_speed_info())
        poll_outputs = [auth_status, progress, terminal, campaign_summary, signin_methods, sign_out, gmail_form,
                        campaign_outcome, outcome_text, continue_draft, next_campaign,
                        pause_button, resume_button, cancel_button, send_button, build_review, status, speed_info]
        timer = gr.Timer(1)
        timer.tick(poll, outputs=poll_outputs, queue=False, show_progress="hidden")
        demo.load(lambda: refresh(""), outputs=refresh_outputs).then(
            fn=None, js="() => { document.getElementById('mail-merger-shell').dataset.ready = 'true'; }")
        demo.load(poll, outputs=poll_outputs)
    demo.queue(default_concurrency_limit=4, max_size=128)
    return demo
