/** Run configureAudit once, then deploy as a web app (execute as owner).
 * showAuditSecret displays the secret to copy into the private setup TXT.
 * The endpoint accepts sign-in metadata only, never passwords or OTP codes.
 */
const TARGET_SPREADSHEET_ID = '1xixbvkNWk6I9iqTydbZ-P-OuhFzGyvjKpOAK_NJdkRI';

function configureAudit() {
  const properties = PropertiesService.getScriptProperties();
  properties.setProperty('SPREADSHEET_ID', TARGET_SPREADSHEET_ID);
  if (!properties.getProperty('AUDIT_SECRET')) {
    properties.setProperty('AUDIT_SECRET', (Utilities.getUuid() + Utilities.getUuid()).replace(/-/g,''));
  }
  const book = SpreadsheetApp.openById(TARGET_SPREADSHEET_ID);
  const sheet = book.getSheetByName('Logins') || book.insertSheet('Logins');
  if (sheet.getLastRow() === 0) sheet.appendRow(['Event ID','Time (UTC)','Account email','Event','Result','App version','Session ID','Installation ID']);
  sheet.setFrozenRows(1);
  SpreadsheetApp.getUi().alert('Audit receiver configured. Deploy as a Web app, executing as yourself. Run showAuditSecret to copy the audit_secret into your private setup TXT.');
}

function showAuditSecret() {
  const secret = PropertiesService.getScriptProperties().getProperty('AUDIT_SECRET');
  if (!secret) throw new Error('Run configureAudit first.');
  const escaped = secret.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  const content = '<p>Copy this as audit_secret in the private installation TXT. Do not put it in spreadsheet cells or public source.</p><textarea readonly style="width:100%;height:85px;font:14px monospace">' + escaped + '</textarea>';
  SpreadsheetApp.getUi().showModalDialog(HtmlService.createHtmlOutput(content).setWidth(480).setHeight(190), 'Private audit settings');
}

function doPost(e) {
  let lock;
  try {
    const properties = PropertiesService.getScriptProperties();
    const secret = properties.getProperty('AUDIT_SECRET');
    const id = properties.getProperty('SPREADSHEET_ID');
    if (!secret || secret.length < 32 || !id) return reply({ok:false});
    const request = JSON.parse(e.postData.contents);
    const expected = Utilities.computeHmacSha256Signature(request.payload, secret)
      .map(value => ('0' + ((value + 256) % 256).toString(16)).slice(-2)).join('');
    let difference = expected.length ^ String(request.signature || '').length;
    for (let i=0; i<expected.length; i++) difference |= expected.charCodeAt(i) ^ String(request.signature || '').charCodeAt(i);
    if (difference !== 0) return reply({ok:false});
    const event = JSON.parse(request.payload);
    const fields = ['event_id','time_utc','account_email','event','result','app_version','session_id','installation_id'];
    if (!/^[a-f0-9]{32}$/.test(event.event_id) || fields.some(key => typeof event[key] !== 'string' || event[key].length > 500)) return reply({ok:false});
    if (!['app_open','app_close','sign_in','sign_out','history_unlock'].includes(event.event)) return reply({ok:false});
    lock = LockService.getScriptLock();
    if (!lock.tryLock(10000)) return reply({ok:false});
    const book = SpreadsheetApp.openById(id);
    const sheet = book.getSheetByName('Logins') || book.insertSheet('Logins');
    if (sheet.getLastRow() === 0) {
      sheet.appendRow(['Event ID','Time (UTC)','Account email','Event','Result','App version','Session ID','Installation ID']);
      sheet.setFrozenRows(1);
      sheet.getRange('A1:H1').setBackground('#f0f2f5').setFontColor('#202124').setFontWeight('bold');
    }
    if (sheet.getLastRow() > 1 && sheet.getRange(2,1,sheet.getLastRow()-1,1).createTextFinder(event.event_id).matchEntireCell(true).findNext()) {
      return reply({ok:true,event_id:event.event_id});
    }
    const row = fields.map(key => /^[=+@-]/.test(event[key]) ? "'" + event[key] : event[key]);
    sheet.appendRow(row);
    return reply({ok:true,event_id:event.event_id});
  } catch (_) {
    return reply({ok:false});
  } finally {
    if (lock && lock.hasLock()) lock.releaseLock();
  }
}

function reply(value) {
  return ContentService.createTextOutput(JSON.stringify(value)).setMimeType(ContentService.MimeType.JSON);
}
