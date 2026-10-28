// ESP BLE Bridge web UI. Talks to the add-on's REST API (rootfs/app/web_ui.py); relative URLs keep it
// working behind Home Assistant Ingress.
'use strict';

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const BOARDS = [
  ['esp32dev', 'ESP32 (WROOM/WROVER dev boards)'],
  ['esp32-s3-devkitc-1', 'ESP32-S3'],
  ['esp32-c3-devkitm-1', 'ESP32-C3'],
  ['esp32-c6-devkitc-1', 'ESP32-C6'],
];
const ESP_WEB_TOOLS = 'https://unpkg.com/esp-web-tools@10.2.1/dist/web/install-button.js?module';
const EFFECT_INFO = {
  rainbow: 'Every light a different hue, rotating',
  color_loop: 'All lights step through the rainbow',
  twinkle: 'Random lights brighten and dim',
  fire: 'Flickering warm colors',
  christmas: 'Red, green and white, shifting',
  halloween: 'Orange and purple',
  strobe: 'Flashing on and off',
  breathe: 'Slowly brightens and dims',
};

const state = {
  config: {},
  lights: [],
  controllers: [],
  presets: [],
  effects: [],
  running: [],
  scenes: [],
  settings: {},
  effectSelection: new Set(),
  jobsWatched: new Set(),
  outputFor: null,
  pairingTimer: null,
  map: null,
  markers: new Map(),
};

// --- helpers -----------------------------------------------------------------

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch]);
}

function slug(name) {
  return String(name).trim().toLowerCase().replace(/[ _]/g, '-');
}

function css(rgb) {
  const [r, g, b] = rgb || [255, 255, 255];
  return `rgb(${r}, ${g}, ${b})`;
}

function toHex(rgb) {
  return '#' + (rgb || [255, 255, 255]).map(v => Math.round(v).toString(16).padStart(2, '0')).join('');
}

function fromHex(hex) {
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex || '');
  return m ? [1, 2, 3].map(i => parseInt(m[i], 16)) : [255, 255, 255];
}

async function api(path, { method = 'GET', body, form } = {}) {
  const options = { method, headers: {} };
  if (form) {
    options.body = form;
  } else if (body !== undefined) {
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  let data = null;
  try {
    data = await response.json();
  } catch {
    data = null;
  }
  // Errors come back as HTTP 4xx/5xx with {error}. (A build job's own `success` field is its result.)
  if (!response.ok) {
    throw new Error((data && data.error) || `${response.status} ${response.statusText}`);
  }
  return data;
}

// Runs an API call and reports a failure as a toast. Returns the data, or null on failure.
async function attempt(promise, failure) {
  try {
    return await promise;
  } catch (error) {
    toast(`${failure}: ${error.message}`, 'error');
    return null;
  }
}

function toast(message, kind = 'info', ms = 5000) {
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.textContent = message;
  $('#toasts').appendChild(el);
  setTimeout(() => el.remove(), ms);
}

function storage(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(key);
    localStorage.setItem(key, value);
  } catch {
    return null;
  }
  return null;
}

// Shows the shared dialog. Resolves with the value of the button that closed it ('' for Esc).
function openDialog(html) {
  const dialog = $('#dialog');
  $('#dialog-body').innerHTML = html;
  dialog.returnValue = '';
  dialog.showModal();
  return new Promise(resolve => dialog.addEventListener('close', () => resolve(dialog.returnValue), { once: true }));
}

function dialogField(name) {
  return $(`#dialog-body [name="${name}"]`);
}

async function confirmDialog(title, text, okLabel = 'OK', danger = false) {
  const result = await openDialog(`
    <h2>${esc(title)}</h2>
    <p>${esc(text)}</p>
    <div class="dialog-actions">
      <button class="btn" value="">Cancel</button>
      <button class="btn ${danger ? 'danger' : 'primary'}" value="ok">${esc(okLabel)}</button>
    </div>`);
  return result === 'ok';
}

function lightName(id) {
  const light = state.lights.find(l => l.id === id);
  return light ? light.name : `Light ${id}`;
}

// --- tabs ------------------------------------------------------------------------

function showTab(name) {
  $$('.tabs [role=tab]').forEach(tab => tab.setAttribute('aria-selected', String(tab.dataset.tab === name)));
  $$('main > .panel').forEach(panel => { panel.hidden = panel.id !== `tab-${name}`; });
  storage('tab', name);
  if (location.hash !== `#${name}`) history.replaceState(null, '', `#${name}`);
  if (name === 'map') initMap();
  if (name === 'settings') loadSettings();
  if (name === 'pairing') pollPairing();
}

// --- lights ----------------------------------------------------------------------

async function loadLights() {
  const lights = await api('api/lights').catch(() => null);
  if (!lights) return;
  state.lights = lights;
  renderLights();
  renderEffectLights();
  renderRunning();  // names the lights of running effects
  renderUnplaced();
  updateMarkers();
}

async function loadRunning() {
  const running = await api('api/effects/running').catch(() => null);
  if (!running) return;
  state.running = running;
  $('#stop-effects').hidden = !running.length;
  $('#stop-effects').textContent = `⏹ Stop effects (${running.length})`;
  renderRunning();
}

function effectOf(lightId) {
  return state.running.find(run => run.light_ids.includes(lightId));
}

function renderLights() {
  const grid = $('#light-grid');
  $('#lights-empty').hidden = state.lights.length > 0;
  const whites = state.presets.filter(p => p.kelvin);
  grid.innerHTML = state.lights.map(light => {
    const s = light.state || {};
    const effect = effectOf(light.id);
    const notes = [];
    if (light.on_controller === false) notes.push('<span class="warn">not on any controller</span>');
    else if (!s.entity_id) notes.push('<span class="warn">not in Home Assistant yet</span>');
    if (effect) notes.push(`<span class="effect">✨ ${esc(effect.effect.replace('_', ' '))}</span>`);
    return `
      <article class="card ${s.state ? 'is-on' : ''}" data-light="${light.id}">
        <div class="card-head">
          <div>
            <div class="card-title">${esc(light.name)}</div>
            <div class="meta">ID ${light.id}${light.kind ? ' · ' + esc(light.kind.toUpperCase()) : ''}${notes.length ? ' · ' + notes.join(' · ') : ''}</div>
          </div>
          <span class="state-dot ${s.state ? 'on' : ''}" title="${s.state ? 'On' : 'Off'}"></span>
        </div>
        <div class="color-bar" style="background:${s.state ? css(s.rgb) : 'var(--surface-2)'}" data-light-action="color" title="Pick a color"></div>
        ${whites.length ? `<div class="dots">${whites.map(p => `<button class="dot" style="background:${css(p.swatch)}" title="${esc(p.label)} (${p.kelvin} K)" data-light-action="preset" data-preset="${p.id}"></button>`).join('')}</div>` : ''}
        <div class="card-actions">
          <button class="btn grow ${s.state ? 'on' : ''}" data-light-action="toggle">${s.state ? 'On' : 'Off'}</button>
          <button class="btn" data-light-action="color">Color</button>
          <button class="btn" data-light-action="more" title="More">⋯</button>
        </div>
      </article>`;
  }).join('');
}

