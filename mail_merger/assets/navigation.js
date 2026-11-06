const shell = document.getElementById('mail-merger-shell');
const menu = element.querySelector('.workspace-menu');
const allowed = new Set(['dashboard', 'history', 'campaigns', 'recipients', 'compose', 'attachments', 'review', 'accounts']);
function navigate() {
  const requested = window.location.hash.replace(/^#\/?/, '');
  const selected = allowed.has(requested) ? requested : (allowed.has(props.value) ? props.value : 'recipients');
  shell.dataset.page = selected;
  if (selected === 'dashboard') document.dispatchEvent(new Event('mail-merger-dashboard-open'));
  if (selected === 'history') document.dispatchEvent(new Event('mail-merger-history-open'));
  for (const link of menu.querySelectorAll('a[data-page]')) {
    if (link.dataset.page === selected) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  }
  const workspace = document.getElementById('page-workspace');
  workspace.scrollTo({top: 0});
  const heading = document.querySelector('#page-' + selected + ' .page-heading h1');
  if (heading) { heading.tabIndex = -1; heading.focus({preventScroll: true}); }
}
window.addEventListener('hashchange', navigate);
navigate();
