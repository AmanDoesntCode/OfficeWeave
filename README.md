# Mail-Merger

A local Windows desktop mail-merge application built with Python, Gradio, and pywebview. Send personalised Gmail messages from Excel, CSV, or a read-only Google Sheets sharing link.

## Start the app

Windows x64, Python 3.12, and the [Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/) are required for source installation.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

You can also double-click `start-mail-merger.bat`. With an activated environment, `python app.py` works. If Windows Python aliases are unavailable, use your installed interpreter's full path to create the environment.

The portable distribution includes Python and the app dependencies. Extract **all** files in `Mail-Merger-Windows-x64.zip`, then open `Mail-Merger\Mail-Merger.exe`. Keep the directory together. WebView2 must be installed. The executable is unsigned.

The app automatically creates **Documents\Mail-Merger** on first start. Windows' configured Documents location is honoured, including redirected Documents folders. The app itself provides no hosted storage or cloud synchronisation; your Windows folder-sync settings still apply.

The desktop interface uses a random authenticated local session on `127.0.0.1`. Gradio sharing, analytics, and request-history recording are disabled. Only one app instance may use a history folder at a time. Development options:

```powershell
.\.venv\Scripts\python.exe app.py --browser
.\.venv\Scripts\python.exe app.py --data-dir "D:\My Mail-Merger History"
```

Native file pickers, account-first startup, and the close overlay are designed for the desktop window. Browser development mode asks for Gmail authentication and its email code in the terminal before opening the interface.

## Sign in