async function setLight(id, body, failure = 'Could not set the light') {
  const ok = await attempt(api(`api/lights/${id}`, { method: 'POST', body }), failure);
  if (ok) setTimeout(refreshLights, 600);
}

async function setAllLights(body) {
  const ok = await attempt(api('api/lights/bulk', { method: 'POST', body }), 'Could not set the lights');
  if (ok) setTimeout(refreshLights, 600);
}

async function refreshLights() {
  await loadRunning();
  await loadLights();
}

function brightnessOf(light) {
  const s = light.state || {};
  return s.state && s.brightness ? s.brightness : undefined;
}

async function onLightAction(id, action, button) {
  const light = state.lights.find(l => l.id === id);
  if (!light) return;
  if (action === 'toggle') {
    // Only switch: Home Assistant turns the lamp back on with its last color or white.
    await setLight(id, { state: !(light.state && light.state.state) }, 'Could not switch the light');
  } else if (action === 'preset') {
    await setLight(id, { preset: button.dataset.preset, brightness: brightnessOf(light) });
  } else if (action === 'color') {
    await colorDialog(light);
  } else if (action === 'more') {
    await lightMenu(light);
  }
}

async function colorDialog(light) {
  const s = light.state || {};
  const result = await openDialog(`
    <h2>${esc(light.name)}</h2>
    <div class="chips">${state.presets.map(p => `<button class="chip" value="preset:${p.id}"><span class="swatch" style="background:${css(p.swatch)}"></span>${esc(p.label)}</button>`).join('')}</div>
    <label>Custom color <input type="color" name="color" value="${toHex(s.rgb)}"></label>
    <label>Brightness <input type="range" name="brightness" min="1" max="255" value="${s.brightness || 255}"></label>
    <div class="dialog-actions">
      <button class="btn" value="">Cancel</button>
      <button class="btn primary" value="apply">Apply color</button>
    </div>`);
  if (!result) return;
  const brightness = parseInt(dialogField('brightness').value, 10);
  if (result.startsWith('preset:')) {
    await setLight(light.id, { preset: result.slice(7), brightness });
  } else {
    await setLight(light.id, { state: true, rgb: fromHex(dialogField('color').value), brightness });
  }
}

async function lightMenu(light) {
  const result = await openDialog(`
    <h2>${esc(light.name)}</h2>
    <p class="muted small">Light ID ${light.id}${light.state && light.state.entity_id ? ' · ' + esc(light.state.entity_id) : ''}</p>
    <label>Name <input name="name" value="${esc(light.name)}" maxlength="60"></label>
    <div class="dialog-actions">
      <button class="btn danger" value="remove">Remove</button>
      <button class="btn" value="identify">Blink to identify</button>
      <button class="btn" value="">Close</button>
      <button class="btn primary" value="rename">Save name</button>
    </div>`);
  if (result === 'rename') {
    const name = dialogField('name').value.trim();
    if (name && name !== light.name && await attempt(api(`api/lights/${light.id}`, { method: 'PUT', body: { name } }), 'Could not rename')) {
      toast('Renamed. Install the firmware on your controllers to rename the entity in Home Assistant.', 'ok');
      loadLights();
    }
  } else if (result === 'identify') {
    if (await attempt(api(`api/lights/${light.id}/identify`, { method: 'POST', body: {} }), 'Could not blink the light')) {
      toast(`Watch for ${light.name} blinking three times`, 'info');
    }
  } else if (result === 'remove') {
    if (await confirmDialog('Remove light', `Remove ${light.name} (ID ${light.id}) from the add-on? The lamp itself stays paired; install the firmware afterwards to drop its entity.`, 'Remove', true)
        && await attempt(api(`api/lights/${light.id}/unpair`, { method: 'POST', body: {} }), 'Could not remove')) {
      toast(`${light.name} removed`, 'ok');
      loadLights();
    }
  }
}

function renderPresetChips() {
  $('#all-presets').innerHTML = state.presets.map(p =>
    `<button class="chip" data-all-preset="${p.id}" title="${p.kelvin ? p.kelvin + ' K' : ''}"><span class="swatch" style="background:${css(p.swatch)}"></span>${esc(p.label)}</button>`
  ).join('');
}

// --- pairing ---------------------------------------------------------------------

function renderPairingControllers() {
  const select = $('#pairing-controller');
  const current = select.value;
  select.innerHTML = state.controllers.length
    ? state.controllers.map(c => `<option value="${esc(c.name)}">${esc(c.name)}${c.online ? '' : ' (offline)'}</option>`).join('')
    : '<option value="">No controllers yet</option>';
  if (state.controllers.some(c => c.name === current)) select.value = current;
}

async function startPairing() {
  const controller = $('#pairing-controller').value;
  if (!controller) {
    toast('Add a controller first (Controllers tab)', 'warn');
    return;
  }
  const result = await attempt(api('api/pairing/start', { method: 'POST', body: { controller, seconds: 120 } }), 'Could not start pairing');
  if (result) {
    toast(`${result.controller} is looking for reset lamps for ${result.seconds} s`, 'ok');
    schedulePairing(1000);
  }
}

async function stopPairing() {
  await attempt(api('api/pairing/stop', { method: 'POST', body: { controller: $('#pairing-controller').value } }), 'Could not stop pairing');
  schedulePairing(1500);
}

function schedulePairing(ms) {
  clearTimeout(state.pairingTimer);
  state.pairingTimer = setTimeout(pollPairing, ms);
}

async function pollPairing() {
  const controller = $('#pairing-controller').value;
  if (!controller) return;
  const result = await api(`api/pairing/status?controller=${encodeURIComponent(controller)}`).catch(() => null);
  if (!result) return;
  renderPairing(result);
  if (result.added && result.added.length) {
    toast(`Added light ${result.added.join(', ')}. Install the firmware on your controllers to use it in Home Assistant.`, 'ok', 8000);
    loadLights();
  }
  if (result.active) schedulePairing(2000);
}

