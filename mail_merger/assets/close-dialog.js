(() => {
  const existing = document.getElementById('mail-merger-close');
  if (existing) { existing.querySelector('button').focus(); return; }
  const overlay = document.createElement('div');
  overlay.id = 'mail-merger-close';
  overlay.innerHTML = `<style>
    #mail-merger-close{position:fixed;inset:0;z-index:2147483647;display:grid;place-items:center;background:rgba(10,18,32,.64);backdrop-filter:blur(5px);padding:24px;font:15px/1.5 'Segoe UI',sans-serif;color:#152238;box-sizing:border-box}
    #mail-merger-close .close-card{box-sizing:border-box;width:100%;max-width:460px;padding:32px;border:1px solid #e1e7ef;border-radius:18px;background:#fff;box-shadow:0 24px 80px #0005}
    #mail-merger-close h2{font-size:23px;line-height:1.3;margin:0 0 12px;color:#152238;font-weight:650}
    #mail-merger-close p{margin:0 0 24px;color:#526176}
    #mail-merger-close button{display:block;width:100%;border:1px solid #cbd5e1;border-radius:9px;background:#fff;color:#152238;padding:12px 16px;margin:10px 0 0;font:600 15px 'Segoe UI',sans-serif;cursor:pointer}
    #mail-merger-close button[data-choice=stay_signed_in]{background:#2048df;border-color:#2048df;color:#fff}
    #mail-merger-close button[data-choice=cancel]{border-color:transparent;color:#526176}
    #mail-merger-close button:focus-visible{outline:3px solid #90b6ff;outline-offset:3px}
    #mail-merger-close button:disabled{opacity:.65;cursor:wait}
    #mail-merger-close .close-error{color:#b42318;margin:14px 0 0}
  </style><section class="close-card" role="dialog" aria-modal="true" aria-labelledby="close-title" aria-describedby="close-description" tabindex="-1">
    <h2 id="close-title">Close Mail-Merger?</h2>
    <p id="close-description">Sending will pause and your progress will be saved.</p>
    <button data-choice="stay_signed_in">Stay signed in and close application</button>
    <button data-choice="sign_out">Sign out and close application</button>
    <button data-choice="cancel">Keep working</button>
    <p class="close-error" role="alert" hidden></p>
  </section>`;
  const previous = document.activeElement;
  document.body.appendChild(overlay);
  // The native dialog must sit above open editor popovers too.
  document.querySelectorAll('[popover]').forEach(node => { try { node.hidePopover(); } catch {} });
  const buttons = [...overlay.querySelectorAll('button')];
  let busy = false;
  async function choose(choice) {
    if (busy) return;
    busy = true;
    buttons.forEach(button => button.disabled = true);
    try {
      const result = await window.pywebview.api.close_application(choice);
      if (!result.ok) throw new Error(result.error);
      if (choice === 'cancel') { overlay.remove(); previous?.focus(); }
    } catch (error) {
      const message = overlay.querySelector('[role=alert]');
      message.textContent = error.message || 'Could not close. Please try again.';
      message.hidden = false;
      busy = false;
      buttons.forEach(button => button.disabled = false);
    }
  }
  buttons.forEach(button => button.addEventListener('click', () => choose(button.dataset.choice)));
  overlay.addEventListener('keydown', event => {
    if (event.key === 'Escape') { event.preventDefault(); choose('cancel'); }
    if (event.key === 'Tab') {
      event.preventDefault();
      const index = buttons.indexOf(document.activeElement);
      buttons[(index + (event.shiftKey ? -1 : 1) + buttons.length) % buttons.length].focus();
    }
  });
  buttons[0].focus();
})();
