const root = element.querySelector('.merge-editor');
const subject = root.querySelector('.subject');
const rich = root.querySelector('.rich');
const source = root.querySelector('.source');
const toggle = root.querySelector('.source-toggle');
const toolbar = root.querySelector('.toolbar');
const suggestions = root.querySelector('.suggestions');
const chips = root.querySelector('.field-chips');
const tooltip = root.querySelector('.field-tooltip');
let data = {}, sourceMode = false, target = null, match = null, savedRange = null, options = [], active = 0, saveTimer, tooltipTimer;
let localDirty = false, lastPublished = null;
let bodyRange = null;
const colors = ['#000000','#434343','#666666','#999999','#cccccc','#ffffff','#cc0000','#e69138','#f1c232','#38761d','#0b5394','#674ea7','#a64d79','#f4cccc','#fce5cd','#fff2cc','#d9ead3','#cfe2f3','#d9d2e9','#ead1dc'];
const colorPanel = root.querySelector('.color-panel');
const linkPanel = root.querySelector('.link-panel');
const styleProperties = ['color','background-color','font-family','font-size','font-weight','font-style','text-decoration','text-align','margin-left'];
const allowedTags = new Set(['P','BR','DIV','SPAN','STRONG','B','EM','I','U','S','UL','OL','LI','A','H1','H2','H3','BLOCKQUOTE','PRE','CODE','TABLE','THEAD','TBODY','TR','TH','TD']);
function cleanHTML(value) {
  const template = document.createElement('template');
  template.innerHTML = value;
  for (const node of [...template.content.querySelectorAll('*')]) {
    if (node.tagName === 'FONT') {
      const span = document.createElement('span');
      if (node.color) span.style.color = node.color;
      if (node.face) span.style.fontFamily = node.face;
      if (node.size) span.style.fontSize = ({1:10,2:13,3:16,4:18,5:24,6:32,7:48}[node.size] || 16) + 'px';
      span.append(...node.childNodes); node.replaceWith(span);
      continue;
    }
    if (['SCRIPT','STYLE','IFRAME','OBJECT','IMG','LINK','META'].includes(node.tagName)) { node.remove(); continue; }
    if (!allowedTags.has(node.tagName)) { node.replaceWith(...node.childNodes); continue; }
    for (const attr of [...node.attributes]) {
      const listNumber = ((node.tagName === 'OL' && attr.name === 'start') || (node.tagName === 'LI' && attr.name === 'value')) && /^[0-9]{1,6}$/.test(attr.value);
      if (!(node.tagName === 'A' && ['href','title'].includes(attr.name)) && attr.name !== 'style' && !listNumber) node.removeAttribute(attr.name);
    }
    const safeStyle = document.createElement('span').style;
    for (const property of styleProperties) {
      const value = node.style.getPropertyValue(property);
      if (value && !/[{}\\]|url\s*\(|expression|var\s*\(/i.test(value)) safeStyle.setProperty(property, value);
    }
    node.removeAttribute('style');
    if (safeStyle.cssText) node.setAttribute('style', safeStyle.cssText);
    if (node.tagName === 'A' && node.hasAttribute('href')) {
      const href = node.getAttribute('href').trim();
      if (!/^(https?:|mailto:)/i.test(href) && !href.includes('${')) node.removeAttribute('href');
    }
  }
  return template.innerHTML;
}
function sync() {
  if (props.value === lastPublished) return;
  let incoming;
  try { incoming = JSON.parse(props.value || '{}'); } catch { incoming = {}; }
  const sameCampaign = incoming.campaign_id === data.campaign_id;
  if (!sameCampaign) {
    clearTimeout(saveTimer);
    sourceMode = false; target = null; match = savedRange = bodyRange = null;
    source.value = '';
    source.hidden = true; rich.hidden = false; toolbar.hidden = false;
    toggle.textContent = 'HTML source'; hideSuggestions(); hideValues();
    colorPanel.hidden = linkPanel.hidden = true;
    root.querySelector('.link-url').value = '';
    root.querySelector('.link-error').textContent = '';
    root.querySelector('.color-toggle').setAttribute('aria-expanded', 'false');
    root.querySelector('.font-family').value = 'Arial';
    root.querySelector('.font-size').value = '3';
    root.querySelector('.alignment').value = 'justifyLeft';
    toolbar.querySelectorAll('[aria-pressed]').forEach(button => button.setAttribute('aria-pressed', 'false'));
  }
  const localBody = sourceMode ? source.value : rich.innerHTML;
  const matchesLocal = incoming.subject === subject.value && cleanHTML(incoming.body || '') === cleanHTML(localBody);
  const stale = sameCampaign && Number(incoming.revision || 0) < Number(data.revision || 0);
  const preserveDraft = sameCampaign && !incoming.locked && ((localDirty && !matchesLocal) || stale);
  if (preserveDraft) {
    data = {...incoming, revision: Math.max(incoming.revision || 0, data.revision || 0), subject: subject.value, body: localBody};
    lastPublished = JSON.stringify(data);
    props.value = lastPublished;
  } else {
    data = incoming;
    localDirty = false;
  }
  if (subject.value !== (data.subject || '')) subject.value = data.subject || '';
  const safe = cleanHTML(data.body || '');
  if (!sourceMode && cleanHTML(rich.innerHTML) !== safe) { rich.innerHTML = safe; bodyRange = null; }
  if (sourceMode && source.value !== (data.body || '')) source.value = data.body || '';
  subject.disabled = !!data.locked;
  source.disabled = !!data.locked;
  rich.contentEditable = data.locked ? 'false' : 'true';
  root.querySelectorAll('.toolbar button, .toolbar select, .editor-popover button, .editor-popover input, .insert-subject-field, .insert-body-field').forEach(btn => btn.disabled = !!data.locked);
  chips.replaceChildren();
  for (const field of data.fields || []) {
    const button = document.createElement('button');
    button.type = 'button'; button.textContent = '${' + field + '}'; button.disabled = !!data.locked;
    button.addEventListener('mouseenter', event => showValues(field, event));
    button.addEventListener('mouseleave', scheduleHide);
    button.addEventListener('mousedown', event => event.preventDefault());
    button.addEventListener('click', () => { insertField(field, false); });
    chips.append(button);
  }
}
function publish() {
  localDirty = true;
  data = {...data, subject: subject.value, body: sourceMode ? source.value : cleanHTML(rich.innerHTML)};
  lastPublished = JSON.stringify(data);
  props.value = lastPublished;
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => trigger('input'), 350);
}
function context(input) {
  target = input; match = null; savedRange = null;
  if (input === rich) {
    const selection = window.getSelection();
    if (!selection.rangeCount || !rich.contains(selection.anchorNode) || !selection.isCollapsed) { hideSuggestions(); return; }
    const range = selection.getRangeAt(0);
    const node = range.startContainer.nodeType === Node.TEXT_NODE ? range.startContainer.parentElement : range.startContainer;
    const block = node.closest('p,li,div,h1,h2,h3,blockquote,pre,td,th') || rich;
    const prefix = range.cloneRange(); prefix.selectNodeContents(block); prefix.setEnd(range.endContainer, range.endOffset);
    const text = prefix.toString(); match = text.match(/\$\{([^{}]*)$/);
    if (match) {
      const walker = document.createTreeWalker(block, NodeFilter.SHOW_TEXT);
      let remaining = text.length - match[0].length, current;
      while ((current = walker.nextNode())) {
        if (remaining <= current.textContent.length) {
          savedRange = range.cloneRange(); savedRange.setStart(current, remaining); break;
        }
        remaining -= current.textContent.length;
      }
    }
  } else {
    match = input.value.slice(0, input.selectionStart).match(/\$\{([^{}]*)$/);
  }
  if (match) showSuggestions(match[1]); else hideSuggestions();
}
function hideSuggestions() {
  if (suggestions.matches(':popover-open')) suggestions.hidePopover();
  suggestions.hidden = true;
}
function showSuggestions(query) {
  options = (data.fields || []).filter(field => field.toLowerCase().includes(query.toLowerCase()));
  active = 0; drawSuggestions();
}
function drawSuggestions() {
  suggestions.replaceChildren();
  suggestions.hidden = false;
  if (suggestions.showPopover && !suggestions.matches(':popover-open')) suggestions.showPopover();
  if (!options.length) {
    const empty = document.createElement('div'); empty.className = 'empty-fields';
    empty.textContent = (data.fields || []).length ? 'No matching columns' : 'Import recipients to add column fields';
    suggestions.append(empty);
  }
  options.forEach((field, i) => {
    const button = document.createElement('button');
    button.type = 'button'; button.textContent = field; button.setAttribute('role', 'option');
    button.className = i === active ? 'active' : '';
    button.setAttribute('aria-selected', String(i === active));
    button.addEventListener('mousedown', event => event.preventDefault());
    button.addEventListener('click', () => insertField(field, true)); suggestions.append(button);
  });
  positionSuggestions();
}
function positionSuggestions() {
  if (suggestions.hidden) return;
  if (!root.getClientRects().length) { hideSuggestions(); return; }
  const anchor = target === subject ? subject : target === source ? source : rich;
  let box = anchor.getBoundingClientRect();
  if (target === rich && savedRange) {
    const caret = savedRange.cloneRange(); caret.collapse(false);
    const bounds = caret.getBoundingClientRect(); if (bounds.height) box = bounds;
  }
  const height = Math.min(190, suggestions.scrollHeight + 2);
  const below = box.bottom + 4;
  suggestions.style.top = Math.max(8, below + height <= innerHeight - 8 ? below : box.top - height - 4) + 'px';
  suggestions.style.left = Math.max(8, Math.min(box.left, innerWidth - Math.min(320, innerWidth - 24) - 8)) + 'px';
}
function insertField(field, replacePrefix) {
  if (data.locked) return;
  const token = '${' + field + '}';
  if (!target) target = sourceMode ? source : rich;
  if (target === rich) {
    rich.focus();
    const selection = window.getSelection();
    if (replacePrefix && match && savedRange) {
      const range = savedRange.cloneRange();
      range.deleteContents(); const text = document.createTextNode(token); range.insertNode(text);
      range.setStartAfter(text); range.collapse(true); selection.removeAllRanges(); selection.addRange(range);
    } else { restoreSelection(); document.execCommand('insertText', false, token); }
  } else {
    const end = target.selectionStart ?? target.value.length;
    const start = replacePrefix && match ? end - match[0].length : end;
    target.setRangeText(token, start, end, 'end'); target.focus();
  }
  hideSuggestions(); match = null; publish();
}
function showValues(field, event) {
  clearTimeout(tooltipTimer);
  const current = data.values?.[field] ?? '';
  const values = (data.samples?.[field] || []).map((value, i) => (i + 1) + '. ' + (value || '(blank)'));
  tooltip.textContent = field + '\nSelected recipient: ' + (current || '(blank)') + '\n\nSample values (up to 50):\n' + values.join('\n');
  tooltip.style.left = Math.min(event.clientX + 12, window.innerWidth - 350) + 'px';
  tooltip.style.top = Math.min(event.clientY + 16, window.innerHeight - 260) + 'px';
  tooltip.hidden = false;
}
function hideValues() { tooltip.hidden = true; }
function scheduleHide() { tooltipTimer = setTimeout(hideValues, 300); }
tooltip.addEventListener('mouseenter', () => clearTimeout(tooltipTimer));
tooltip.addEventListener('mouseleave', hideValues);
for (const input of [subject, rich, source]) {
  input.addEventListener('input', () => { publish(); context(input); });
  input.addEventListener('focus', () => { target = input; });
  input.addEventListener('keyup', event => { if (!['ArrowDown','ArrowUp','Enter','Escape'].includes(event.key)) context(input); });
  input.addEventListener('keydown', event => {
    if (suggestions.hidden) return;
    if (!options.length) { if (event.key === 'Escape') hideSuggestions(); return; }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault(); active = (active + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length; drawSuggestions();
    } else if (event.key === 'Enter') { event.preventDefault(); insertField(options[active], true); }
    else if (event.key === 'Escape') hideSuggestions();
  });
}
rich.addEventListener('paste', event => {
  event.preventDefault();
  const value = event.clipboardData.getData('text/html');
  if (value) document.execCommand('insertHTML', false, cleanHTML(value));
  else document.execCommand('insertText', false, event.clipboardData.getData('text/plain'));
  publish();
});
rich.addEventListener('mousemove', event => {
  const range = document.caretRangeFromPoint?.(event.clientX, event.clientY);
  if (range && range.startContainer.nodeType === Node.TEXT_NODE && rich.contains(range.startContainer)) {
    const text = range.startContainer.textContent;
    for (const found of text.matchAll(/\$\{([^{}]+)\}/g)) {
      if (range.startOffset >= found.index && range.startOffset <= found.index + found[0].length) { showValues(found[1], event); return; }
    }
  }
  hideValues();
});
rich.addEventListener('mouseleave', scheduleHide);
subject.addEventListener('mousemove', event => {
  const style = getComputedStyle(subject), box = subject.getBoundingClientRect();
  const canvas = document.createElement('canvas'), context = canvas.getContext('2d'); context.font = style.font;
  const offset = event.clientX - box.left - parseFloat(style.paddingLeft) + subject.scrollLeft;
  for (const found of subject.value.matchAll(/\$\{([^{}]+)\}/g)) {
    const start = context.measureText(subject.value.slice(0, found.index)).width;
    const end = start + context.measureText(found[0]).width;
    if (offset >= start && offset <= end) { showValues(found[1], event); return; }
  }
  hideValues();
});
subject.addEventListener('mouseleave', scheduleHide);
for (const [selector, input] of [['.insert-subject-field', subject], ['.insert-body-field', rich]]) {
  const button = root.querySelector(selector);
  button.addEventListener('mousedown', event => event.preventDefault());
  button.addEventListener('click', () => {
    if (data.locked) return;
    target = input === rich && sourceMode ? source : input; match = null;
    target.focus(); showSuggestions('');
  });
}
function rememberSelection() {
  const selection = window.getSelection();
  if (selection.rangeCount && rich.contains(selection.anchorNode) && rich.contains(selection.focusNode)) {
    bodyRange = selection.getRangeAt(0).cloneRange();
    for (const command of ['bold','italic','underline','strikeThrough','insertOrderedList','insertUnorderedList']) {
      root.querySelector(`[data-command="${command}"]`)?.setAttribute('aria-pressed', String(document.queryCommandState(command)));
    }
  }
}
document.addEventListener('selectionchange', rememberSelection);
function restoreSelection() {
  rich.focus();
  if (bodyRange && rich.contains(bodyRange.commonAncestorContainer)) {
    const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(bodyRange);
  }
}
function execute(command, value = null) {
  if (data.locked || sourceMode) return;
  restoreSelection();
  document.execCommand('styleWithCSS', false, true);
  document.execCommand(command, false, value);
  rememberSelection(); publish();
}
toolbar.querySelectorAll('button[data-command]').forEach(button => {
  button.addEventListener('mousedown', event => event.preventDefault());
  button.addEventListener('click', () => execute(button.dataset.command, button.dataset.value || null));
});
for (const [selector, command] of [['.font-family','fontName'],['.font-size','fontSize'],['.alignment',null]]) {
  root.querySelector(selector).addEventListener('change', event => execute(command || event.target.value, command ? event.target.value : null));
}
const colorToggle = root.querySelector('.color-toggle');
colorToggle.addEventListener('mousedown', event => event.preventDefault());
colorToggle.addEventListener('click', () => {
  colorPanel.hidden = !colorPanel.hidden; linkPanel.hidden = true;
  colorToggle.setAttribute('aria-expanded', String(!colorPanel.hidden));
});
for (const [selector, command, label] of [['.text-colors','foreColor','Text color'],['.background-colors','hiliteColor','Background color']]) {
  for (const color of colors) {
    const button = document.createElement('button'); button.type = 'button'; button.style.backgroundColor = color;
    button.setAttribute('aria-label', label + ' ' + color); button.title = color;
    button.addEventListener('mousedown', event => event.preventDefault());
    button.addEventListener('click', () => { execute(command, color); colorPanel.hidden = true; colorToggle.setAttribute('aria-expanded','false'); });
    root.querySelector(selector).append(button);
  }
}
root.querySelector('.link-toggle').addEventListener('mousedown', event => event.preventDefault());
root.querySelector('.link-toggle').addEventListener('click', () => {
  linkPanel.hidden = !linkPanel.hidden; colorPanel.hidden = true;
  root.querySelector('.link-error').textContent = '';
  root.querySelector('.link-url').focus();
});
root.querySelector('.apply-link').addEventListener('click', () => {
  const value = root.querySelector('.link-url').value.trim();
  if (!/^(https?:\/\/|mailto:)/i.test(value)) { root.querySelector('.link-error').textContent = 'Use an https://, http:// or mailto: link.'; return; }
  restoreSelection();
  if (window.getSelection().isCollapsed) {
    const link = document.createElement('a'); link.href = value; link.textContent = value;
    execute('insertHTML', link.outerHTML);
  } else execute('createLink', value);
  linkPanel.hidden = true;
});
root.querySelector('.cancel-link').addEventListener('click', () => { linkPanel.hidden = true; restoreSelection(); });
root.addEventListener('keydown', event => { if (event.key === 'Escape') { linkPanel.hidden = colorPanel.hidden = true; } });
toggle.addEventListener('click', () => {
  if (!sourceMode) source.value = cleanHTML(rich.innerHTML);
  else rich.innerHTML = cleanHTML(source.value);
  sourceMode = !sourceMode;
  source.hidden = !sourceMode; rich.hidden = sourceMode; toolbar.hidden = sourceMode;
  toggle.textContent = sourceMode ? 'Rich text' : 'HTML source'; hideSuggestions();
  colorPanel.hidden = linkPanel.hidden = true; publish();
});
document.addEventListener('scroll', positionSuggestions, true);
window.addEventListener('resize', positionSuggestions);
document.addEventListener('pointerdown', event => { if (!root.contains(event.target)) hideSuggestions(); });
sync();
watch('value', sync);
