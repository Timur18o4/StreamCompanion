'use strict';
const $ = selector => document.querySelector(selector);
const esc = (value='') => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let access = new URLSearchParams(location.hash.slice(1)).get('access') || sessionStorage.getItem('sender-access') || '';
if (access) sessionStorage.setItem('sender-access',access);
if (location.hash) history.replaceState(null,'',location.pathname);
const roles = [['streamer','Mikrofon','Deine Stimme · Streamer'],['conversation','Gespräch / Discord','Andere Stimmen · zunächst ohne Personenidentität'],['game','Gameplay','Spieldialoge und Spielkontext']];
let state, devices = [], ready = false;
async function api(path,body) {
  const result = await fetch('/api/' + path,{headers:{'Authorization':'Bearer '+access,'Content-Type':'application/json'},...(body === undefined ? {} : {method:'POST',body:JSON.stringify(body)})});
  const data = await result.json().catch(() => ({}));
  if (result.status === 401) {$('#sender-unlock').hidden=false;$('#sender-main').hidden=true;throw new Error('Lokalen Zugangscode prüfen.');}
  if (!result.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Die Aktion ist fehlgeschlagen.');
  return data;
}
function renderDevices() {
  $('#sender-sources').innerHTML = roles.map(([role,name,hint]) => {
    const saved = state?.config.sources.find(source => source.role === role);
    return `<div class="sender-source"><div><b>${name}</b><p>${hint}</p></div><select data-role="${role}" aria-label="${name}"><option value="">Nicht verwenden</option>${devices.map(device => `<option value="${device.index}" ${saved?.device === device.index ? 'selected' : ''}>${esc(device.name)} · ${esc(device.host)}</option>`).join('')}</select></div>`;
  }).join('');
}
async function refresh() {
  try {
    state = await api('status');
    $('#sender-unlock').hidden=true; $('#sender-main').hidden=false;
    $('#sender-status').textContent = state.remote_active ? 'Überträgt Audio' : state.running ? 'Sender bereit' : state.paired ? 'Gekoppelt' : 'Noch nicht verbunden';
    $('#sender-status').className = 'badge ' + (state.remote_active ? 'mint' : 'neutral');
    $('#sender-paired').textContent = state.paired ? 'Verbindung gespeichert' : 'Noch nicht gekoppelt';
    $('#sender-message').textContent = state.status;
    $('#sender-error').textContent = state.error;
    $('#sender-bytes').textContent = (state.sent_bytes / 1024 / 1024).toFixed(1) + ' MB übertragen';
    $('#sender-start').disabled = state.running;
    $('#sender-stop').disabled = !state.running;
    document.querySelectorAll('#sender-sources select, #sender-monitor').forEach(el => el.disabled=state.running);
    if (!ready) {
      ready = true; $('#sender-server').value = state.config.server;
      await loadDevices();
      const result = await api('monitors');
      $('#sender-monitor').innerHTML = '<option value="0">Keinen Bildschirm übertragen</option>'+result.monitors.map(m => `<option value="${m.index}">Bildschirm ${m.index} · ${m.width} × ${m.height}</option>`).join('');
      $('#sender-monitor').value = String(state.config.monitor || 0);
    }
  } catch(error) {$('#sender-error').textContent=error.message;}
}
async function loadDevices() {devices=(await api('devices')).devices;renderDevices();}
document.addEventListener('click',async event => {
  const button=event.target.closest('[data-sender-action]');if(!button||button.disabled)return;
  button.disabled=true;$('#sender-error').textContent='';
  try {
    if(button.dataset.senderAction==='pair'){await api('pair',{server:$('#sender-server').value,code:$('#sender-code').value});$('#sender-code').value='';}
    if(button.dataset.senderAction==='devices')await loadDevices();
    if(button.dataset.senderAction==='start'){
      const sources=[...document.querySelectorAll('#sender-sources select')].filter(el=>el.value!=='').map(el=>({id:'pc_'+el.dataset.role,name:roles.find(role=>role[0]===el.dataset.role)[1],role:el.dataset.role,device:Number(el.value),device_name:devices.find(device=>device.index===Number(el.value))?.name || '',enabled:true,remember:el.dataset.role==='streamer'}));
      await api('start',{sources,monitor:Number($('#sender-monitor').value)});
    }
    if(button.dataset.senderAction==='stop')await api('stop',{});
    await refresh();
  }catch(error){$('#sender-error').textContent=error.message;}
  finally{button.disabled=false;if(state){$('#sender-start').disabled=state.running;$('#sender-stop').disabled=!state.running;}}
});
$('#sender-unlock-form').addEventListener('submit',async event=>{event.preventDefault();access=$('#sender-access').value.trim();sessionStorage.setItem('sender-access',access);await refresh();});
if(access)refresh();else $('#sender-unlock').hidden=false;
setInterval(()=>{if(access)refresh();},2000);