function renderPairing(result) {
  $('#pairing-start').hidden = result.active;
  $('#pairing-stop').hidden = !result.active;
  const status = $('#pairing-status');
  const table = $('#pairing-table');
  if (!result.available) {
    status.textContent = 'This controller has no pairing sensor in Home Assistant yet. Install firmware 2.1.0 or newer on it.';
    table.hidden = true;
    return;
  }
  const paired = result.lamps.filter(l => l.state === 'ok').length;
  status.textContent = result.active
    ? `Looking for reset lamps… ${result.lamps.length} found, ${paired} paired.`
    : result.lamps.length ? `Pairing finished: ${paired} of ${result.lamps.length} paired.` : 'Not pairing.';
  table.hidden = !result.lamps.length;
  const labels = {
    ok: 'Paired',
    wait: result.active ? 'Pairing…' : 'Not confirmed',
    nofree: 'No free light ID',
    unsup: 'Extended protocol, not supported yet',
  };
  table.innerHTML = '<tr><th>Light ID</th><th>Device</th><th>Type</th><th>State</th><th></th></tr>' + result.lamps.map(l => `
    <tr>
      <td>${l.light_id || '–'}</td>
      <td><code>${esc(l.did)}</code></td>
      <td>${esc(l.type_label || l.type_code)}</td>
      <td><span class="badge ${l.state === 'ok' ? 'ok' : l.state === 'wait' ? 'busy' : 'bad'}">${esc(labels[l.state] || l.state)}</span></td>
      <td>${l.known ? 'in Lights' : (l.state === 'wait' && !result.active && l.light_id
        ? `<button class="btn small" data-add-lamp="${l.light_id}" data-did="${esc(l.did)}" data-type="${l.type_code}" title="The lamp may have joined without the controller hearing it confirm">Add anyway</button>` : '')}</td>
    </tr>`).join('');
}

async function addUnconfirmed(button) {
  const body = { light_id: Number(button.dataset.addLamp), did: button.dataset.did, type_code: Number(button.dataset.type) };
  if (await attempt(api('api/pairing/add', { method: 'POST', body }), 'Could not add the lamp')) {
    toast(`Added light ${body.light_id}. If it does not react after installing the firmware, reset and pair it again.`, 'ok', 8000);
    loadLights();
    pollPairing();
  }
}

// --- controllers -------------------------------------------------------------------

async function loadControllers() {
  const controllers = await api('api/controllers').catch(() => null);
  if (!controllers) return;
  state.controllers = controllers;
  renderControllers();
  renderPairingControllers();
  renderUnplaced();
  updateMarkers();
  controllers.filter(c => c.job && c.job.running).forEach(c => watchJob(slug(c.name)));
}

function jobBadge(job) {
  if (!job) return '';
  if (job.running) return `<span class="badge busy">${job.action === 'compile' ? 'Building' : 'Installing'} · ${job.elapsed}s</span>`;
  return job.success ? '<span class="badge ok">Last build OK</span>' : '<span class="badge bad">Last build failed</span>';
}

function renderControllers() {
  const grid = $('#controller-grid');
  if (!state.controllers.length) {
    grid.innerHTML = '<div class="notice">No controllers yet. Create one, then install its firmware on an ESP32.</div>';
    return;
  }
  grid.innerHTML = state.controllers.map(c => {
    const board = (BOARDS.find(b => b[0] === c.board) || [c.board || 'esp32dev', c.board || 'esp32dev'])[1];
    return `
      <article class="card" data-controller="${esc(c.name)}">
        <div class="card-head">
          <div>
            <div class="card-title">${esc(c.name)}</div>
            <div class="meta">${esc(board)} · ${c.light_count} light${c.light_count === 1 ? '' : 's'}${c.all_lights ? ' (all)' : ''}${c.ip ? ' · ' + esc(c.ip) : ''}</div>
          </div>
          <span class="badge ${c.online ? 'ok' : ''}">${c.online ? 'Online' : c.in_home_assistant ? 'Offline' : 'Not in HA yet'}</span>
        </div>
        ${c.warning ? `<div class="meta"><span class="warn">${esc(c.warning)}</span></div>` : ''}
        ${c.config_status !== 'configured' ? '<div class="meta"><span class="warn">No ESPHome configuration yet: click Regenerate configs.</span></div>' : ''}
        <div>${jobBadge(c.job)}</div>
        <div class="card-actions">
          <button class="btn primary" data-controller-action="install">⚡ Install</button>
          <button class="btn" data-controller-action="build">Build</button>
          <button class="btn" data-controller-action="output">Output</button>
          <button class="btn" data-controller-action="edit">Edit</button>
          <a class="btn" href="api/esphome/download/${encodeURIComponent(slug(c.name))}" download title="Download the ESPHome YAML">YAML</a>
        </div>
      </article>`;
  }).join('');
}

async function onControllerAction(name, action) {
  const controller = state.controllers.find(c => c.name === name);
  if (!controller) return;
  const key = slug(name);
  if (action === 'install') await installDialog(controller);
  else if (action === 'build') await startJob(key, 'build', {}, 'Build');
  else if (action === 'output') showOutput(key);
  else if (action === 'edit') await editController(controller);
}

async function startJob(key, endpoint, body, label) {
  const result = await attempt(api(`api/esphome/${endpoint}/${encodeURIComponent(key)}`, { method: 'POST', body }), `Could not start ${label.toLowerCase()}`);
  if (!result) return false;
  toast(`${label} started for ${key}. The first build after an update downloads the toolchain and can take 10 minutes or more.`, 'info', 8000);
  watchJob(key);
  showOutput(key);
  return true;
}

function watchJob(key) {
  if (state.jobsWatched.has(key)) return;
  state.jobsWatched.add(key);
  const tick = async () => {
    const job = await api(`api/esphome/job/${encodeURIComponent(key)}`).catch(() => null);
    if (!job) {
      state.jobsWatched.delete(key);
      return;
    }
    if (state.outputFor === key) setOutput(job.output);
    if (job.running) {
      setTimeout(tick, 3000);
      return;
    }
    state.jobsWatched.delete(key);
    const what = job.action === 'compile' ? 'build' : 'install';
    toast(job.success ? `${key}: ${what} finished` : `${key}: ${what} failed (${job.error || 'see Output'})`, job.success ? 'ok' : 'error', 9000);
    loadControllers();
  };
  tick();
}

async function waitForJob(key) {
  for (;;) {
    await new Promise(resolve => setTimeout(resolve, 3000));
    const job = await api(`api/esphome/job/${encodeURIComponent(key)}`).catch(() => null);
    if (!job || !job.running) return job;
  }
}

async function showOutput(key) {
  state.outputFor = key;
  $('#output-box').hidden = false;
  $('#output-title').textContent = `Build output · ${key}`;
  const data = await api(`api/esphome/logs/${encodeURIComponent(key)}`).catch(() => null);
  setOutput(data ? data.logs : 'No output.');
}

