(() => {
  if (document.getElementById('otp-installation-setup')) return;
  const overlay=document.createElement('div');overlay.id='otp-installation-setup';
  overlay.style.cssText='position:fixed;inset:0;z-index:2147483646;display:grid;place-items:center;background:rgba(10,18,32,.65);backdrop-filter:blur(5px);font:15px/1.5 Segoe UI,sans-serif;color:#152238';
  overlay.innerHTML='<section role="dialog" aria-modal="true" aria-labelledby="otp-setup-title" style="max-width:440px;margin:24px;padding:32px;border-radius:16px;background:white;box-shadow:0 20px 70px #0004"><h2 id="otp-setup-title" style="margin:0 0 12px;font-size:24px">Set up this installation</h2><p>Choose the private TXT file supplied by your app administrator to configure email unlock codes.</p><p>The file is read on this computer. It is not uploaded.</p><button type="button" style="width:100%;padding:13px;border:0;border-radius:8px;background:#2048df;color:white;font:600 15px Segoe UI,sans-serif">Choose setup TXT</button><p class="setup-result" role="status" style="color:#526176;margin-bottom:0"></p></section>';
  document.body.append(overlay);
  const button=overlay.querySelector('button'),message=overlay.querySelector('.setup-result');
  button.onclick=async()=>{button.disabled=true;try{const result=await window.pywebview.api.import_otp_setup();message.textContent=result.message;if(result.ok)window.location.reload();}catch{message.textContent='Could not import the setup file. Please try again.';}finally{button.disabled=false;}};
  button.focus();overlay.addEventListener('keydown',event=>{if(event.key==='Tab'){event.preventDefault();button.focus();}});
})();
