# Google Sheets login audit

A Gmail app password authenticates SMTP; it does not authorise Google Sheets writes.
Use a Google-authorised Apps Script deployment as the receiver. Do not place a Gmail
password in this script or in its source repository.

1. Open **MailMerger user data**:
   https://docs.google.com/spreadsheets/d/1xixbvkNWk6I9iqTydbZ-P-OuhFzGyvjKpOAK_NJdkRI/edit
   Its `Logins` tab is already prepared with the eight audit columns.
2. As the owner account, open **Extensions > Apps Script**, paste `Code.gs`, and save.
3. Choose `configureAudit` in the function dropdown and **Run**. Authorise Google
   access when asked. It sets the target ID and generates a private audit secret.
   Run `showAuditSecret` to copy this value; keep it private. No Gmail password is
   required in Apps Script and no secret is written into the Sheet.
4. Deploy as a **Web app**, executing as yourself. Allow requests from app users
   (the receiver authenticates every payload using an HMAC). Authorise Google once.
5. Add the deployment's `/exec` URL as `audit_url` and the matching secret as
   `audit_secret` in the private installation TXT, alongside `email` and `app_password`.
6. Import this TXT in Mail-Merger. For other PCs, privately distribute the same setup
   TXT. Each installation records its own random installation/session IDs.

The app records UTC time, account email, event/result, app version, installation ID,
and session ID. It never uploads passwords, OTP codes, email bodies, recipient lists,
or attachment contents. Configuration-less installations keep events locally.
Unacknowledged uploads stay in a persistent outbox; the receiver deduplicates Event ID
so retrying a lost acknowledgement cannot duplicate a row.

This receiver adds a Google-hosted audit service. Campaign data and sending still run
locally. Each recipient of the private setup file can use its Gmail sender and audit
credentials, so share it only with authorised app users. The setup is not embedded in
the portable executable.