function setOutput(text) {
  const pre = $('#output-text');
  const atEnd = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 20;
  pre.textContent = text || '';
  if (atEnd) pre.scrollTop = pre.scrollHeight;
}

function canFlashFromBrowser() {
  // Web Serial: Chrome/Edge, on HTTPS pages only.
  return window.isSecureContext && 'serial' in navigator;
}

async function installDialog(controller) {
  const key = slug(controller.name);
  const ports = ((await api('api/esphome/ports').catch(() => null)) || {}).ports || [];
  const usb = ports.length
    ? ports.map((p, i) => `<label class="option"><input type="radio" name="target" value="usb" data-port="${esc(p.path)}" ${i === 0 ? 'checked' : ''}><span>USB on the Home Assistant machine: ${esc(p.label)}</span></label>`).join('')
    : '<p class="muted small">No ESP32 is plugged into the Home Assistant machine. Plug one in and open this dialog again to install over USB there.</p>';
  const result = await openDialog(`
    <h2>Install ${esc(controller.name)}</h2>
    <p class="muted small">Builds the firmware if needed, then installs it on the ESP32 you choose.</p>
    ${usb}
    <label class="option"><input type="radio" name="target" value="browser" ${ports.length ? '' : 'checked'}><span>This computer: ${canFlashFromBrowser()
      ? 'flash the ESP32 plugged into the computer you are using now (Chrome or Edge)'
      : 'download the firmware and flash it with web.esphome.io (this page is not on HTTPS, so the browser cannot flash it directly)'}</span></label>
    <label class="option"><input type="radio" name="target" value="ota"><span>Wi-Fi (OTA): for an ESP32 that already runs this firmware${controller.online ? ' (online now)' : ''}</span></label>
    <p class="muted small">A new ESP32, or one that ran other firmware, needs USB or this computer the first time.</p>
    <div class="dialog-actions">
      <button class="btn" value="">Cancel</button>
      <button class="btn primary" value="go">Continue</button>
    </div>`);
  if (result !== 'go') return;
  const choice = $('#dialog-body input[name=target]:checked');
  if (!choice) return;
  if (choice.value === 'browser') await installFromBrowser(key);
  else await startJob(key, 'flash', { port: choice.value === 'usb' ? choice.dataset.port : 'OTA' }, 'Install');
}

async function installFromBrowser(key) {
  const status = await attempt(api(`api/esphome/build-status/${encodeURIComponent(key)}`), 'Could not check the build');
  if (!status) return;
  if (!status.up_to_date) {
    if (!(status.job && status.job.running) && !(await startJob(key, 'build', {}, 'Build'))) return;
    toast('Building the firmware first. The installer opens when it is ready.', 'info', 8000);
    const job = await waitForJob(key);
    if (!job || !job.success) return;
  }
  const firmware = `api/esphome/firmware/${encodeURIComponent(key)}`;
  if (canFlashFromBrowser()) {
    if (!document.querySelector(`script[src="${ESP_WEB_TOOLS}"]`)) {
      const script = document.createElement('script');
      script.type = 'module';
      script.src = ESP_WEB_TOOLS;
      document.head.appendChild(script);
    }
    await openDialog(`
      <h2>Install from this computer</h2>
      <p>Connect the ESP32 with a USB data cable, click Connect and pick its port. No port listed? Install the USB driver
        of your board (CP210x or CH340).</p>
      <esp-web-install-button manifest="api/esphome/manifest/${encodeURIComponent(key)}">
        <button slot="activate" type="button" class="btn primary">Connect</button>
        <span slot="unsupported">This browser cannot flash. Use Chrome or Edge.</span>
      </esp-web-install-button>
      <p class="muted small">Or <a href="${firmware}" download>download the firmware</a> and flash it with web.esphome.io.</p>
      <div class="dialog-actions"><button class="btn" value="">Close</button></div>`);
  } else {
    await openDialog(`
      <h2>Install from this computer</h2>
      <ol class="steps">
        <li><a class="btn primary" href="${firmware}" download>Download the firmware</a></li>
        <li>Connect the ESP32 to this computer with a USB data cable.</li>
        <li>Open <a href="https://web.esphome.io" target="_blank" rel="noopener">web.esphome.io</a> in Chrome or Edge, click
          Connect, then Install, and choose the downloaded file.</li>
      </ol>
      <div class="dialog-actions"><button class="btn" value="">Close</button></div>`);
  }
}

async function wifiOptions() {
  const networks = ((await api('api/wifi-networks').catch(() => null)) || {}).networks || [];
  return networks.map(n => `<option value="${n.id}" ${n.is_default ? 'selected' : ''}>${esc(n.ssid)}</option>`).join('');
}

async function newController() {
  const options = await wifiOptions();
  const result = await openDialog(`
    <h2>New controller</h2>
    <label>Name <input name="name" placeholder="esp-ble-bridge" maxlength="31"></label>
    <label>Board <select name="board">${BOARDS.map(([id, label]) => `<option value="${id}">${esc(label)}</option>`).join('')}</select></label>
    <label>Wi-Fi network <select name="network">${options}<option value="new">Another network…</option></select></label>
    <div class="two" data-new-wifi ${options ? 'hidden' : ''}>
      <label>Network name (SSID) <input name="ssid" autocomplete="off"></label>
      <label>Password <input name="password" type="password" autocomplete="new-password"></label>
    </div>
    <p class="muted small">The controller gets every light. Choose a subset later with Edit.</p>
    <div class="dialog-actions">
      <button class="btn" value="">Cancel</button>
      <button class="btn primary" value="create">Create</button>
    </div>`);
  if (result !== 'create') return;
  const network = dialogField('network').value;
  const body = { name: dialogField('name').value.trim(), board: dialogField('board').value, generate_esphome: true };
  if (network === 'new') {
    body.wifi_ssid = dialogField('ssid').value.trim();
    body.wifi_password = dialogField('password').value;
  } else {
    body.network_id = Number(network);
  }
  const created = await attempt(api('api/controllers', { method: 'POST', body }), 'Could not create the controller');
  if (created) {
    toast(`Created ${created.name}. Click ⚡ Install to put the firmware on an ESP32.`, 'ok', 8000);
    loadControllers();
  }
}

