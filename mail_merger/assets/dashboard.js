function render() {
  let campaigns;
  try { campaigns = JSON.parse(props.value); } catch { return; }
  if (!Array.isArray(campaigns)) return;
  const root = element.querySelector('.campaign-dashboard');
  const summary = root.querySelector('.dashboard-summary');
  summary.replaceChildren();
  for (const [label, count] of [['Campaigns', campaigns.length], ['Accepted', campaigns.reduce((n,c)=>n+c.accepted,0)], ['Need attention', campaigns.reduce((n,c)=>n+c.failed+c.unknown,0)]]) {
    const box = document.createElement('div'), number = document.createElement('strong');
    number.textContent = count; box.append(number, document.createTextNode(label)); summary.append(box);
  }
  const list = root.querySelector('.campaign-list'); list.replaceChildren();
  for (const campaign of campaigns) {
    const card = document.createElement('article'); card.className = 'campaign-entry';
    const details = document.createElement('div'); details.className = 'campaign-details';
    const title = document.createElement('h2'); title.textContent = campaign.name;
    const info = document.createElement('p'); info.textContent = `${campaign.state} · ${campaign.recipients} recipients · ${campaign.accepted} accepted · ${campaign.failed} failed · ${campaign.unknown} unknown`;
    details.append(title,info); card.append(details);
    for (const [action,label] of [['view','View history'],['reuse','Reuse']]) {
      const button = document.createElement('button'); button.type='button';button.textContent=label;
      button.addEventListener('click',()=>{ props.value=JSON.stringify({id:campaign.id,action}); trigger('input'); });
      card.append(button);
    }
    list.append(card);
  }
}
document.addEventListener('mail-merger-dashboard-open',()=>{props.value=JSON.stringify({action:'refresh'});trigger('input');});
render(); watch('value',render);
