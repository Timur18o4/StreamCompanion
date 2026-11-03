'use strict';
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const esc = (value = '') => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icon = (name) => `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
const clock = (seconds, date = false) => new Date(seconds * 1000).toLocaleString('de-DE', date ? {day:'2-digit',month:'2-digit',year:'numeric'} : {hour:'2-digit',minute:'2-digit'});
let access = new URLSearchParams(location.hash.slice(1)).get('access') || sessionStorage.getItem('companion-access') || '';
if (location.hash.includes('access=')) history.replaceState(null, '', location.pathname);
if (access) sessionStorage.setItem('companion-access', access);
let state, currentView = 'home', deviceList = [], memoryList = [], feedSignature = '', draftSignature = '', oauthRole = null, refreshing = false, failures = 0;
let toastTimeout;

function toast(message, error = false) {
  $('#toast').textContent = message;
  $('#toast').classList.toggle('error', error);
  $('#toast').hidden = false;
  clearTimeout(toastTimeout);
  toastTimeout = setTimeout(() => $('#toast').hidden = true, error ? 8000 : 4500);
}

async function api(path, options = {}) {
  const headers = {'Authorization': `Bearer ${access}`, ...(options.body instanceof FormData ? {} : {'Content-Type':'application/json'}), ...options.headers};
  const response = await fetch('/api/' + path, {...options, headers});
  const payload = await response.json().catch(() => ({}));
  if (response.status === 401) {
    $('#app').hidden = true;
    $('#unlock').hidden = false;
    throw new Error('Bitte den lokalen Zugangscode prüfen.');
  }
  if (!response.ok) {
    let message = typeof payload.detail === 'string' ? payload.detail : 'Die Aktion konnte nicht abgeschlossen werden.';
    if (payload.fields?.length) message += ' ' + payload.fields.map(x => x.field.replace('body.settings.', '') + ': ' + x.message).join('; ');
    throw new Error(message);
  }
  return payload;
}
const post = (path, body) => api(path, {method:'POST', ...(body === undefined ? {} : {body:JSON.stringify(body)})});

function setView(name) {
  const labels = {home:'Übersicht',setup:'Einrichtung',audio:'Audio & Bild',memory:'Erinnerungen',logs:'Aktivität'};
  if (!labels[name]) return;
  currentView = name;
  $$('.view').forEach(el => el.classList.toggle('active', el.id === 'view-' + name));
  $$('.nav-item').forEach(el => el.classList.toggle('active', el.dataset.view === name));
  $('#page-label').textContent = labels[name];
  if (name === 'audio') loadDevices().catch(error => toast(error.message, true));
  if (name === 'memory') loadMemories().catch(error => toast(error.message, true));
  window.scrollTo({top:0,behavior:'instant'});
}

function showModal(content) {
  $('#modal-content').innerHTML = content;
  if (!$('#modal').open) $('#modal').showModal();
}

function collectSettings() {
  const settings = structuredClone(state.settings);
  $$('[data-setting]').forEach(el => {
    settings[el.dataset.setting] = el.type === 'checkbox' ? el.checked : el.type === 'number' || el.dataset.valueType === 'number' ? Number(el.value) : el.value;
  });
  settings.input_mode = $('input[name="input-mode"]:checked')?.value || settings.input_mode;
  settings.sources = $$('[data-source]').map(row => ({
    id:row.dataset.source, name:row.querySelector('[data-field=name]').value,
    role:row.querySelector('[data-field=role]').value,
    enabled:row.querySelector('[data-field=enabled]').checked,
    remember:row.querySelector('[data-field=remember]').checked,
    device:row.querySelector('[data-field=device]').value === '' ? null : Number(row.querySelector('[data-field=device]').value),
    device_name:row.querySelector('[data-field=device]').selectedOptions[0]?.dataset.name || row.dataset.deviceName || ''
  }));
  return settings;
}

async function save(silent = false) {
  const invalid = $$('[data-setting], [data-field=name]').find(el => !el.checkValidity());
  if (invalid) { invalid.reportValidity(); throw new Error('Bitte die markierte Eingabe prüfen.'); }
  const body = {settings:collectSettings()};
  $$('[data-secret]').forEach(el => { if (el.value) body[el.dataset.secret] = el.value; });
  await post('settings', body);
  $$('[data-secret]').forEach(el => el.value = '');
  await refresh(true);
  if (!silent) toast('Einstellungen gespeichert.');
}

function hydrate() {
  const cpuMaximum = Math.max(1, Number(state.capabilities.cpu_threads_max) || 1);
  const choices = Array.from({length:cpuMaximum}, (_, index) => `<option value="${index+1}">${index+1} ${index ? 'Threads' : 'Thread'}</option>`).join('');
  $('#whisper-cpu-threads').innerHTML = `<option value="0">Automatisch (${Math.min(8,cpuMaximum)} Threads)</option>` + choices;
  $('#streaming-cpu-threads').innerHTML = `<option value="0">Automatisch (${Math.min(8,cpuMaximum)} Threads)</option>` + choices;
  $('#local-ai-cpu-threads').innerHTML = '<option value="0">Modell-Vorgabe</option>' + choices;
  $('#cpu-thread-info').textContent = `Dieser Rechner stellt ${cpuMaximum} logische Prozessoren bereit. Spracherkennung und Gesprächsmodell teilen sich die Rechenleistung. Änderungen beenden einen laufenden Durchgang.`;
  $$('[data-setting]').forEach(el => {
    const value = state.settings[el.dataset.setting];
    if (el.type === 'checkbox') el.checked = Boolean(value); else el.value = value ?? '';
  });
  $$('[data-secret]').forEach(el => el.placeholder = state.secret_status[el.dataset.secret] ? 'Hinterlegt · nur zum Ersetzen neu eingeben' : 'Noch kein Schlüssel hinterlegt');
  const inputMode = $(`input[name="input-mode"][value="${state.settings.input_mode}"]`);
  if (inputMode) inputMode.checked = true;
  renderSources(state.settings.sources);
  updateProviderFields();
}

function updateProviderFields() {
  $('#base-url-field').hidden = $('#provider').value === 'openai';
  $('#stt-key-field').hidden = $('#stt-provider').value !== 'openai_realtime';
  $('#whisper-field').hidden = $('#stt-provider').value !== 'local_whisper';
  $('#whisper-cpu-field').hidden = $('#stt-provider').value !== 'local_whisper';
  $('#streaming-field').hidden = $('#stt-provider').value !== 'local_streaming';
  $('#streaming-cpu-field').hidden = $('#stt-provider').value !== 'local_streaming';
  $('#local-cpu-field').hidden = $('#provider').value !== 'local';
  $('#cpu-settings').hidden = !['local_whisper','local_streaming'].includes($('#stt-provider').value) && $('#provider').value !== 'local';
}

function renderAccounts() {
  for (const role of ['owner','bot']) {
    const profile = state.profiles[role], pending = state.oauth[role];
    const title = role === 'owner' ? 'Kanalbesitzer' : 'Bot-Account';
    $('#account-' + role).innerHTML = `<div class="account-meta"><div class="account-avatar">${profile?.profile_image_url ? `<img alt="" src="${esc(profile.profile_image_url)}">` : icon(role === 'owner' ? 'grid' : 'chat')}</div><div><b>${esc(profile?.display_name || title)}</b><small>${profile ? esc(title + ' · verbunden') : pending?.status === 'pending' ? 'Wartet auf Freigabe bei Twitch' : 'Noch nicht verbunden'}</small></div></div><button class="${profile ? 'text-button' : 'button small'}" data-action="${profile ? 'disconnect' : 'connect'}" data-role="${role}">${profile ? 'Trennen' : 'Mit Twitch verbinden'}</button>`;
    if (oauthRole === role && pending?.status === 'connected') {
      oauthRole = null; $('#modal').close();
      if (role === 'owner' && !$('#channel').value) $('#channel').value = profile?.login || '';
      toast(`${profile?.display_name || title} ist verbunden.`);
    } else if (oauthRole === role && pending?.status === 'error') {
      oauthRole = null; $('#modal').close(); toast(pending.message, true);
    }
  }
  const complete = [Boolean(state.settings.twitch_client_id), Boolean(state.profiles.owner && state.profiles.bot), Boolean(state.settings.text_model), Boolean(state.settings.channel)];
  $('#setup-progress').innerHTML = ['Twitch-App','Zwei Konten','KI-Verbindung','Dein Charakter'].map((name,index) => `<div class="progress-step ${complete[index] ? 'done' : ''}"><span>${complete[index] ? '✓' : index + 1}</span>${name}</div>`).join('');
  $('#onboarding-hint').hidden = complete.every(Boolean);
  $('#owner-initial').textContent = (state.profiles.owner?.display_name || 'SC').slice(0,2).toUpperCase();
}

function renderRuntime() {
  const runtime = state.runtime, settings = state.settings;
  const channel = runtime.channel;
  $('#channel-name').textContent = runtime.demo ? 'Beispielstream' : channel.name || settings.channel || 'Dein Twitch-Kanal';
  $('#channel-title').textContent = runtime.demo ? 'Ein neuer Versuch mit dem Bogen · Beispielsituation' : channel.title || 'Die Verbindung beginnt in der Einrichtung.';
  $('#channel-category').textContent = runtime.demo ? 'Gameplay · Demo' : runtime.offline_test ? 'Lokales Audio · Offline-Test' : channel.category || 'Kategorie wird automatisch erkannt';
  $('#channel-avatar').innerHTML = !runtime.demo && channel.profile_image_url ? `<img src="${esc(channel.profile_image_url)}" alt="">` : '↗';
  const badge = $('#live-badge');
  badge.textContent = runtime.demo ? 'Demo · keine Aufnahme' : channel.live ? 'LIVE' : channel.id ? runtime.offline_test ? 'Offline · Test aktiv' : 'Offline' : 'Noch nicht verbunden';
  badge.className = 'badge ' + (runtime.demo ? 'demo' : channel.live ? 'live' : 'neutral');
  $$('.mode-switch button').forEach(el => el.classList.toggle('selected', el.dataset.mode === settings.mode));
  $('#mode-description').textContent = settings.mode === 'automatic' ? runtime.offline_test ? 'Offline-Test: Das Mikrofon wird verarbeitet. Automatische Beiträge erscheinen im echten Twitch-Chat.' : 'Passende Beiträge können automatisch im verbundenen Twitch-Kanal erscheinen.' : 'In der Vorschau entscheidest du, welche Beiträge veröffentlicht werden.';
  $('#run-button').classList.toggle('stop', runtime.running);
  $('#run-button').innerHTML = icon(runtime.running ? 'stop' : 'play') + (runtime.running ? 'Begleiter stoppen' : 'Begleiter starten');
  const lastModelResult = (runtime.logs || []).find(log => ['Lokales Modell nicht bereit', 'Lokales Modell bereit'].includes(log.title));
  const modelFailure = runtime.paused && lastModelResult?.title === 'Lokales Modell nicht bereit' ? lastModelResult.message : '';
  $('#pause-button').hidden = !runtime.running;
  $('#pause-button').textContent = runtime.paused ? modelFailure ? 'Modell erneut prüfen' : 'Weiter zuhören' : 'Pause';
  $('#listening-dot').classList.toggle('listening', runtime.running && !runtime.paused);
  $('#character-name').textContent = settings.bot_name;
  $('#character-description').textContent = settings.personality.length > 180 ? settings.personality.slice(0,177) + '…' : settings.personality;
  $('#character-state').textContent = runtime.demo ? 'Eine Beispielsituation zum Kennenlernen.' : runtime.paused ? modelFailure ? 'KI-Modell konnte nicht geladen werden.' : 'Macht gerade Pause.' : runtime.running && runtime.analyzing ? `${runtime.analysis_label || (runtime.analysis_kind === 'memory' ? 'Prüft Erinnerungen' : 'Formuliert gerade')} · ${Math.round(runtime.phase_seconds ?? runtime.analysis_seconds)} Sekunden` : runtime.running ? 'Hört zu und behält den Kontext.' : 'Bereit, wenn du es bist.';
  $('#runtime-reason').textContent = runtime.demo ? 'Diese Demo sendet keine Nachrichten an Twitch.' : modelFailure ? `${modelFailure} Modell: ${settings.text_model}. Modellliste und lokalen Modellordner prüfen.` : runtime.reason;
  $('#stat-sent').textContent = runtime.sent_hour;
  $('#stat-limit').textContent = 'von höchstens ' + settings.max_per_hour;
  $('#stat-memory').textContent = runtime.memories_count;
  $('#memory-nav-count').textContent = runtime.memories_count;
  const sources = Object.values(runtime.sources), activeSources = sources.filter(item => Date.now()/1000 - item.last_packet < 3).length;
  $('#stat-sources').textContent = activeSources;
  $('#stat-source-label').textContent = activeSources ? 'Werden laufend verarbeitet' : 'Noch keine Quelle aktiv';
  $('#analyze-button').disabled = !runtime.running || runtime.paused || runtime.demo;
  const signature = runtime.events.map(event => `${event.id}:${event.version || 0}`).join(',');
  if (signature !== feedSignature || !runtime.events.length) {
    feedSignature = signature;
    if (runtime.events.length) {
      $('#context-feed').innerHTML = runtime.events.map(event => `<div class="context-item ${event.kind === 'chat' ? 'chat-event' : event.kind === 'bot' ? 'bot-event' : ''}"><span class="event-icon">${icon(event.kind === 'chat' ? 'chat' : event.kind === 'bot' ? 'check' : 'audio')}</span><div><span class="event-label">${esc(event.name)}<span class="event-role">${event.demo ? 'DEMO' : event.kind === 'bot' ? 'Auf Twitch bestätigt' : event.provisional ? 'Mitschrift wird ergänzt' : event.role === 'streamer' ? 'Streamer' : event.role === 'game' ? 'Spielkontext' : event.role === 'conversation' ? 'Gespräch / unbekannte Stimme' : 'Chat'}</span></span><p>${esc(event.text)}</p></div><time class="event-time">${clock(event.time)}</time></div>`).join('');
      $('#context-feed').scrollTop = $('#context-feed').scrollHeight;
    } else {
      $('#context-feed').innerHTML = `<div class="empty-state"><span class="empty-icon">${icon('audio')}</span><h3>Hier beginnt das Zuhören.</h3><p>Nach dem Start erscheinen erkannte Aussagen, Chatnachrichten und veröffentlichte Beiträge.</p><button class="text-button" data-action="demo">Eine Beispielsituation ansehen ${icon('arrow')}</button></div>`;
    }
  }
  $('#context-count').textContent = runtime.events.length ? runtime.events.length + ' Beobachtungen' : 'Noch kein Kontext';
  const draft = runtime.pending;
  const newDraftSignature = draft ? draft.id + (draft.revision !== undefined && runtime.events.at(-1)?.id || '') : '';
  if (newDraftSignature !== draftSignature) {
    draftSignature = newDraftSignature;
    $('#draft-content').innerHTML = draft ? `<p class="draft-message">${esc(draft.text)}</p><p class="draft-reason">${esc(draft.reason)}</p>${draft.demo ? '<span class="demo-label">Beispielbeitrag · kein KI-Aufruf · kein Versand</span>' : ''}` : '<p class="muted">Ein Vorschlag erscheint, sobald ein passender Gesprächsanlass erkannt wurde.</p>';
  }
  $('#draft-actions').hidden = !draft || draft.demo;
  $$('[data-source]').forEach(row => {
    const info = runtime.sources[row.dataset.source];
    row.querySelector('.source-level i').style.width = Math.min(100,(info?.level || 0)*400) + '%';
    row.querySelector('.source-status').textContent = info ? info.status + (Number.isFinite(info.lag_seconds) && info.lag_seconds >= 1 ? ` · ${info.lag_seconds.toFixed(1)} s Audiopuffer` : '') + (info.dropped ? ` · ${info.dropped} verworfene Puffer` : '') : 'Noch kein Audiosignal';
    row.querySelector('.source-preview').textContent = info?.partial_text ? `Mitschrift: ${info.partial_text.slice(-220)}` : '';
  });
  $('#logs-list').innerHTML = runtime.logs.length ? runtime.logs.map(log => `<div class="log-item ${esc(log.level)}"><time>${clock(log.time)}</time><i></i><div><b>${esc(log.title)}</b><p>${esc(log.message)}</p></div></div>`).join('') : '<div class="empty-state"><h3>Alles an einem Ort.</h3><p>Verbindungen und Entscheidungen erscheinen nach den ersten Einrichtungsschritten.</p></div>';
}

async function refresh(withHydrate = false) {
  if (refreshing && !withHydrate) return;
  refreshing = true;
  try {
    state = await api('status'); failures = 0;
    $('#unlock').hidden = true; $('#app').hidden = false;
    $('#server-status').innerHTML = '<i></i>Lokal verbunden';
    if (withHydrate) hydrate();
    renderAccounts(); renderRuntime();
  } catch (error) {
    failures++;
    $('#server-status').textContent = 'Verbindung unterbrochen';
    if (withHydrate) throw error;
  } finally { refreshing = false; }
}

function renderSources(sources) {
  const roleOptions = (role) => [['streamer','Streamer / Mikrofon'],['conversation','Gespräch / Discord'],['game','Gameplay'],['ignore','Nicht auswerten']].map(([value,label]) => `<option value="${value}" ${role === value ? 'selected' : ''}>${label}</option>`).join('');
  $('#audio-sources').innerHTML = sources.map(source => `<div class="source-row" data-source="${esc(source.id)}" data-device-name="${esc(source.device_name || '')}"><div><label>Name der Quelle</label><input data-field="name" aria-label="Name der Quelle" value="${esc(source.name)}" maxlength="80" required><label class="check-row"><input type="checkbox" data-field="enabled" ${source.enabled ? 'checked' : ''}>Quelle verwenden</label></div><div><label>Rolle</label><select data-field="role" aria-label="Rolle der Audioquelle">${roleOptions(source.role)}</select><label class="check-row"><input type="checkbox" data-field="remember" ${source.remember ? 'checked' : ''}>Für Streamer-Erinnerungen</label></div><div><label>Aufnahmegerät auf diesem Rechner</label><select data-field="device" aria-label="Aufnahmegerät" data-selected="${source.device ?? ''}"></select><div class="source-level"><i></i></div><span class="source-status">Noch kein Audiosignal</span><p class="source-preview field-hint"></p></div><button class="source-delete" data-action="remove-source" aria-label="Quelle entfernen">${icon('trash')}</button></div>`).join('');
  updateDeviceOptions();
}

function updateDeviceOptions() {
  $$('[data-field=device]').forEach(select => {
    const selected = select.options.length ? select.value : select.dataset.selected || '';
    const found = deviceList.some(device => String(device.index) === String(selected));
    select.innerHTML = '<option value="">Quelle wählen …</option>' + deviceList.map(device => `<option value="${device.index}" data-name="${esc(device.name)}">${esc(device.name)} · ${esc(device.host)}</option>`).join('') + (!found && selected !== '' ? `<option value="${esc(selected)}">Gerät ${esc(selected)} · derzeit nicht verfügbar</option>` : '');
    select.value = selected;
    select.dataset.selected = selected;
  });
}

async function loadDevices() {
  const result = await api('audio/devices'); deviceList = result.devices;
  $('#audio-device-note').textContent = deviceList.length ? `${deviceList.length} verfügbare Eingänge auf dem Rechner, auf dem Stream Companion läuft.` : 'Auf diesem Rechner wurden keine Eingänge gefunden. Audio-Zusatzpaket prüfen oder den PC-Sender verwenden.';
  updateDeviceOptions();
}

async function loadMemories() {
  memoryList = (await api('memories')).memories;
  renderMemories();
}

function renderMemories() {
  const term = $('#memory-search').value.toLowerCase();
  const filtered = memoryList.filter(item => (item.fact + ' ' + item.subject_name).toLowerCase().includes(term));
  $('#memory-total').textContent = memoryList.length + ' Notizen';
  $('#memories-list').innerHTML = filtered.length ? filtered.map(item => `<article class="memory-item"><div class="memory-item-top"><div><b>${esc(item.subject_name)}</b><time>${clock(item.created,true)}</time></div><button class="icon-button" data-action="delete-memory" data-id="${item.id}" aria-label="Erinnerung löschen">${icon('trash')}</button></div><p>${esc(item.fact)}</p><div class="memory-source">Herkunft: ${esc(item.source)}</div></article>`).join('') : '<div class="empty-state"><span class="empty-icon">'+icon('memory')+'</span><h3>Platz für gemeinsame Geschichten.</h3><p>Belegte Erinnerungen erscheinen hier. Du kannst auch selbst eine Notiz zum Streamer hinzufügen.</p></div>';
}

async function connect(role) {
  await save(true);
  const result = await post(`twitch/${role}/connect`);
  oauthRole = role;
  const url = new URL(result.verification_uri);
  if (url.protocol !== 'https:' || !['www.twitch.tv','twitch.tv'].includes(url.hostname)) throw new Error('Unerwartete Twitch-Freigabeadresse.');
  showModal(`<p class="eyebrow">TWITCH-ANMELDUNG</p><h2>${role === 'owner' ? 'Deinen Kanal verbinden.' : 'Deinen Bot anmelden.'}</h2><p>Öffne Twitch und erteile die Freigabe für den richtigen Account. ${role === 'bot' ? 'Wenn noch dein persönlicher Account angemeldet ist, wechsle dort zunächst zum Bot-Konto.' : ''}</p><div class="device-code">${esc(result.user_code)}</div><a class="primary full-width" href="${esc(url.href)}" target="_blank" rel="noopener noreferrer">Bei Twitch freigeben ↗</a><p>Diese Oberfläche übernimmt die Verbindung nach deiner Freigabe automatisch. Der Code läuft nach einiger Zeit ab.</p><button class="text-button" data-action="cancel-oauth">Anmeldung abbrechen</button>`);
  await refresh();
}

async function action(name, element) {
  if (!state && name !== 'close-modal') return;
  switch(name) {
    case 'save': await save(); break;
    case 'devices': await loadDevices(); toast('Geräteliste aktualisiert.'); break;
    case 'add-source': {
      const sources = collectSettings().sources;
      if (sources.length >= 8) throw new Error('Es sind maximal acht Quellen vorgesehen.');
      sources.push({id:'source_' + crypto.randomUUID().slice(0,8),name:'Weitere Quelle',role:'conversation',enabled:true,remember:false,device:null});
      renderSources(sources); break;
    }
    case 'remove-source': element.closest('[data-source]').remove(); break;
    case 'connect': await connect(element.dataset.role); break;
    case 'disconnect': await post(`twitch/${element.dataset.role}/disconnect`); await refresh(); toast('Account getrennt.'); break;
    case 'cancel-oauth': if (oauthRole) await post(`twitch/${oauthRole}/cancel`); oauthRole = null; $('#modal').close(); await refresh(); break;
    case 'check-channel': await save(true); await post('twitch/check'); await refresh(); toast('Kanal geprüft.'); break;
    case 'models': {
      await save(true); const result = await api('ai/models');
      $('#model-list').innerHTML = result.models.map(model => `<option value="${esc(model)}"></option>`).join('');
      toast(result.models.length + ' Modelle geladen. Im Modellfeld auswählen.'); $('#text-model').focus(); break;
    }
    case 'test-ai': await save(true); await post('ai/test'); toast('Das KI-Modell hat erfolgreich geantwortet.'); break;
    case 'run':
      if (state.runtime.running) await post('run/stop'); else { await save(true); await post('run/start'); }
      await refresh(); break;
    case 'pause': await post('run/pause'); await refresh(); break;
    case 'analyze': await post('run/analyze'); await refresh(); break;
    case 'send': if (state.runtime.pending) {await post('draft/send',{id:state.runtime.pending.id}); await refresh(); toast('Twitch hat die Veröffentlichung bestätigt.');} break;
    case 'discard': if (state.runtime.pending) {await post('draft/discard',{id:state.runtime.pending.id}); await refresh();} break;
    case 'demo':
      if (state.runtime.running) { showModal('<h2>Beispielsituation öffnen?</h2><p>Der aktive Durchgang wird gestoppt. Die Demo verwendet ausschließlich Beispieldaten und sendet nichts an Twitch.</p><button class="primary" data-action="confirm-demo">Durchgang stoppen und Demo öffnen</button>'); break; }
      await post('demo'); await refresh(); setView('home'); break;
    case 'confirm-demo': $('#modal').close(); await post('demo'); await refresh(); setView('home'); break;
    case 'automatic-mode': await post('run/mode',{mode:'automatic'}); $('#modal').close(); await refresh(); break;
    case 'pair': {
      const result = await post('pairing'); $('#pair-code').hidden = false;
      $('#pair-code').textContent = result.code;
      toast('Verbindungscode erstellt. Im PC-Sender eingeben.'); break;
    }
    case 'revoke-pair': await api('pairing',{method:'DELETE'}); $('#pair-code').hidden = true; toast('Die PC-Verbindung ist nicht mehr zugelassen.'); break;
    case 'obs-image': await save(true); showImage(await post('screenshot/obs')); toast('Aktuelles OBS-Bild übernommen.'); break;
    case 'add-memory': {
      const fact = $('#memory-fact').value.trim(); if (fact.length < 3) throw new Error('Bitte eine aussagekräftige Notiz eingeben.');
      await post('memories',{fact}); $('#memory-fact').value = ''; await loadMemories(); await refresh(); toast('Erinnerung hinzugefügt.'); break;
    }
    case 'delete-memory': await api('memories/' + element.dataset.id,{method:'DELETE'}); await loadMemories(); await refresh(); break;
    case 'clear-memory': showModal('<h2>Erinnerungen löschen?</h2><p>Alle Erinnerungen und Zuschauer-Zustimmungen dieses Kanals werden entfernt. Persönliche Speichersperren bleiben erhalten. Andere Kanäle bleiben erhalten.</p><button class="primary" data-action="confirm-clear-memory">Erinnerungen dieses Kanals löschen</button>'); break;
    case 'confirm-clear-memory': await api('memories',{method:'DELETE'}); $('#modal').close(); await loadMemories(); await refresh(); toast('Erinnerungen und Zustimmungen gelöscht. Speichersperren bleiben erhalten.'); break;
    case 'export-memory': {
      await loadMemories(); const blob = new Blob([JSON.stringify(memoryList,null,2)],{type:'application/json'});
      const url = URL.createObjectURL(blob), link = document.createElement('a'); link.href = url; link.download = 'stream-companion-erinnerungen.json'; link.click(); setTimeout(() => URL.revokeObjectURL(url),1000); break;
    }
    case 'help': showModal(`<p class="eyebrow">ERSTE SCHRITTE</p><h2>Einmal verbinden, dann zuhören.</h2><ol><li>In der Einrichtung eine öffentliche Twitch-App-ID hinterlegen. Kanalbesitzer und Bot getrennt über Twitch anmelden.</li><li>Den eigenen Kanal prüfen. Ein Textmodell und eine Spracherkennung einrichten; den KI-Verbindungstest ausführen.</li><li>Unter Audio & Bild die Audioherkunft wählen. Für lokale Aufnahme Geräte auswählen. Für einen Raspberry den PC-Sender verbinden.</li><li>Mit der Vorschau starten. Erst nach geprüften Beiträgen bei Bedarf auf automatisch umstellen.</li></ol><p>Die Demo funktioniert ohne Konten oder KI-Schlüssel. Für den Livebetrieb benötigst du eigene Zugänge. Eine ausführliche Anleitung liegt als README.md im Projektordner.</p><p>Stop beendet Erfassung und automatische Beiträge. Pause unterbricht die Verarbeitung bis zum Fortsetzen. OBS-Bilder ändern keine Szenen.</p>`); break;
    case 'close-modal': $('#modal').close(); break;
  }
}

function showImage(result) { $('#image-preview').src = result.image; $('#image-preview').hidden = false; $('#image-status').textContent = 'Bild um ' + new Date().toLocaleTimeString('de-DE',{hour:'2-digit',minute:'2-digit'}) + ' übernommen'; }

document.addEventListener('click', async event => {
  const viewButton = event.target.closest('[data-view]');
  if (viewButton) { event.preventDefault(); setView(viewButton.dataset.view); return; }
  const mode = event.target.closest('[data-mode]');
  if (mode && state) {
    if (mode.dataset.mode === 'automatic' && state.settings.mode !== 'automatic') showModal('<h2>Automatisch mitreden?</h2><p>Der Begleiter darf passende Beiträge mit dem verbundenen Bot-Account selbst auf Twitch veröffentlichen. Abstände, Stundenlimit und Live-Status werden weiterhin geprüft.</p><div class="button-group"><button class="primary" data-action="automatic-mode">Automatische Beiträge einschalten</button><button class="button" data-action="close-modal">In der Vorschau bleiben</button></div>');
    else if (mode.dataset.mode !== state.settings.mode) {try {await post('run/mode',{mode:mode.dataset.mode}); await refresh();} catch(error) {toast(error.message,true);}}
    return;
  }
  const element = event.target.closest('[data-action]');
  if (!element || element.disabled) return;
  event.preventDefault(); element.disabled = true;
  try { await action(element.dataset.action,element); }
  catch(error) {toast(error.message || 'Die Aktion konnte nicht abgeschlossen werden.',true);}
  finally {element.disabled = false;}
});

$('#provider').addEventListener('change', () => { updateProviderFields(); if ($('#provider').value === 'openai' && !$('#text-model').value) $('#text-model').value = 'gpt-4.1-mini'; });
$('#stt-provider').addEventListener('change',updateProviderFields);
$('#memory-search').addEventListener('input',renderMemories);
$('#unlock-form').addEventListener('submit', async event => {event.preventDefault(); access = $('#access').value.trim(); sessionStorage.setItem('companion-access',access); try {await refresh(true); $('#access').value = ''; $('#unlock-error').textContent = '';} catch(error) {$('#unlock-error').textContent = error.message;}});
$('#image-upload').addEventListener('change', async event => {const file = event.target.files[0]; if (!file) return; const form = new FormData(); form.append('file',file); try {showImage(await api('screenshot/upload',{method:'POST',body:form})); toast('Testbild übernommen.');} catch(error) {toast(error.message,true);} finally {event.target.value = '';}});
if (access) refresh(true).catch(error => {$('#unlock').hidden = false; $('#unlock-error').textContent = error.message;}); else $('#unlock').hidden = false;
setInterval(() => {if (access && !$('#app').hidden) refresh();},2000);