async function editController(controller) {
  const selected = new Set(controller.light_ids || []);
  const all = controller.all_lights;
  const result = await openDialog(`
    <h2>${esc(controller.name)}</h2>
    <label>Board <select name="board">${BOARDS.map(([id, label]) => `<option value="${id}" ${id === (controller.board || 'esp32dev') ? 'selected' : ''}>${esc(label)}</option>`).join('')}</select></label>
    <label>Fixed IP address (optional, for Wi-Fi installs) <input name="ip" value="${esc(controller.ip_address || '')}" placeholder="automatic"></label>
    <label class="check"><input type="checkbox" name="all" ${all ? 'checked' : ''}> All lights</label>
    <div class="checklist" data-light-pick ${all ? 'hidden' : ''}>
      ${state.lights.map(l => `<label><input type="checkbox" name="light" value="${l.id}" ${all || selected.has(l.id) ? 'checked' : ''}> ${esc(l.name)}</label>`).join('')}
    </div>
    <div class="dialog-actions">
      <button class="btn danger" value="remove">Remove controller</button>
      <button class="btn" value="">Cancel</button>
      <button class="btn primary" value="save">Save</button>
    </div>`);
  if (result === 'save') {
    const body = {
      board: dialogField('board').value,
      ip_address: dialogField('ip').value.trim(),
      light_ids: dialogField('all').checked ? null : $$('#dialog-body input[name=light]:checked').map(i => Number(i.value)),
    };
    if (await attempt(api(`api/controllers/${controller.id}`, { method: 'PUT', body }), 'Could not save')) {
      toast('Saved. Install the firmware to apply it.', 'ok');
      loadControllers();
      loadLights();
    }
  } else if (result === 'remove') {
    if (await confirmDialog('Remove controller', `Remove ${controller.name} and delete its ESPHome configuration file? The ESP32 keeps its firmware until you flash something else.`, 'Remove', true)
        && await attempt(api(`api/controllers/${encodeURIComponent(controller.name)}/reset`, { method: 'POST', body: {} }), 'Could not remove')) {
      toast(`${controller.name} removed`, 'ok');
      loadControllers();
      loadLights();
    }
  }
}

async function regenerate() {
  const result = await attempt(api('api/esphome/generate', { method: 'POST', body: { force: true } }), 'Could not write the configurations');
  if (result) {
    const counts = Object.entries(result.stats || {}).map(([k, v]) => `${v} ${k.replace('_', ' ')}`).join(', ');
    toast(`Configurations: ${counts || 'nothing to do'}. Install the firmware to apply them.`, 'ok', 7000);
    loadControllers();
  }
}

// --- effects -----------------------------------------------------------------------

function renderEffectLights() {
  const known = new Set(state.lights.map(l => l.id));
  state.effectSelection.forEach(id => { if (!known.has(id)) state.effectSelection.delete(id); });
  $('#effect-lights').innerHTML = state.lights.map(l =>
    `<label><input type="checkbox" value="${l.id}" ${state.effectSelection.has(l.id) ? 'checked' : ''}> ${esc(l.name)}</label>`
  ).join('') || '<span class="muted">No lights yet.</span>';
}

function renderEffects() {
  $('#effect-list').innerHTML = state.effects.map(e =>
    `<button class="pick" data-effect="${esc(e.name)}"><strong>${esc(e.name.replace('_', ' '))}</strong><span class="muted small">${esc(EFFECT_INFO[e.name] || '')}</span></button>`
  ).join('');
}

function renderRunning() {
  $('#running-effects').innerHTML = state.running.length
    ? state.running.map(run => {
      const names = run.light_ids.map(lightName);
      const shown = names.length > 4 ? `${names.slice(0, 4).join(', ')} and ${names.length - 4} more` : names.join(', ');
      return `<li><span><strong>${esc(run.effect.replace('_', ' '))}</strong> · ${esc(shown)}</span><button class="btn small danger" data-stop-effect="${esc(run.id)}">Stop</button></li>`;
    }).join('')
    : '<li class="empty">No effects running</li>';
}

async function startEffect(name) {
  if (!state.effectSelection.size) {
    toast('Select at least one light', 'warn');
    return;
  }
  const params = {};
  if (name === 'strobe' || name === 'breathe' || name === 'twinkle') {
    const result = await openDialog(`
      <h2>${esc(name)}</h2>
      <label>Color <input type="color" name="color" value="#ffffff"></label>
      <div class="dialog-actions"><button class="btn" value="">Cancel</button><button class="btn primary" value="go">Start</button></div>`);
    if (result !== 'go') return;
    params.color = fromHex(dialogField('color').value);
  }
  const result = await attempt(api(`api/effects/${encodeURIComponent(name)}`, { method: 'POST', body: { light_ids: [...state.effectSelection], params } }), 'Could not start the effect');
  if (result) {
    toast(`${name.replace('_', ' ')} started. Stop it here or with ⏹ Stop effects.`, 'ok');
    refreshLights();
  }
}

async function stopEffects(effectId) {
  const result = await attempt(api('api/effects/stop', { method: 'POST', body: effectId ? { effect_id: effectId } : {} }), 'Could not stop');
  if (result && !effectId) toast(`Stopped ${result.stopped} effect(s). The lights keep their current color.`, 'ok');
  refreshLights();
}

// --- scenes -------------------------------------------------------------------------

async function loadScenes() {
  const scenes = await api('api/scenes').catch(() => null);
  if (!scenes) return;
  state.scenes = scenes;
  $('#scenes-empty').hidden = scenes.length > 0;
  $('#scene-grid').innerHTML = scenes.map(scene => {
    const colors = (scene.lights || []).slice(0, 6).map(item => {
      const rgb = Array.isArray(item.rgb) ? item.rgb : item.rgb ? [item.rgb.r, item.rgb.g, item.rgb.b] : [255, 255, 255];
      return `<span class="swatch" style="background:${css(rgb)}"></span>`;
    }).join('');
    return `
      <div class="pick" data-scene="${esc(scene.name)}">
        <strong>${esc(scene.name)}</strong>
        <div class="row">${scene.effect ? `<span class="muted small">Effect: ${esc(scene.effect.replace('_', ' '))}</span>` : colors}</div>
        <div class="row" style="margin-top:8px">
          <button class="btn small primary" data-scene-action="apply">Apply</button>
          <button class="btn small danger" data-scene-action="delete">Delete</button>
        </div>
      </div>`;
  }).join('');
}

