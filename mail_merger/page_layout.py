"""Focused workspace pages; controls are kept mounted while navigating."""

import gradio as gr


def page_header(title):
    gr.HTML(f'<header class="page-heading"><h1>{title}</h1></header>')


def page_navigation(previous=None, following=None):
    links = []
    if previous:
        key, label = previous
        links.append(f'<a class="page-back" href="#/{key}">← {label}</a>')
    if following:
        key, label = following
        links.append(f'<a class="page-next" href="#/{key}">{label} <span>→</span></a>')
    gr.HTML('<nav class="page-navigation" aria-label="Page traversal">' + ''.join(links) + '</nav>')


def create_layout(app, *, asset, table_value, frame, checks):
    with gr.Row(elem_id="mail-merger-shell", equal_height=False):
        with gr.Column(scale=0, min_width=224, elem_id="left-panel"):
            gr.HTML('<div class="sidebar-brand"><span class="brand-mark">M<span>↗</span></span><span>Mail-Merger</span></div>')
            campaign_summary = gr.Markdown(app.campaign.name, elem_id="sidebar-campaign")
            navigation = gr.HTML(
                "dashboard" if app.auth.accounts() else "accounts",
                html_template=asset("navigation.html"), css_template="", js_on_load=asset("navigation.js"),
                elem_id="page-menu")

        with gr.Column(scale=1, min_width=0, elem_id="page-workspace"):
            status = gr.Markdown("", elem_id="app-status")
            with gr.Column(visible=bool(app.campaign.run_id), elem_id="campaign-outcome") as campaign_outcome:
                outcome_text = gr.HTML()
                with gr.Row(elem_classes=["outcome-actions"]):
                    continue_draft = gr.Button("Edit as new draft", variant="primary", scale=0, min_width=150)
                    next_campaign = gr.Button("New campaign", scale=0, min_width=130)

            with gr.Column(elem_id="page-campaigns", elem_classes=["focused-page"]):
                page_header("Campaign")
                with gr.Row(elem_classes=["aligned-row"]):
                    campaign_picker = gr.Dropdown(label="Saved campaigns", choices=app.store.list_campaigns(), value=app.campaign.id, scale=3, interactive=True, allow_custom_value=False)
                    save_campaign = gr.Button("Save", variant="primary", scale=0, min_width=90)
                    open_campaign = gr.Button("Open", scale=1)
                with gr.Row(elem_classes=["compact-actions"]):
                    new_campaign = gr.Button("New campaign", variant="primary")
                    duplicate_campaign = gr.Button("Reuse with a new spreadsheet")
                gr.HTML('<div class="section-divider"></div>')
                name = gr.Textbox(label="Campaign name", value=app.campaign.name, interactive=True, elem_id="campaign-name")
                sender = gr.Dropdown(label="Send using", choices=[("Gmail", "gmail")], value="gmail", interactive=False)
                page_navigation(following=("recipients", "Next: Recipients"))

            with gr.Column(elem_id="page-dashboard", elem_classes=["focused-page"]):
                page_header("Campaigns")
                dashboard = gr.HTML("[]", html_template=asset("dashboard.html"),
                                    css_template=asset("dashboard.css"), js_on_load=asset("dashboard.js"))
                refresh_dashboard = gr.Button("Refresh campaigns", elem_classes=["fit-action"])
                gr.HTML('<a class="page-next" href="#/campaigns">Create a campaign &#8594;</a>')

            with gr.Column(elem_id="page-history", elem_classes=["focused-page"]):
                page_header("Campaign history")
                history_refresh = gr.HTML("", html_template='<span hidden></span>', js_on_load="document.addEventListener('mail-merger-history-open', () => {props.value=String(Date.now());trigger('input');});")
                history_title = gr.Markdown()
                with gr.Row():
                    history_state = gr.Dropdown(label="Outcome", choices=["All", "Accepted", "Failed", "Unknown", "Pending", "Cancelled", "Invalid", "Draft"], value="All", interactive=True)
                    history_column = gr.Dropdown(label="Show column", choices=app.campaign.headers, value=app.campaign.uid_column or app.campaign.email_column or None, interactive=True)
                    history_search = gr.Textbox(label="Filter column values", placeholder="Name, department, subject...")
                history_table = gr.Dataframe(headers=["Recipient", "Column value", "Outcome", "Attempts", "Details"], interactive=False, wrap=True, label="Recipient outcomes")
                retry_people = gr.CheckboxGroup(label="Unsent drafts: failed, unknown, cancelled and invalid recipients", choices=[], interactive=True)
                unknown_checked = gr.Checkbox(label="I checked Sent mail: selected unknown messages were not accepted", value=False)
                with gr.Row():
                    retry_draft = gr.Button("Create retry draft", variant="primary")
                    reuse_campaign = gr.Button("Reuse with a new spreadsheet")
                    history_resume = gr.Button("Resume pending")
                    history_pause = gr.Button("Pause sending")
                history_preview_recipient = gr.Dropdown(label="View recipient message", choices=[], interactive=True)
                history_subject = gr.Textbox(label="Subject", interactive=False)
                history_body = gr.HTML()
                history_files = gr.Markdown()
                history_log = gr.Textbox(label="Campaign log", lines=8, interactive=False)
                history_log_page = gr.Number(label="Log page (1 = newest)", value=1, minimum=1, precision=0, interactive=True)
                history_log_info = gr.Markdown()
                gr.HTML('<a class="page-back" href="#/dashboard">&#8592; All campaigns</a>')

            with gr.Column(elem_id="page-recipients", elem_classes=["focused-page"]):
                page_header("Recipients")
                with gr.Row(equal_height=True, elem_classes=["source-options"]):
                    with gr.Column(scale=1, min_width=240, elem_classes=["surface-panel", "source-card"]):
                        gr.Markdown("### Local file")
                        upload = gr.File(label="Excel or CSV", file_types=[".xlsx", ".xls", ".csv"], type="filepath", height=136, elem_id="recipient-upload")
                    with gr.Column(scale=1, min_width=240, elem_classes=["surface-panel", "source-card"]):
                        gr.Markdown("### Google Sheets")
                        sheet_url = gr.Textbox(label="Google Sheets sharing link", placeholder="https://docs.google.com/spreadsheets/d/…")
                        fetch = gr.Button("Load worksheets", elem_classes=["card-action"])
                with gr.Row(elem_classes=["aligned-row"]):
                    worksheet = gr.Dropdown(label="Worksheet", choices=[], interactive=True, scale=2)
                    import_button = gr.Button("Import", variant="primary", scale=1)
                exclude_duplicates = gr.Checkbox(label="Deselect duplicate email addresses", value=app.campaign.exclude_duplicate_emails)
                with gr.Accordion("Correct a recipient address", open=False):
                    correction_recipient = gr.Dropdown(label="Recipient to correct", choices=[], interactive=True)
                    correction_email = gr.Textbox(label="Corrected primary email address")
                    correct_address = gr.Button("Save corrected address", elem_classes=["fit-action"])
                with gr.Column(visible=False, elem_id="column-mapping", elem_classes=["surface-panel"]) as column_mapping:
                    gr.Markdown("### Identify recipients")
                    with gr.Row():
                        uid_column = gr.Dropdown(label="Unique ID column", choices=[], interactive=True)
                        target_email = gr.Dropdown(label="Recipient email column", choices=[], interactive=True)
                    build_recipients = gr.Button("Create recipients", variant="primary", scale=0, min_width=180, elem_classes=["fit-action"])
                with gr.Column(visible=bool(app.campaign.headers), elem_id="grouping-settings", elem_classes=["surface-panel"]) as grouping_settings:
                    collapse_columns = gr.CheckboxGroup(label="Collapse repeated values in", info="Checked columns keep distinct values. Unchecked columns keep every source row. The primary email address always collapses.", choices=[h for h in app.campaign.headers if h != app.campaign.email_column], value=app.campaign.collapse_columns, interactive=True)
                    apply_grouping = gr.Button("Apply grouping choices", visible=bool(app.campaign.recipients), elem_classes=["fit-action"])
                with gr.Column(visible=bool(app.campaign.recipients), elem_id="recipient-results") as recipient_results:
                    with gr.Accordion("Email addresses", open=True):
                        with gr.Row():
                            email_column = gr.Dropdown(label="Email column", choices=app.campaign.headers, value=app.campaign.email_column or None)
                            alternate_email_column = gr.Dropdown(label="Alternate email column (optional)", choices=[("None", ""), *[(h,h) for h in app.campaign.headers if h != app.campaign.email_column]], value=app.campaign.alternate_email_column, interactive=True)
                        with gr.Accordion("Additional copied addresses", open=False):
                            cc = gr.Textbox(label="CC", value=app.campaign.cc)
                            bcc = gr.Textbox(label="BCC", value=app.campaign.bcc)
                    grouping_analytics = gr.HTML(elem_id="grouping-analytics")
                    recipient_table = gr.HTML(table_value(app), html_template=asset("table.html"), css_template=asset("table.css"), js_on_load=asset("table.js"))
                    with gr.Accordion("Personalization fields", open=True):
                        fields = gr.CheckboxGroup(label="Confirmed merge fields", choices=app.campaign.headers, value=app.campaign.fields)
                page_navigation(("campaigns", "Campaign"), ("compose", "Next: Compose"))

            with gr.Column(elem_id="page-compose", elem_classes=["focused-page"]):
                page_header("Compose")
                editor = gr.HTML(app.editor_value(), html_template=asset("editor.html"), css_template=asset("editor.css"), js_on_load=asset("editor.js"))
                save_draft = gr.Button("Save draft", size="sm", scale=0, min_width=130, elem_classes=["fit-action"])
                page_navigation(("recipients", "Recipients"), ("attachments", "Next: Attachments"))

            with gr.Column(elem_id="page-attachments", elem_classes=["focused-page"]):
                page_header("Attachments")
                with gr.Column(elem_classes=["surface-panel"]):
                    attachment_upload = gr.Textbox(label="Local attachment paths", lines=3, placeholder="Choose files, or enter one full local path per line", elem_id="attachment-upload")
                    browse_attachments = gr.Button("Choose files from this computer", elem_classes=["fit-action"])
                    add_attachments = gr.Button("Add files", elem_classes=["fit-action"])
                    library_files = gr.CheckboxGroup(label="Files to assign", info="Select the files for this assignment.", choices=[], interactive=True)
                with gr.Column(elem_classes=["surface-panel"]):
                    gr.Markdown("### Assign files")
                    attachment_scope = gr.Radio(label="Send selected files to", choices=["Matching column values", "Everyone selected"], value="Matching column values")
                    attachment_columns = gr.CheckboxGroup(label="Match columns", choices=app.campaign.headers, value=[], interactive=True)
                    attachment_targets = gr.CheckboxGroup(label="Matching value groups", choices=[], value=[], interactive=True)
                    assign_attachments = gr.Button("Save file assignments", variant="primary", interactive=False, elem_classes=["fit-action"])
                    assignment_summary = gr.HTML()
                    attachment_notice = gr.Markdown(elem_id="attachment-notice")
                remove_files = gr.Button("Remove selected files", size="sm", elem_classes=["fit-action"])
                clear_legacy = gr.Button("Remove previous attachment assignments", visible=bool(app.campaign.shared or app.campaign.groups), elem_classes=["fit-action"])
                page_navigation(("compose", "Compose"), ("review", "Next: Review"))

            with gr.Column(elem_id="page-review", elem_classes=["focused-page"]):
                page_header("Review & send")
                preview_row = gr.Dropdown(label="Preview recipient", choices=[], interactive=True)
                preview_subject = gr.Textbox(label="Subject", interactive=False)
                preview_body = gr.HTML(frame(""))
                preview_info = gr.Markdown()
                build_review = gr.Button("Check campaign", scale=0, min_width=150, elem_classes=["fit-action"])
                report = gr.Markdown("")
                review_checks = gr.CheckboxGroup(label="I have checked", choices=checks, value=[])
                blank_ack = gr.Checkbox(label="I acknowledge the blank merge values listed above", value=False)
                with gr.Accordion("Sending speed", open=False):
                    with gr.Row():
                        worker_count = gr.Number(label="Workers", value=app.sending_preferences["workers"], minimum=1, maximum=8, precision=0)
                        sending_rate = gr.Number(label="Messages per minute", value=app.sending_preferences["per_minute"], minimum=1, maximum=300, precision=0)
                    apply_speed = gr.Button("Apply sending speed", elem_classes=["fit-action"])
                    speed_info = gr.Markdown(app.sending_speed_info(), elem_id="sending-speed-info")
                    gr.Markdown("Outlook: up to 4 workers and 30 messages/minute. Gmail: configured rate is a cap; account limits and throttling still apply.")
                with gr.Row(elem_classes=["send-actions"]):
                    send_button = gr.Button("Send", variant="primary", interactive=False)
                    pause_button = gr.Button("Pause", visible=False)
                    resume_button = gr.Button("Resume pending", visible=False)
                    cancel_button = gr.Button("Cancel remaining", visible=False)
                progress = gr.HTML()
                with gr.Accordion("Sending activity", open=True):
                    terminal = gr.Textbox(label="Sending activity", lines=8, max_lines=12, interactive=False, elem_id="send-terminal", autoscroll=True)
                page_navigation(previous=("attachments", "Attachments"))

            with gr.Column(elem_id="page-accounts", elem_classes=["focused-page"]):
                page_header("Accounts")
                with gr.Column(elem_id="account-card", elem_classes=["surface-panel"]):
                    gr.HTML('<div class="account-heading"><span class="account-google-icon" aria-hidden="true">' + asset("google.svg") + '</span><div><h2>Connect your Gmail</h2><p>Use your email and Google app password.</p></div></div>')
                    with gr.Row(visible="gmail" not in app.auth.accounts()) as signin_methods:
                        gmail = gr.Button("Sign in with Google", elem_id="google-sign-in")
                    with gr.Column(visible=False, elem_id="gmail-sign-in") as gmail_form:
                        gmail_email = gr.Textbox(label="Email address", type="email", placeholder="you@gmail.com", elem_id="gmail-address")
                        gmail_password = gr.Textbox(label="App password", type="password", max_length=64, placeholder="16-character app password")
                        gmail_connect = gr.Button("Sign in", variant="primary")
                        gr.HTML('<a class="app-password-link" href="https://myaccount.google.com/apppasswords" target="_blank" rel="noopener noreferrer">Get a Google app password <span aria-hidden="true">&#8599;</span></a>')
                    auth_status = gr.Markdown(elem_id="connection-status")
                    sign_out = gr.Button("Sign out", size="sm", visible="gmail" in app.auth.accounts())
                with gr.Accordion("History storage & encryption", open=False):
                    storage_location = gr.Textbox(label="History folder", value=str(app.store.root), interactive=False)
                    encryption_status = gr.Markdown("Encrypted for " + app.store.vault.owner if app.store.vault.enabled else "Encryption is off")
                    gr.Markdown("An email code is required after Gmail sign-in. Optional history encryption uses your Gmail app password; no separate passphrase is required.")
                    otp_sender = gr.Textbox(label="Code-sending Gmail account", value=app.store.vault.default_mail_sender(), placeholder="Your dedicated Gmail account")
                    otp_password = gr.Textbox(label="Code-sending account app password", type="password", placeholder="Leave blank to use the saved OTP sender")
                    enable_encryption = gr.Button("Encrypt history for my signed-in account", variant="primary", interactive=not app.store.vault.enabled)
                    export_recovery = gr.Button("Export encrypted recovery key")
                    import_otp_setup = gr.Button("Import private setup TXT")
                    export_otp_setup = gr.Button("Export private setup TXT (contains credentials)")
                    export_login_audit = gr.Button("Export login audit to Excel")
                    gr.Markdown("Sign-in times and session events are recorded locally. Google Sheets synchronisation requires an administrator-configured audit endpoint.")
                page_navigation(following=("campaigns", "Next: Campaign"))
    return locals()