Open **Accounts > Sign in with Google**, enter your email address and a [Google app password](https://myaccount.google.com/apppasswords), then choose **Sign in**. This is Gmail SMTP authentication, not Google browser OAuth. Signing in verifies the account without sending a message. Google requires 2-Step Verification for app passwords. Spaces in the 16-character password are removed automatically.

Only one sending account can be connected. Saved Gmail sign-in credentials use Windows DPAPI; campaign-history encryption described below is portable and is **not bound to a Windows account**. The desktop app never reads the legacy `.env` file. Microsoft email integration is removed.

Closing the window displays **Stay signed in and close application**, **Sign out and close application**, and **Keep working** over a dimmed background. Closing pauses dispatch, waits for in-flight work, and saves progress. Forced process termination cannot display a prompt. In encrypted mode, **Sign out and close history** closes the account's unlocked session.

## Campaigns and history

**All campaigns** is the landing page after sign-in. It lists drafts and sent campaigns with recipient, accepted, failed, and unknown counts. **View history** opens a campaign's saved results. The dashboard refreshes when you return to it; **Refresh campaigns** is also available.

History offers:

- Outcome filters, including accepted, failed, unknown, pending, cancelled, and invalid recipients.
- A **Show column** selector and value search, for example department, professor, employee ID, or subject.
- Recipient previews generated from saved recipient values and the original subject/body template when selected. Resolved messages are not stored.
- Campaign logs and **Resume pending** / **Pause sending**.
- A separate **Unsent drafts** list. Choose recipients and **Create retry draft**, correct addresses if needed, then review and approve before sending.

Accepted messages cannot enter a retry draft. Unknown outcomes require confirmation that you checked Sent mail and the messages were **not accepted**. This acknowledgement cannot establish delivery automatically: the provider may have accepted a message without returning its response. Retry drafts retain a link to the original history; a persisted claim prevents creating multiple retry drafts for the same original outcome. Open the existing retry draft instead. Its own subsequent outcomes can be handled separately.

**Reuse** or **Reuse with a new spreadsheet** starts a new campaign with the saved subject, body, confirmed fields, unique-ID mapping, email mapping, and duplicate-address policy. Recipients, attachment assignments, and send approval reset. Import an Excel/CSV file or Google Sheets link again. Existing headers are preselected when they still exist; missing merge fields must be fixed before review. The original recipients and results remain available in history.

**Campaign > Save** explicitly saves the campaign name and current template. Content edits save automatically. **Open** loads an existing record, and **New campaign** starts a fresh workspace.

## Recipients and compose

Upload `.xlsx`, `.xls`, or UTF-8 `.csv`, or paste a Google Sheets link shared as **Anyone with the link > Viewer**. Sheets import downloads a read-only workbook and never changes the source. No separate Sheets OAuth, API key, or Google registration is needed. The owner must permit downloading; restricted links require a local file import.

Select a worksheet, **Import**, choose a **Unique ID column** and primary email column, then **Create recipients**. Up to 1,000 source rows and 20 MB input files are supported. Headers must be unique and nonempty for columns containing data. Entirely empty spacer columns are ignored. Common dates, leading zeros, and Excel display formats are preserved. Formula values depend on the workbook's last saved cached results.

Rows with the same UID become one recipient and one email. In **Collapse repeated values in**, choose each column independently: check **Name** to show a professor once, and leave **Paper Code** and **Paper Name** unchecked to retain every paper in source order. The primary email always collapses. Use **Apply grouping choices** to change the selection while the imported source is still open. Choices persist when saving or reusing a campaign. After reopening, reimport the spreadsheet before changing them because compact history does not retain a second copy of source rows. Conflicting primary addresses for one UID must be corrected. **Deselect duplicate email addresses** is optional and off by default; distinct UIDs may intentionally share a destination.

Four cards above the table show source rows, recipients, rows with collapsed values, and source rows affected by possible pairing issues. Hover or keyboard-focus the risk card to inspect **every affected recipient and all their original rows** in a scrollable view. The other cards show up to three recipients and three sample rows each. After reopening a saved snapshot, the risk card shows all affected recipients' saved grouped values; reimport the sheet to inspect original rows. Partial collapse is highlighted as a potential risk; actual conflicts between fields used in the same list item block review and sending. Original row positions are checked as well as value counts, so two collapsed columns with the same number of values cannot silently pair unrelated rows.

The recipient table supports search, paging, selection, and an optional alternate-email column. Empty alternate cells are allowed. Alternate addresses default to CC; the row selector can switch them to BCC. Fixed additional CC/BCC addresses are in the **Email addresses** drawer. Duplicate destinations are removed. Gmail BCC destinations are only in the SMTP envelope, never in the message headers.

Subject, rich-text body, and HTML source support `${Column Name}` fields. Type `${` for suggestions or use Insert field. Header matching is exact and case-sensitive after trimming. Hover previews show current/sample values. The formatting toolbar supports fonts, sizes, colour, highlight, lists, alignment, links, and common text styles. Substituted values are escaped and previews are sanitised.

A grouped field inside an ordered or unordered list item expands into consecutive list items. Outside a list, body values use line breaks; subject values use commas. Ambiguous multi-field list lengths fail review instead of pairing unrelated values.

## Attachments without copies

Use **Choose files from this computer** for the native file picker, or enter one absolute local path per line, then **Add files**. The app records only the name, absolute path, size, and SHA-256 fingerprint. It does not upload or copy attachment content into a storage bucket.

Choose files and assign them to **Everyone selected** or **Matching column values**. Multiple-column matches must occur in the same original spreadsheet row. Grouped recipients receive the union of their matching attachments, deduplicated by content. Once saved, compact recipient-to-file memberships preserve those exact assignments without storing a duplicate source-row snapshot.

Keep referenced originals at their locations. Every review and submission checks that the file still exists and its bytes match the recorded fingerprint. Moved or changed files must be added and reviewed again. Removing a reference never deletes the original. To change column-based rules for grouped recipients after reopening, import the spreadsheet again; existing saved assignments still work.

Individual files must be smaller than 3 MB and the fully encoded request smaller than 4,000,000 bytes. Files are read only for review and submission. Their originals remain **outside** optional history encryption.

## What is saved

Documents\Mail-Merger contains:

- `campaigns\<id>.json`: grouped recipient values, raw subject/body templates, field/column settings, attachment references/memberships, review/run identifiers, and parent-campaign links. Encrypted mode stores an authenticated ciphertext envelope instead.
- `mail-merger.sqlite3`: the campaign index, delivery states/attempts, retry-draft links, logs, and non-secret sending preferences. Sensitive names, details, and log fields are encrypted when encryption is enabled; status/count/timing metadata remains readable.
- `credentials\`: Windows-protected sender sign-in, if retained.
- `protection.json`: optional account-owned, passphrase-encrypted private key, wrapped data key, and encrypted code-sender configuration.

Original spreadsheets, duplicate raw source rows, rendered individual emails, encoded MIME messages, and new attachment copies are not saved. Temporary spreadsheet uploads are removed after import and cleaned at startup/shutdown. The original spreadsheets and attachment originals remain yours.

On first default-folder launch, the previous `%LOCALAPPDATA%\Mail-Merger` database and sign-in configuration are imported. The old directory is left intact as a fallback. Old attachment copies have no recoverable original location, so migrated records retain references to them until you select the originals again. Encrypting the new Documents history does not encrypt or erase that previous directory or your own backups.

## Optional account-owned encryption

New desktop installations prompt for a private `.txt` setup file. Use **Accounts >
History storage & encryption > Export private setup TXT** on an administrator's
configured installation, then distribute that TXT privately. It contains the OTP
sender app password and optional audit settings. New users select it with the
native picker; it is read locally, verified with Gmail, and saved using Windows
credential protection. The TXT itself is not copied into the app or uploaded.
`examples/otp-setup.example.txt` documents the format without real credentials.
Signing out of the sending account does not remove the installation's OTP sender.

Gmail sign-in always requires **email address + Google app password, then an emailed OTP**. OTP is required whether history encryption is on or off. It is sent to the Gmail account being signed in, not the installation's code-sending account. Signing out closes the workspace; the next sign-in requires a new code. Continuing a saved account also requests a fresh code.

Each Gmail account gets its own history folder under `Documents\Mail-Merger\accounts`. Another account can sign in without unlocking someone else's history. Existing history is retained for its recorded owner. No campaigns or recipients are loaded until Gmail authentication and the email code succeed.

Codes expire in five minutes, can be resent once per minute, and five incorrect attempts trigger a five-minute cooldown. Codes and login passwords are never included in the audit Sheet.

History encryption remains optional. To enable it, open **Accounts > History storage & encryption** and select **Encrypt history for my signed-in account**. The app uses your existing Gmail app password to protect the portable history key. There is no separate passphrase to create or enter. The app password supplies cryptographic protection; OTP is the application's local access check, not server-enforced key custody.

Previously created passphrase-encrypted histories cannot be decrypted without their previous password. After verifying Gmail and OTP, the app offers a **one-time history upgrade** using that old password. Afterwards, the same history opens with the Gmail app password and OTP. You can instead open a fresh workspace while retaining the old encrypted files. If your Google app password changes later, the previous password/key is needed once to upgrade the history key.

Export the encrypted recovery key and keep backups of your history folder. To move history to another PC, copy the history folder, import the private installation TXT, then sign in with the corresponding Gmail app password and email code. Attachment paths may need updating. There is no Windows-account binding for the encrypted history key; the installation's saved SMTP settings still use Windows credential protection.

Back up the history folder while the app is closed. Encryption protects local sensitive content at rest; it does not encrypt Gmail's Sent copies, attachment originals, old history folders, or other backups.

## Login audit and Google Sheets

Sign-in success/failure, sign-out, application open/close, and encrypted-history
unlock events are recorded in a durable local audit log. It contains UTC time,
account email, event/result, app version, and random installation/session IDs.
Passwords, OTP codes, message contents, and mail recipient lists are never included.
Sensitive audit payloads use the history key when encryption is enabled.
**Export login audit to Excel** exports the recorded events to a `Logins` worksheet.

An optional Google Apps Script receiver synchronises events to a Google Sheet.
A Gmail app password cannot authorise Sheets writes. The administrator must deploy
the receiver once using a Google-authorised account and add `audit_url` and
`audit_secret` to the private setup TXT. See `tools/google-login-audit/README.md`.
Pending events survive restarts; signed requests and receiver-side Event ID
deduplication prevent duplicate rows when an acknowledgement is lost. Without an
endpoint, auditing stays local. This audit integration is the only new hosted data
component; campaigns and attachments continue to run locally.

## Sending, validation, and packaging

Review checks recipients, fields, blanks, content, attachment assignments, file fingerprints, authentication, and encoded size before approval. Changes invalidate approval. Sending uses reusable SMTP sessions, a bounded queue of 100 lightweight jobs, and two workers / 30 messages per minute by default. Review's **Sending speed** allows 1–8 workers and 1–300 messages per minute. Account quotas and provider limits still apply.

Definitely rejected or never-submitted transient requests receive three retries after the initial attempt. Exhausted retries pause the campaign. Authentication/quota problems pause immediately. A connection failure after submission becomes **unknown** and pauses dispatch. Accepted and unknown submissions are never automatically resent, including after restart. **Accepted by provider** is not a delivery receipt.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe tools\stress_test.py
.\build-desktop.ps1
```

Tests use simulated providers and never send real mail. UI tests use headless Edge. The stress report covers four accelerated 1,000-message scenarios, including retries, restart recovery, and unknown outcomes, in `test-results\stress-1000.json`. A legacy Outlook-format serializer is exercised for historical compatibility; Microsoft sending is not available in the app.

Each release gets a matching versioned folder and ZIP directly inside `dist`, for example `Mail-Merger-v1.0.1-Windows-x64\Mail-Merger.exe` and `Mail-Merger-v1.0.1-Windows-x64.zip`. Keep the entire extracted folder together. `build-desktop.ps1` synchronizes the app/project version and automatically increments the patch version when that release already exists, preserving previous releases. Use `./build-desktop.ps1 -Version 1.1.0` to choose a newer version explicitly. All app assets and the Python runtime are bundled. Live Gmail delivery and a real OTP mailbox must be tested with your own accounts; automated simulations do not claim to validate a live provider.

The original CLI remains available through `python automate_emails.py --help`; it has no sending or argument-parsing side effects during import. Its `.env` configuration is separate from the desktop application.