async function newScene() {
  const result = await openDialog(`
    <h2>New scene</h2>
    <label>Name <input name="name" placeholder="Movie night" maxlength="40"></label>
    <label>Type <select name="type"><option value="colors">A color per light</option><option value="effect">Start an effect on every light</option></select></label>
    <div data-scene-colors>
      ${state.lights.map(l => `<label class="check"><input type="checkbox" name="light" value="${l.id}" checked> <input type="color" name="color-${l.id}" value="${toHex(l.state && l.state.rgb)}"> ${esc(l.name)}</label>`).join('')}
    </div>
    <label data-scene-effect hidden>Effect <select name="effect">${state.effects.map(e => `<option value="${esc(e.name)}">${esc(e.name.replace('_', ' '))}</option>`).join('')}</select></label>
    <div class="dialog-actions"><button class="btn" value="">Cancel</button><button class="btn primary" value="save">Save scene</button></div>`);
  if (result !== 'save') return;
  const name = dialogField('name').value.trim();
  if (!name) {
    toast('A scene needs a name', 'warn');
    return;
  }
  const scene = { name };
  if (dialogField('type').value === 'effect') {
    scene.effect = dialogField('effect').value;
  } else {
    scene.lights = $$('#dialog-body input[name=light]:checked').map(input => ({
      id: Number(input.value),
      rgb: fromHex(dialogField(`color-${input.value}`).value),
      brightness: 255,
    }));
  }
  if (await attempt(api('api/scenes', { method: 'POST', body: scene }), 'Could not save the scene')) {
    toast(`Scene ${name} saved`, 'ok');
    loadScenes();
  }
}

async function onSceneAction(name, action) {
  if (action === 'apply') {
    if (await attempt(api(`api/scenes/${encodeURIComponent(name)}`, { method: 'POST', body: {} }), 'Could not apply the scene')) {
      setTimeout(refreshLights, 600);
    }
  } else if (action === 'delete' && await confirmDialog('Delete scene', `Delete the scene ${name}?`, 'Delete', true)) {
    if (await attempt(api(`api/scenes/${encodeURIComponent(name)}`, { method: 'DELETE' }), 'Could not delete')) loadScenes();
  }
}

// --- map -----------------------------------------------------------------------------

function initMap() {
  const enabled = state.settings.map_enabled !== false;
  $('#map-disabled').hidden = enabled;
  $('.map-layout').hidden = !enabled;
  if (!enabled || !window.L) return;
  if (state.map) {
    setTimeout(() => state.map.invalidateSize(), 50);
    return;
  }
  const lat = Number(state.settings.latitude) || 0;
  const lon = Number(state.settings.longitude) || 0;
  state.map = L.map('map').setView([lat, lon], Number(state.settings.zoom) || (lat || lon ? 18 : 2));
  const street = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 20, maxNativeZoom: 19, attribution: '© OpenStreetMap contributors',
  });
  const satellite = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
    maxZoom: 20, maxNativeZoom: 19, attribution: 'Imagery © Esri',
  });
  satellite.addTo(state.map);
  L.control.layers({ Satellite: satellite, Street: street }).addTo(state.map);
  updateMarkers();
}

function placeables() {
  return [
    ...state.lights.map(l => ({ key: `light-${l.id}`, kind: 'light', id: l.id, name: l.name, location: l.location })),
    ...state.controllers.map(c => ({ key: `controller-${c.id}`, kind: 'controller', id: c.id, name: c.name, location: c.location })),
  ];
}

function isPlaced(item) {
  return item.location && item.location.x != null && item.location.y != null;
}

function renderUnplaced() {
  const items = placeables().filter(item => !isPlaced(item));
  $('#unplaced').innerHTML = items.length
    ? items.map(item => `<li data-place="${item.key}"><span>${item.kind === 'controller' ? '📡' : '💡'} ${esc(item.name)}</span><span class="muted small">Place</span></li>`).join('')
    : '<li class="empty">Everything is on the map</li>';
}

async function saveLocation(item, latlng) {
  const body = { x: latlng.lng, y: latlng.lat };
  const path = item.kind === 'light' ? `api/lights/${item.id}/location` : `api/controllers/${item.id}/location`;
  const ok = await attempt(api(path, { method: item.kind === 'light' ? 'POST' : 'PUT', body }), 'Could not save the position');
  if (ok) item.location = body;
  return ok;
}

function updateMarkers() {
  if (!state.map) return;
  const items = placeables().filter(isPlaced);
  const keep = new Set(items.map(item => item.key));
  state.markers.forEach((marker, key) => {
    if (!keep.has(key)) {
      marker.remove();
      state.markers.delete(key);
    }
  });
  items.forEach(item => {
    const position = [item.location.y, item.location.x];
    let marker = state.markers.get(item.key);
    if (!marker) {
      const icon = L.divIcon({ className: '', html: `<div class="map-pin ${item.kind}">${item.kind === 'controller' ? '📡' : '💡'}</div>`, iconSize: [28, 28], iconAnchor: [14, 14] });
      marker = L.marker(position, { draggable: true, icon, title: item.name }).addTo(state.map);
      marker.on('dragend', () => saveLocation(item, marker.getLatLng()));
      state.markers.set(item.key, marker);
    } else if (!marker.dragging || !marker.dragging.moved()) {
      marker.setLatLng(position);
    }
    marker.bindTooltip(item.name);
  });
}

async function placeItem(key) {
  const item = placeables().find(i => i.key === key);
  if (!item || !state.map) return;
  if (await saveLocation(item, state.map.getCenter())) {
    await (item.kind === 'light' ? loadLights() : loadControllers());
  }
}

// --- settings ---------------------------------------------------------------------------

async function loadSettings() {
  const settings = await api('api/settings').catch(() => null);
  if (!settings) return;
  state.settings = settings;
  const form = $('#settings-form');
  for (const [key, value] of Object.entries(settings)) {
    const input = form.elements[key];
    if (!input || input instanceof RadioNodeList) continue;
    if (input.type === 'checkbox') input.checked = !!value;
    else input.value = value ?? '';
  }
  loadWifi();
}

async function saveSettings(event) {
  event.preventDefault();
  const form = $('#settings-form');
  const body = {};
  for (const input of form.elements) {
    if (!input.name) continue;
    body[input.name] = input.type === 'checkbox' ? input.checked : input.type === 'number' ? (input.value === '' ? null : Number(input.value)) : input.value.trim();
  }
  const result = await attempt(api('api/settings', { method: 'POST', body }), 'Could not save the settings');
  if (result) {
    toast(result.message || 'Settings saved', 'ok');
    state.settings = { ...state.settings, ...body };
    if (state.map) state.map.setView([Number(body.latitude) || 0, Number(body.longitude) || 0], Number(body.zoom) || 18);
  }
}

async function loadWifi() {
  const networks = ((await api('api/wifi-networks').catch(() => null)) || {}).networks || [];
  $('#wifi-list').innerHTML = networks.length
    ? networks.map(n => `
      <li>
        <span>${esc(n.ssid)} ${n.is_default ? '<span class="badge ok">default</span>' : ''}</span>
        <span class="row">
          ${n.is_default || n.id < 0 ? '' : `<button type="button" class="btn small" data-wifi-default="${n.id}">Make default</button>`}
          ${n.id < 0 ? '' : `<button type="button" class="btn small danger" data-wifi-delete="${n.id}">Delete</button>`}
        </span>
      </li>`).join('')
    : '<li class="empty">No networks saved</li>';
}

async function addWifi() {
  const result = await openDialog(`
    <h2>Add Wi-Fi network</h2>
    <label>Network name (SSID) <input name="ssid" autocomplete="off"></label>
    <label>Password <input name="password" type="password" autocomplete="new-password"></label>
    <div class="dialog-actions"><button class="btn" value="">Cancel</button><button class="btn primary" value="add">Add</button></div>`);
  if (result !== 'add') return;
  const body = { ssid: dialogField('ssid').value.trim(), password: dialogField('password').value };
  if (await attempt(api('api/wifi-networks', { method: 'POST', body }), 'Could not add the network')) loadWifi();
}

async function findAddress() {
  const query = $('#address-query').value.trim();
  if (!query) return;
  let results = [];
  try {
    const response = await fetch(`https://nominatim.openstreetmap.org/search?format=json&limit=5&q=${encodeURIComponent(query)}`);
    results = await response.json();
  } catch {
    toast('Address search is not reachable', 'error');
    return;
  }
  $('#address-results').innerHTML = results.length
    ? results.map(r => `<li><span class="small">${esc(r.display_name)}</span><button type="button" class="btn small" data-use-address="${r.lat},${r.lon}">Use</button></li>`).join('')
    : '<li class="empty">Nothing found</li>';
}

function useAddress(value) {
  const [lat, lon] = value.split(',');
  const form = $('#settings-form');
  form.elements.latitude.value = Number(lat).toFixed(6);
  form.elements.longitude.value = Number(lon).toFixed(6);
  form.elements.zoom.value = 19;
  $('#address-results').innerHTML = '';
  toast('Location set. Save settings to keep it.', 'info');
}

function randomMeshKey() {
  const bytes = new Uint8Array(4);
  crypto.getRandomValues(bytes);
  $('#set-mesh-key').value = [...bytes].map(b => b.toString(16).padStart(2, '0')).join('');
  $('#set-mesh-key').type = 'text';
  toast('New key filled in. Save settings to use it; lamps then need to be paired again.', 'warn', 8000);
}

async function systemReset() {
  if (!await confirmDialog('Remove everything', 'Remove all lights, controllers and scenes from the add-on? This cannot be undone.', 'Remove everything', true)) return;
  const result = await attempt(api('api/system/reset', { method: 'POST', body: { confirm: true } }), 'Reset failed');
  if (result) {
    toast(result.message, 'ok');
    refreshAll();
  }
}

// --- import -----------------------------------------------------------------------------

async function importDialog() {
  let payload = null;
  let mesh = null;
  const body = $('#dialog-body');
  const dialogDone = openDialog(`
    <h2>Import your BRMesh export</h2>
    <p class="muted small">In the BRMesh app, export your setup (a file named BRMESH_BACKUP…) and choose it here. It is read by
      the add-on; nothing has to be copied into Home Assistant first.</p>
    <div class="drop" data-drop><input type="file" name="file"><span>Drop the file here or tap to choose it</span></div>
    <details><summary class="small">Or paste the file's text or the app's share code</summary>
      <textarea name="text" rows="4" style="width:100%"></textarea>
      <button type="button" class="btn small" data-read-text>Read</button>
    </details>
    <div data-preview></div>
    <div class="dialog-actions"><button class="btn" value="">Close</button><button class="btn primary" value="apply" data-apply disabled>Import</button></div>`);

  const preview = async () => {
    const options = new URLSearchParams({ apply: '0' });
    if (mesh) options.set('mesh', mesh);
    const result = await send(options);
    if (result) showPreview(result.preview);
  };

  const send = async options => {
    const path = `api/import/upload?${options}`;
    try {
      if (payload.file) {
        const form = new FormData();
        form.append('file', payload.file);
        return await api(path, { method: 'POST', form });
      }
      return await api(path, { method: 'POST', body: { text: payload.text } });
    } catch (error) {
      $('[data-preview]', body).innerHTML = `<div class="notice">${esc(error.message)}</div>`;
      $('[data-apply]', body).disabled = true;
      return null;
    }
  };

  const showPreview = p => {
    const meshes = p.meshes.length > 1
      ? `<label>Mesh <select data-mesh>${p.meshes.map(m => `<option value="${esc(m.id)}" ${m.id === p.mesh_id ? 'selected' : ''}>${esc(m.label)} (${m.lights} lights, key ${esc(m.mesh_key_masked)})</option>`).join('')}</select></label>` : '';
    const skipped = p.skipped.length
      ? `<p class="muted small">Skipped: ${p.skipped.map(s => `${esc(s.name || s.light_id)} (${esc(s.reason)})`).join(', ')}</p>` : '';
    $('[data-preview]', body).innerHTML = `
      ${meshes}
      <p><strong>${p.lights.length} light(s)</strong>, mesh key ${p.mesh_key_found ? esc(p.mesh_key_masked) : '<span class="muted">not in this file</span>'}
        ${p.mesh_key_differs ? '<br><span class="small" style="color:var(--warn)">This key differs from the current one; importing replaces it.</span>' : ''}</p>
      <div class="table-wrap"><table class="table">
        <tr><th>ID</th><th>Name</th><th>Type</th><th></th></tr>
        ${p.lights.map(l => `<tr><td>${l.light_id}</td><td>${esc(l.name)}</td><td>${esc(l.type_label || l.kind)}</td><td>${l.exists ? '<span class="badge">known</span>' : '<span class="badge ok">new</span>'}</td></tr>`).join('')}
      </table></div>
      ${skipped}
      <label class="check"><input type="checkbox" name="replace"> Replace the light list (remove lights that are not in this file)</label>
      <label class="check"><input type="checkbox" name="update_names" checked> Use the app's names for lights I already have</label>`;
    $('[data-apply]', body).disabled = !p.lights.length && !p.mesh_key_found;
    const meshSelect = $('[data-mesh]', body);
    if (meshSelect) meshSelect.onchange = () => { mesh = meshSelect.value; preview(); };
  };

  const fileInput = $('input[name=file]', body);
  const drop = $('[data-drop]', body);
  fileInput.onchange = () => {
    if (!fileInput.files.length) return;
    payload = { file: fileInput.files[0] };
    $('span', drop).textContent = fileInput.files[0].name;
    mesh = null;
    preview();
  };
  drop.ondragover = event => { event.preventDefault(); drop.classList.add('over'); };
  drop.ondragleave = () => drop.classList.remove('over');
  drop.ondrop = event => {
    event.preventDefault();
    drop.classList.remove('over');
    if (event.dataTransfer.files.length) {
      payload = { file: event.dataTransfer.files[0] };
      $('span', drop).textContent = event.dataTransfer.files[0].name;
      mesh = null;
      preview();
    }
  };
  $('[data-read-text]', body).onclick = () => {
    const text = $('textarea[name=text]', body).value.trim();
    if (!text) return;
    payload = { text };
    mesh = null;
    preview();
  };

  const result = await dialogDone;
  if (result !== 'apply' || !payload) return;
  const options = new URLSearchParams({
    apply: '1',
    replace: dialogField('replace') && dialogField('replace').checked ? '1' : '0',
    update_names: dialogField('update_names') && dialogField('update_names').checked ? '1' : '0',
  });
  if (mesh) options.set('mesh', mesh);
  const applied = await send(options);
  if (!applied) {
    toast('Import failed', 'error');
    return;
  }
  const r = applied.result;
  toast(`Imported: ${r.added.length} new, ${r.updated.length} updated, ${r.total_lights} lights in total${r.mesh_key_changed ? ', mesh key updated' : ''}.`
    + (applied.needs_install ? ' Install the firmware on your controllers to apply it.' : ''), 'ok', 9000);
  refreshAll();
}

// --- startup & events ----------------------------------------------------------------------

function applyTheme(theme) {
  if (theme) document.documentElement.dataset.theme = theme;
  else delete document.documentElement.dataset.theme;
}

function toggleTheme() {
  const dark = document.documentElement.dataset.theme
    ? document.documentElement.dataset.theme === 'dark'
    : window.matchMedia('(prefers-color-scheme: dark)').matches;
  const next = dark ? 'light' : 'dark';
  applyTheme(next);
  storage('theme', next);
}

async function refreshAll() {
  await Promise.all([loadRunning(), loadControllers(), loadScenes()]);
  await loadLights();
}

const ACTIONS = {
  'open-import': importDialog,
  'all-on': () => setAllLights({ state: true }),
  'all-off': () => setAllLights({ state: false }),
  'regenerate': regenerate,
  'new-controller': newController,
  'close-output': () => { $('#output-box').hidden = true; state.outputFor = null; },
  'effect-select-all': () => { state.lights.forEach(l => state.effectSelection.add(l.id)); renderEffectLights(); },
  'effect-select-none': () => { state.effectSelection.clear(); renderEffectLights(); },
  'stop-all-effects': () => stopEffects(),
  'new-scene': newScene,
  'toggle-key': () => {
    const input = $('#set-mesh-key');
    input.type = input.type === 'password' ? 'text' : 'password';
  },
  'random-key': randomMeshKey,
  'add-wifi': addWifi,
  'find-address': findAddress,
  'reset-settings': async () => {
    if (await confirmDialog('Default settings', 'Put every setting back to its default? Lights, controllers and Wi-Fi networks are kept.', 'Reset')
        && await attempt(api('api/settings/reset', { method: 'POST', body: {} }), 'Could not reset')) {
      loadSettings();
    }
  },
  'system-reset': systemReset,
};

function bindEvents() {
  document.addEventListener('click', async event => {
    const target = event.target.closest('button, [data-place], .color-bar');
    if (!target) return;
    const d = target.dataset;
    if (d.tab) return showTab(d.tab);
    if (d.goto) return showTab(d.goto);
    if (d.action && ACTIONS[d.action]) return ACTIONS[d.action]();
    if (d.lightAction) return onLightAction(Number(target.closest('[data-light]').dataset.light), d.lightAction, target);
    if (d.allPreset) return setAllLights({ preset: d.allPreset });
    if (d.controllerAction) return onControllerAction(target.closest('[data-controller]').dataset.controller, d.controllerAction);
    if (d.effect) return startEffect(d.effect);
    if (d.stopEffect) return stopEffects(d.stopEffect);
    if (d.sceneAction) return onSceneAction(target.closest('[data-scene]').dataset.scene, d.sceneAction);
    if (d.place) return placeItem(d.place);
    if (d.addLamp) return addUnconfirmed(target);
    if (d.wifiDefault) {
      if (await attempt(api(`api/wifi-networks/${d.wifiDefault}/set-default`, { method: 'POST', body: {} }), 'Could not change')) loadWifi();
      return;
    }
    if (d.wifiDelete) {
      if (await attempt(api(`api/wifi-networks/${d.wifiDelete}`, { method: 'DELETE' }), 'Could not delete')) loadWifi();
      return;
    }
    if (d.useAddress) return useAddress(d.useAddress);
  });

  // Inputs inside the shared dialog that change what it shows.
  $('#dialog-body').addEventListener('change', event => {
    const t = event.target;
    if (t.name === 'network') $('[data-new-wifi]').hidden = t.value !== 'new';
    if (t.name === 'all') $('[data-light-pick]').hidden = t.checked;
    if (t.name === 'type') {
      $('[data-scene-colors]').hidden = t.value === 'effect';
      $('[data-scene-effect]').hidden = t.value !== 'effect';
    }
  });

  $('#effect-lights').addEventListener('change', event => {
    const id = Number(event.target.value);
    if (event.target.checked) state.effectSelection.add(id);
    else state.effectSelection.delete(id);
  });

  $('#stop-effects').addEventListener('click', () => stopEffects());
  $('#theme-toggle').addEventListener('click', toggleTheme);
  $('#pairing-start').addEventListener('click', startPairing);
  $('#pairing-stop').addEventListener('click', stopPairing);
  $('#pairing-controller').addEventListener('change', pollPairing);
  $('#settings-form').addEventListener('submit', saveSettings);
  $('#address-query').addEventListener('keydown', event => {
    if (event.key === 'Enter') {
      event.preventDefault();
      findAddress();
    }
  });
}

async function start() {
  applyTheme(storage('theme'));
  bindEvents();
  const [config, presets, effects, settings] = await Promise.all([
    api('api/config').catch(() => ({})),
    api('api/presets').catch(() => []),
    api('api/effects').catch(() => []),
    api('api/settings').catch(() => ({})),
  ]);
  state.config = config || {};
  state.presets = presets || [];
  state.effects = effects || [];
  state.settings = settings || {};
  renderPresetChips();
  renderEffects();
  await refreshAll();
  const linked = location.hash.slice(1);
  showTab($(`.tabs [data-tab="${linked}"]`) ? linked : storage('tab') || 'lights');
  if (!state.config.mesh_key) {
    toast('Start by importing your BRMesh export (or set a mesh key in Settings).', 'info', 9000);
  }
  // Light states change outside the add-on too; refresh while the page is visible.
  setInterval(() => { if (!document.hidden) refreshLights(); }, 5000);
  setInterval(() => { if (!document.hidden) loadControllers(); }, 30000);
}

document.addEventListener('DOMContentLoaded', start);
