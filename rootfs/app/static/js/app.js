// BRMesh Bridge Frontend Application
let config = {};
let lights = [];
let controllers = [];
let map = null;
let lightMarkers = {};
let controllerMarkers = {};
let selectedLights = new Set();
let currentLightForColor = null;

// Initialize on page load
document.addEventListener('DOMContentLoaded', async () => {
    setupTabs();
    setupModals();
    setupDarkMode();
    await loadConfig();
    
    // Check for firmware updates
    checkFirmwareVersion();
    
    // Check if mesh key is configured, if not, default to settings tab
    if (!config.mesh_key || config.mesh_key.trim() === '') {
        // Switch to settings tab
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        document.querySelector('.tab-btn[data-tab="settings"]').classList.add('active');
        document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));
        document.getElementById('settings-tab').classList.add('active');
        loadSettings();
        loadWiFiNetworks();
        showNotification('⚠️ Import your BRMesh app export (📱 Import from BRMesh App) or enter the mesh key in Settings to get started', 'warning');
    }
    
    await loadLights();
    await loadControllers();
    await loadEffects();
    await loadScenes();
    initMap();
    
    // Setup event listeners
    document.getElementById('import-app-btn').addEventListener('click', openImportModal);
    setupImportModal();
    document.getElementById('refresh-btn').addEventListener('click', refreshAll);
    document.getElementById('save-layout-btn').addEventListener('click', saveLayout);
    document.getElementById('create-scene-btn').addEventListener('click', createScene);
    document.getElementById('create-controller-btn').addEventListener('click', createController);
    document.getElementById('add-controller-btn').addEventListener('click', addExistingController);
    document.getElementById('dark-mode-toggle').addEventListener('click', toggleDarkMode);
    
    // Mesh key visibility toggle
    document.getElementById('toggle-mesh-key').addEventListener('click', () => {
        const input = document.getElementById('mesh-key');
        const button = document.getElementById('toggle-mesh-key');
        if (input.type === 'password') {
            input.type = 'text';
            button.textContent = '🙈';
        } else {
            input.type = 'password';
            button.textContent = '👁️';
        }
    });
    
    // Light states are cheap to refresh; controllers change rarely. Skip while the page is hidden.
    setInterval(() => { if (!document.hidden) loadLights(); }, 5000);
    setInterval(() => { if (!document.hidden) loadControllers(); }, 30000);
});

// Generate random mesh key (8 hex characters)
function generateMeshKey() {
    const hexChars = '0123456789abcdef';
    let meshKey = '';
    for (let i = 0; i < 8; i++) {
        meshKey += hexChars[Math.floor(Math.random() * 16)];
    }
    document.getElementById('mesh-key').value = meshKey;
    showNotification('✅ Generated new mesh key: ' + meshKey, 'success');
}

// Tab Navigation
function setupTabs() {
    const tabBtns = document.querySelectorAll('.tab-btn');
    tabBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            const tab = btn.dataset.tab;
            
            // Update active states
            tabBtns.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            
            document.querySelectorAll('.tab-content').forEach(content => {
                content.classList.remove('active');
            });
            document.getElementById(`${tab}-tab`).classList.add('active');
            
            // Resize map when map tab is activated
            if (tab === 'map' && map) {
                setTimeout(() => map.invalidateSize(), 100);
            }
            
            // Load settings when settings tab is activated
            if (tab === 'settings') {
                loadSettings();
                loadWiFiNetworks();
            }
        });
    });
}

// Modal Management
function setupModals() {
    const modal = document.getElementById('color-picker-modal');
    const closeBtn = modal.querySelector('.close');
    
    closeBtn.addEventListener('click', () => {
        modal.style.display = 'none';
    });
    
    window.addEventListener('click', (event) => {
        if (event.target === modal) {
            modal.style.display = 'none';
        }
    });
    
    const brightnessInput = document.getElementById('brightness-input');
    const brightnessValue = document.getElementById('brightness-value');
    brightnessInput.addEventListener('input', () => {
        brightnessValue.textContent = brightnessInput.value;
    });
    
    document.getElementById('apply-color-btn').addEventListener('click', applyColor);
}

// API Functions
async function loadConfig() {
    try {
        const response = await fetch('api/config');
        config = await response.json();
    } catch (error) {
        console.error('Failed to load config:', error);
    }
}

async function checkFirmwareVersion() {
    try {
        const response = await fetch('api/firmware/check');
        const data = await response.json();
        
        // Update version display in header
        const versionDisplay = document.getElementById('firmware-version-display');
        if (versionDisplay) {
            versionDisplay.textContent = data.current_version;
        }
        
        // Show update banner if update available
        if (data.update_available && !localStorage.getItem('firmware-banner-dismissed')) {
            const banner = document.getElementById('firmware-update-banner');
            const versionInfo = document.getElementById('firmware-version-info');
            const downloadLink = document.getElementById('firmware-download-link');
            
            if (banner && versionInfo && downloadLink) {
                versionInfo.textContent = `v${data.current_version} → v${data.latest_version}`;
                downloadLink.href = data.release_url;
                banner.style.display = 'block';
                
                // Setup dismiss button
                const dismissBtn = document.getElementById('dismiss-firmware-banner');
                if (dismissBtn) {
                    dismissBtn.addEventListener('click', () => {
                        banner.style.display = 'none';
                        localStorage.setItem('firmware-banner-dismissed', 'true');
                    });
                }
            }
        }
    } catch (error) {
        console.error('Failed to check firmware version:', error);
        // Silently fail - not critical
        const versionDisplay = document.getElementById('firmware-version-display');
        if (versionDisplay) {
            versionDisplay.textContent = '0.50.7';
        }
    }
}


async function loadLights() {
    try {
        const response = await fetch('api/lights');
        lights = await response.json();
        // Ensure all lights have valid location objects
        lights = lights.map(light => ({
            ...light,
            location: light.location || { x: null, y: null }
        }));
        renderLights();
        renderLightSelectors();
        updateMapMarkers();
    } catch (error) {
        console.error('Failed to load lights:', error);
    }
}

async function loadControllers() {
    try {
        const response = await fetch('api/controllers');
        controllers = await response.json();
        // Ensure all controllers have valid location objects
        controllers = controllers.map(controller => ({
            ...controller,
            location: controller.location || { x: null, y: null }
        }));
        renderControllers();
        updateMapMarkers();
        initLogViewer(); // Update log controller dropdown
    } catch (error) {
        console.error('Failed to load controllers:', error);
    }
}

async function loadEffects() {
    try {
        const response = await fetch('api/effects');
        const effects = await response.json();
        renderEffects(effects);
    } catch (error) {
        console.error('Failed to load effects:', error);
    }
}

async function loadScenes() {
    try {
        const response = await fetch('api/scenes');
        const scenes = await response.json();
        renderScenes(scenes);
    } catch (error) {
        console.error('Failed to load scenes:', error);
    }
}

// Light Management
function renderLights() {
    const grid = document.getElementById('lights-grid');
    grid.innerHTML = '';
    
    lights.forEach(light => {
        const card = document.createElement('div');
        card.className = `light-card ${light.state.state ? 'active' : ''}`;
        
        const rgb = light.state.rgb || [255, 255, 255];
        const brightness = light.state.brightness || 255;
        const colorHex = rgbToHex(rgb[0], rgb[1], rgb[2]);
        
        card.innerHTML = `
            <div class="light-header">
                <span class="light-name">${escapeHtml(light.name)}</span>
                <span class="light-status ${light.state.state ? 'on' : ''}"></span>
            </div>
            <div class="light-meta">ID ${light.id}${light.kind ? ' · ' + light.kind.toUpperCase() : ''}${light.on_controller === false ? ' · <span class="warn" title="Assign it to a controller (Controllers tab → 💡 Lights)">not on any controller</span>' : (light.state.entity_id ? '' : ' · <span class="warn" title="Install the firmware on a controller and add it to Home Assistant (ESPHome integration)">not in Home Assistant yet</span>')}</div>
            <div class="light-controls">
                <button class="btn btn-primary" onclick="toggleLight(${light.id})">
                    ${light.state.state ? '⚡ Off' : '💡 On'}
                </button>
                <button class="btn btn-secondary" onclick="pickColor(${light.id})">
                    🎨 Color
                </button>
            </div>
            <div class="color-preview" style="background-color: ${colorHex};" 
                 onclick="pickColor(${light.id})"></div>
            <div class="light-actions">
                <button class="btn btn-danger btn-sm" onclick="unpairLight(${light.id}, ${JSON.stringify(light.name).replace(/"/g, '&quot;')})" title="Remove from system">
                    🗑️ Remove
                </button>
            </div>
        `;
        
        grid.appendChild(card);
    });
}

function renderLightSelectors() {
    const container = document.getElementById('light-selector');
    container.innerHTML = '';
    
    lights.forEach(light => {
        const label = document.createElement('label');
        label.innerHTML = `
            <input type="checkbox" value="${light.id}" 
                   ${selectedLights.has(light.id) ? 'checked' : ''}>
            ${light.name}
        `;
        
        label.querySelector('input').addEventListener('change', (e) => {
            if (e.target.checked) {
                selectedLights.add(parseInt(e.target.value));
            } else {
                selectedLights.delete(parseInt(e.target.value));
            }
        });
        
        container.appendChild(label);
    });
}

async function toggleLight(lightId) {
    const light = lights.find(l => l.id === lightId);
    if (!light) return;
    
    try {
        const response = await fetch(`api/lights/${lightId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                state: !light.state.state,
                brightness: light.state.brightness || 255,
                rgb: light.state.rgb || [255, 255, 255]
            })
        });
        if (!response.ok) {
            const result = await response.json();
            showNotification(result.error || 'Could not switch the light', 'error');
        }
        setTimeout(loadLights, 700);
    } catch (error) {
        console.error('Failed to toggle light:', error);
    }
}

function pickColor(lightId) {
    currentLightForColor = lightId;
    const light = lights.find(l => l.id === lightId);
    
    if (light && light.state.rgb) {
        const colorHex = rgbToHex(...light.state.rgb);
        document.getElementById('color-input').value = colorHex;
    }
    
    if (light && light.state.brightness) {
        document.getElementById('brightness-input').value = light.state.brightness;
        document.getElementById('brightness-value').textContent = light.state.brightness;
    }
    
    document.getElementById('color-picker-modal').style.display = 'block';
}

async function applyColor() {
    if (currentLightForColor === null) return;
    
    const colorHex = document.getElementById('color-input').value;
    const brightness = parseInt(document.getElementById('brightness-input').value);
    const rgb = hexToRgb(colorHex);
    
    try {
        await fetch(`api/lights/${currentLightForColor}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                state: true,
                brightness: brightness,
                rgb: [rgb.r, rgb.g, rgb.b]
            })
        });
        
        document.getElementById('color-picker-modal').style.display = 'none';
        await loadLights();
    } catch (error) {
        console.error('Failed to apply color:', error);
    }
}

// Effects Management
function renderEffects(effects) {
    const grid = document.getElementById('effects-grid');
    grid.innerHTML = '';
    
    const effectDescriptions = {
        rainbow: 'Smooth rainbow cycle',
        color_loop: 'Loop through colors',
        twinkle: 'Random twinkling',
        fire: 'Flickering fire effect',
        christmas: 'Red and green alternating',
        halloween: 'Orange and purple spooky',
        strobe: 'Fast strobe effect',
        breathe: 'Gentle breathing'
    };
    
    effects.forEach(effect => {
        const card = document.createElement('div');
        card.className = 'effect-card';
        card.innerHTML = `
            <div class="effect-name">${effect.name.toUpperCase()}</div>
            <div class="effect-description">${effectDescriptions[effect.name]}</div>
        `;
        
        card.addEventListener('click', () => startEffect(effect.name));
        grid.appendChild(card);
    });
}

async function startEffect(effectName) {
    if (selectedLights.size === 0) {
        alert('Please select at least one light');
        return;
    }
    
    const params = {};
    
    // Get effect-specific parameters
    if (effectName === 'strobe' || effectName === 'breathe') {
        const colorHex = prompt('Enter color (hex):', '#ffffff');
        if (colorHex) {
            const rgb = hexToRgb(colorHex);
            params.color = [rgb.r, rgb.g, rgb.b];
        }
    }
    
    try {
        await fetch(`api/effects/${effectName}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                light_ids: Array.from(selectedLights),
                params: params
            })
        });
        
        alert(`Effect ${effectName} started!`);
    } catch (error) {
        console.error('Failed to start effect:', error);
    }
}

async function stopAllEffects() {
    try {
        await fetch('api/effects/stop', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({})
        });
    } catch (error) {
        console.error('Failed to stop effects:', error);
    }
}

// Scenes Management
function renderScenes(scenes) {
    const grid = document.getElementById('scenes-grid');
    grid.innerHTML = '';
    
    scenes.forEach(scene => {
        const card = document.createElement('div');
        card.className = 'scene-card';
        
        let preview = '';
        if (scene.effect) {
            preview = `<div class="scene-preview">Effect: ${scene.effect}</div>`;
        } else if (scene.lights) {
            const colors = scene.lights.slice(0, 5).map(l => {
                const rgb = l.rgb || [255, 255, 255];
                return `<div class="scene-color" style="background: rgb(${rgb.join(',')})"></div>`;
            }).join('');
            preview = `<div class="scene-preview">${colors}</div>`;
        }
        
        card.innerHTML = `
            <div class="scene-name">${scene.name}</div>
            ${preview}
        `;
        
        card.addEventListener('click', () => applyScene(scene.name));
        grid.appendChild(card);
    });
}

async function applyScene(sceneName) {
    try {
        await fetch(`api/scenes/${sceneName}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        
        await loadLights();
    } catch (error) {
        console.error('Failed to apply scene:', error);
    }
}

function createScene() {
    // Create modal for scene creation
    const modal = document.createElement('div');
    modal.className = 'modal';
    modal.style.display = 'block';
    modal.innerHTML = `
        <div class="modal-content">
            <span class="close" onclick="this.parentElement.parentElement.remove()">&times;</span>
            <h2>Create New Scene</h2>
            <div class="form-group">
                <label for="scene-name">Scene Name:</label>
                <input type="text" id="scene-name" placeholder="e.g., Christmas, Movie Night, Party Mode" />
            </div>
            <div class="form-group">
                <label>Scene Type:</label>
                <select id="scene-type">
                    <option value="static">Static Colors</option>
                    <option value="effect">Effect Pattern</option>
                </select>
            </div>
            <div id="scene-static-config" class="form-group">
                <h3>Select Lights & Colors</h3>
                <div id="scene-lights-list"></div>
            </div>
            <div id="scene-effect-config" class="form-group" style="display: none;">
                <label for="scene-effect">Effect:</label>
                <select id="scene-effect">
                    <option value="rainbow">Rainbow</option>
                    <option value="chase">Chase</option>
                    <option value="twinkle">Twinkle</option>
                    <option value="pulse">Pulse</option>
                    <option value="fade">Fade</option>
                </select>
            </div>
            <div class="modal-buttons">
                <button class="btn btn-primary" onclick="saveScene()">💾 Save Scene</button>
                <button class="btn btn-secondary" onclick="this.parentElement.parentElement.parentElement.remove()">Cancel</button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);
    
    // Populate lights list
    const lightsList = document.getElementById('scene-lights-list');
    lights.forEach(light => {
        const lightDiv = document.createElement('div');
        lightDiv.className = 'scene-light-item';
        const currentColor = light.state.rgb || [255, 255, 255];
        const colorHex = rgbToHex(currentColor[0], currentColor[1], currentColor[2]);
        
        lightDiv.innerHTML = `
            <label>
                <input type="checkbox" class="scene-light-checkbox" data-light-id="${light.id}" checked />
                ${light.name}
            </label>
            <input type="color" class="scene-light-color" data-light-id="${light.id}" value="${colorHex}" />
        `;
        lightsList.appendChild(lightDiv);
    });
    
    // Handle scene type change
    document.getElementById('scene-type').addEventListener('change', (e) => {
        const isEffect = e.target.value === 'effect';
        document.getElementById('scene-static-config').style.display = isEffect ? 'none' : 'block';
        document.getElementById('scene-effect-config').style.display = isEffect ? 'block' : 'none';
    });
}

async function saveScene() {
    const name = document.getElementById('scene-name').value.trim();
    if (!name) {
        showNotification('Please enter a scene name', 'error');
        return;
    }
    
    const sceneType = document.getElementById('scene-type').value;
    const sceneData = { name, type: sceneType };
    
    if (sceneType === 'static') {
        sceneData.lights = [];
        document.querySelectorAll('.scene-light-checkbox:checked').forEach(checkbox => {
            const lightId = parseInt(checkbox.dataset.lightId);
            const colorInput = document.querySelector(`.scene-light-color[data-light-id="${lightId}"]`);
            const color = hexToRgb(colorInput.value);
            sceneData.lights.push({ id: lightId, rgb: color, brightness: 255 });
        });
    } else {
        sceneData.effect = document.getElementById('scene-effect').value;
    }
    
    try {
        const response = await fetch('api/scenes', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(sceneData)
        });
        
        if (response.ok) {
            showNotification('Scene created successfully!', 'success');
            document.querySelector('.modal').remove();
            await loadScenes();
        } else {
            showNotification('Failed to create scene', 'error');
        }
    } catch (error) {
        console.error('Failed to create scene:', error);
        showNotification('Failed to create scene: ' + error.message, 'error');
    }
}

// Controllers Management
function renderControllers() {
    const grid = document.getElementById('controllers-grid');
    grid.innerHTML = '';

    controllers.forEach(controller => {
        const card = document.createElement('div');
        card.className = 'controller-card';
        const controllerName = controller.name.toLowerCase().replace(/ /g, '-');
        const job = controller.job;
        let jobBadge = '';
        if (job && job.running) {
            jobBadge = `⏳ ${job.action === 'compile' ? 'Building' : 'Installing'} (${job.elapsed}s)`;
            pollFirmwareJob(controllerName);
        } else if (job) {
            jobBadge = job.success ? `✅ Last ${job.action.replace('_', ' + ')} OK` : `❌ Last ${job.action.replace('_', ' + ')} failed`;
        }
        const online = controller.online ? '🟢 Online' : (controller.in_home_assistant ? '🔴 Offline' : '⚪ Not in Home Assistant yet');

        card.innerHTML = `
            <div class="controller-status">
                <strong>${escapeHtml(controller.name)}</strong>
                <div style="margin-top: 5px; font-size: 0.85em;">
                    <span style="margin-right: 10px;">${controller.config_status === 'configured' ? '✅ Config' : '❌ No Config'}</span>
                    <span style="margin-right: 10px;">${online}</span>
                    <span id="job-${controllerName}">${jobBadge}</span>
                </div>
            </div>
            <div class="controller-info">
                <div>IP: ${controller.ip || 'unknown'}${controller.board ? ' · ' + escapeHtml(controller.board) : ''}</div>
                <div>Lights: ${controller.light_count}${controller.all_lights ? ' (all)' : ' (selected)'}</div>
                ${controller.warning ? `<div class="warn">⚠️ ${escapeHtml(controller.warning)}</div>` : ''}
                ${controller.esphome_path ? `<div>📄 ${escapeHtml(controller.esphome_path)}</div>` : ''}
            </div>
            <div class="controller-actions">
                <button class="btn btn-success btn-sm" onclick="installFirmware('${controllerName}', this)" title="Build and install over Wi-Fi (or USB)">⚡ Install</button>
                <button class="btn btn-primary btn-sm" onclick="buildFirmware('${controllerName}', this)" title="Only build the firmware">🔨 Build</button>
                <button class="btn btn-secondary btn-sm" onclick="editControllerLights(${controller.id})" title="Choose which lights this controller handles">💡 Lights</button>
                <button class="btn btn-secondary btn-sm" onclick="viewLogs('${controllerName}')" title="Build output">📋 Output</button>
                <button class="btn btn-secondary btn-sm" onclick="regenerateYAML('${controllerName}')" title="Regenerate ESPHome YAML">🔄 Regenerate</button>
                <button class="btn btn-secondary btn-sm" onclick="downloadESPHomeConfig('${controllerName}')" title="Download YAML">💾 YAML</button>
                <button class="btn btn-danger btn-sm" onclick="resetController('${controllerName}')" title="Remove this controller">🗑️ Delete</button>
            </div>
        `;
        grid.appendChild(card);
    });
}

async function checkESPHomeStatus(controllerId, controllerName) {
    try {
        const response = await fetch(`api/esphome/status/${controllerName}`);
        const data = await response.json();
        
        // Update IP display
        const ipValue = document.getElementById(`ip-value-${controllerId}`);
        if (ipValue && data.ip) {
            ipValue.textContent = data.ip;
        }
        
        // Update online badge
        const onlineBadge = document.getElementById(`online-${controllerId}`);
        if (onlineBadge) {
            let badgeHTML = data.online ? '🟢 ONLINE' : '🔴 Offline';
            
            // Add version warning if needed
            if (data.needs_update && data.firmware_version) {
                badgeHTML += ` <span style="color: #f39c12;" title="Firmware v${data.firmware_version} (expected v${data.expected_version})">⚠️ Update Available</span>`;
            } else if (data.firmware_version) {
                badgeHTML += ` <span style="color: #27ae60;" title="Running v${data.firmware_version}">v${data.firmware_version}</span>`;
            }
            
            onlineBadge.innerHTML = badgeHTML;
        }
        
        // Show/hide visit button based on online status
        const visitBtn = document.getElementById(`visit-${controllerId}`);
        if (visitBtn && data.online && data.ip) {
            visitBtn.style.display = 'inline-block';
            visitBtn.onclick = () => window.open(`http://${data.ip}`, '_blank');
        }
    } catch (error) {
        console.error('Failed to check ESPHome status:', error);
    }
}

async function checkControllerOnline(controllerId, ip) {
    const badge = document.getElementById('online-' + controllerId);
    if (!badge) return;
    
    if (!ip) {
        badge.innerHTML = '🔴 No IP';
        return;
    }
    
    try {
        // Try to fetch ESPHome API status endpoint with short timeout
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 2000);
        
        const response = await fetch(`http://${ip}/`, { 
            signal: controller.signal,
            mode: 'no-cors' // Avoid CORS issues
        });
        clearTimeout(timeout);
        
        badge.innerHTML = '🟢 Online';
    } catch (error) {
        badge.innerHTML = '🔴 Offline';
    }
}

async function checkFirmwareBuild(controllerId, controllerName) {
    const badge = document.getElementById('built-' + controllerId);
    if (!badge) return;
    
    try {
        // Check if .bin file exists in ESPHome build directory
        const response = await fetch(`api/esphome/build-status/${controllerName}`);
        const data = await response.json();
        
        if (data.built) {
            badge.innerHTML = '✅ Built';
        } else {
            badge.innerHTML = '📦 Ready to Build';
        }
    } catch (error) {
        badge.innerHTML = '📦 Ready to Build';
    }
}

async function editController(controllerId) {
    const controller = controllers.find(c => c.id === controllerId);
    if (!controller) {
        showNotification('Controller not found', 'error');
        return;
    }
    
    const modal = document.createElement('div');
    modal.className = 'modal';
    modal.style.display = 'block';
    modal.innerHTML = `
        <div class="modal-content">
            <span class="close" onclick="this.parentElement.parentElement.remove()">&times;</span>
            <h2>✏️ Edit Controller</h2>
            
            <div class="form-group">
                <label for="edit-controller-name">Name:</label>
                <input type="text" id="edit-controller-name" value="${controller.name}" />
            </div>
            
            <div class="form-group">
                <label for="edit-controller-ip">IP Address:</label>
                <input type="text" id="edit-controller-ip" value="${controller.ip || ''}" placeholder="e.g., 10.1.10.154" />
                <small>Leave blank for auto-discovery via mDNS</small>
            </div>
            
            <div class="form-group">
                <label for="edit-controller-mac">MAC Address:</label>
                <input type="text" id="edit-controller-mac" value="${controller.mac || ''}" placeholder="AA:BB:CC:DD:EE:FF" />
            </div>
            
            <div class="modal-buttons">
                <button class="btn btn-primary" onclick="saveControllerEdit(${controllerId})">💾 Save</button>
                <button class="btn btn-secondary" onclick="this.parentElement.parentElement.parentElement.remove()">Cancel</button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);
}

async function saveControllerEdit(controllerId) {
    const name = document.getElementById('edit-controller-name').value.trim();
    const ip = document.getElementById('edit-controller-ip').value.trim();
    const mac = document.getElementById('edit-controller-mac').value.trim();
    
    if (!name) {
        showNotification('Controller name is required', 'error');
        return;
    }
    
    try {
        const response = await fetch(`api/controllers/${controllerId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, ip: ip || null, mac: mac || null })
        });
        
        const result = await response.json();
        
        if (result.success) {
            showNotification('Controller updated successfully!', 'success');
            document.querySelector('.modal').remove();
            await loadControllers();
        } else {
            showNotification(result.error || 'Failed to update controller', 'error');
        }
    } catch (error) {
        showNotification('Error updating controller: ' + error.message, 'error');
    }
}

async function deleteController(controllerId, controllerName) {
    if (!confirm(`Delete controller "${controllerName}"?\n\nThis will remove the controller from the bridge but will NOT delete the ESPHome config file.`)) {
        return;
    }
    
    try {
        const response = await fetch(`api/controllers/${controllerId}`, {
            method: 'DELETE'
        });
        
        const result = await response.json();
        
        if (result.success) {
            showNotification(`Controller "${controllerName}" deleted`, 'success');
            await loadControllers();
        } else {
            showNotification(result.error || 'Failed to delete controller', 'error');
        }
    } catch (error) {
        showNotification('Error deleting controller: ' + error.message, 'error');
    }
}

async function createController() {
    // Load WiFi networks first
    let wifiNetworks = [];
    try {
        const response = await fetch('api/wifi-networks');
        const data = await response.json();
        // Extract networks array from response
        wifiNetworks = data.networks || [];
    } catch (error) {
        console.error('Failed to load WiFi networks:', error);
        wifiNetworks = [];
    }
    
    // Auto-select if only one network, otherwise show dropdown
    const autoSelectNetwork = wifiNetworks.length === 1;
    
    // Build network selector options using the id from the network object
    const networkOptions = wifiNetworks.map((net) => 
        `<option value="${net.id}" ${autoSelectNetwork ? 'selected' : ''}>${autoSelectNetwork ? '✅ ' : '📶 '}${net.ssid}</option>`
    ).join('');
    
    // Set default selection - first saved network if available, otherwise "new"
    const defaultSelection = wifiNetworks.length > 0 ? wifiNetworks[0].id : 'new';
    const showManualInputs = wifiNetworks.length === 0;
    
    // Create modal for building a new ESP32 from scratch
    const modal = document.createElement('div');
    modal.className = 'modal';
    modal.style.display = 'block';
    modal.innerHTML = `
        <div class="modal-content">
            <span class="close" onclick="this.parentElement.parentElement.remove()">&times;</span>
            <h2>🔨 Create New ESP32 Controller</h2>
            <p>Set up a new ESP32. The add-on writes its ESPHome configuration with all your imported lights;
               afterwards you install it with ⚡ Install on the controller card.</p>
            
            <div class="form-group">
                <label for="wifi-network-selector">WiFi Network: <span style="color: red;">*</span></label>
                <select id="wifi-network-selector" onchange="toggleWiFiInputs()">
                    ${networkOptions}
                    <option value="new">➕ Enter New Network</option>
                </select>
                ${autoSelectNetwork ? '<small style="color: green;">✅ Auto-selected (only one network saved)</small>' : ''}
            </div>
            
            <div id="wifi-manual-inputs" ${showManualInputs ? '' : 'style="display:none;"'}>
                <div class="form-group">
                    <label for="wifi-ssid">WiFi SSID: <span style="color: red;">*</span></label>
                    <input type="text" id="wifi-ssid" placeholder="Your WiFi Network Name" required />
                </div>
                
                <div class="form-group">
                    <label for="wifi-password">WiFi Password: <span style="color: red;">*</span></label>
                    <input type="password" id="wifi-password" placeholder="Your WiFi Password" required />
                    <small>Stored securely in /config/secrets.yaml</small>
                </div>
            </div>
            
            <div class="form-group">
                <label for="controller-name">Controller Name (optional):</label>
                <input type="text" id="controller-name" placeholder="Leave blank to auto-generate (esp-ble-bridge, esp-ble-bridge-1, etc.)" />
                <small>Auto-generated if not specified</small>
            </div>

            <div class="form-group">
                <label for="controller-board">ESP32 board:</label>
                <select id="controller-board">
                    <option value="esp32dev">ESP32 (most dev boards, WROOM/WROVER)</option>
                    <option value="esp32-s3-devkitc-1">ESP32-S3</option>
                    <option value="esp32-c3-devkitm-1">ESP32-C3</option>
                    <option value="esp32-c6-devkitc-1">ESP32-C6</option>
                </select>
                <small>The ESP32-S2 (no Bluetooth) and ESP32-H2 (no Wi-Fi) cannot be used.</small>
            </div>
            
            <div class="info-box">
                <strong>What happens next:</strong>
                <ol>
                    <li>The ESPHome configuration and Wi-Fi secrets are written for you</li>
                    <li>Click ⚡ Install on the new controller card and choose your ESP32: USB on the Home Assistant
                        machine, this computer (browser), or Wi-Fi for an ESP32 that already runs this firmware</li>
                    <li>The first build downloads the ESP32 toolchain (5-15 minutes); later builds are quick</li>
                    <li>Add the ESP32 in Home Assistant under Settings → Devices &amp; Services → ESPHome</li>
                </ol>
                <p><small>💡 Name, IP, and location can be configured later in the Controllers tab.</small></p>
            </div>
            
            <div class="modal-buttons">
                <button class="btn btn-success" onclick="saveController(this)">📝 Generate ESPHome Config</button>
                <button class="btn btn-secondary" onclick="this.parentElement.parentElement.parentElement.remove()">Cancel</button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);
    
    // Show/hide manual inputs initially
    toggleWiFiInputs();
}

function toggleWiFiInputs() {
    const selector = document.getElementById('wifi-network-selector');
    const manualInputs = document.getElementById('wifi-manual-inputs');
    if (selector && manualInputs) {
        manualInputs.style.display = selector.value === 'new' ? 'block' : 'none';
    }
}

async function addExistingController() {
    // Load ESPHome devices first
    let esphomeDevices = [];
    try {
        const response = await fetch('api/esphome/devices');
        const data = await response.json();
        esphomeDevices = data.devices || [];
    } catch (error) {
        console.error('Failed to load ESPHome devices:', error);
        esphomeDevices = [];
    }
    
    // Build device selector options - show all ESPHome devices
    const deviceOptions = esphomeDevices.map((device) => 
        `<option value="${device.name}">🔧 ${device.name}</option>`
    ).join('');
    
    // Create modal for adding an already-configured ESP32
    const modal = document.createElement('div');
    modal.className = 'modal';
    modal.style.display = 'block';
    modal.innerHTML = `
        <div class="modal-content">
            <span class="close" onclick="this.parentElement.parentElement.remove()">&times;</span>
            <h2>➕ Add Existing Controller</h2>
            <p>Import an ESP32 controller from ESPHome or add manually.</p>
            
            ${deviceOptions ? `
            <div class="form-group">
                <label for="esphome-device-selector">Select from ESPHome:</label>
                <select id="esphome-device-selector" onchange="toggleManualInputs()">
                    <option value="">📋 Choose ESPHome Device...</option>
                    ${deviceOptions}
                    <option value="manual">✍️ Enter Manually</option>
                </select>
            </div>` : ''}
            
            <div id="manual-controller-inputs" style="${deviceOptions ? 'display:none;' : ''}">
                <div class="form-group">
                    <label for="controller-name">Controller Name:</label>
                    <input type="text" id="controller-name" placeholder="e.g., esp-ble-bridge" />
                </div>
                
                <div class="form-group">
                    <label for="controller-ip">Controller IP Address:</label>
                    <input type="text" id="controller-ip" placeholder="192.168.1.100" />
                    <small>Leave blank for auto-discovery</small>
                </div>
                
                <div class="form-group">
                    <label for="controller-mac">MAC Address (Optional):</label>
                    <input type="text" id="controller-mac" placeholder="AA:BB:CC:DD:EE:FF" />
                </div>
            </div>
            
            <div class="modal-buttons">
                <button class="btn btn-primary" onclick="saveController(this)">💾 Add Controller</button>
                <button class="btn btn-secondary" onclick="this.parentElement.parentElement.parentElement.remove()">Cancel</button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);
}

function toggleManualInputs() {
    const selector = document.getElementById('esphome-device-selector');
    const manualInputs = document.getElementById('manual-controller-inputs');
    const nameInput = document.getElementById('controller-name');
    
    if (selector && manualInputs) {
        const selectedValue = selector.value;
        
        if (selectedValue === 'manual' || selectedValue === '') {
            manualInputs.style.display = 'block';
            if (nameInput) nameInput.value = '';
        } else {
            manualInputs.style.display = 'none';
            // Pre-fill name from selected ESPHome device
            if (nameInput) nameInput.value = selectedValue;
        }
    }
}

async function saveController(buttonElement) {
    // Check which modal type is open by looking for specific fields
    const networkSelector = document.getElementById('wifi-network-selector');
    const ipField = document.getElementById('controller-ip');
    
    // If network selector exists, this is "Generate ESPHome Config" modal
    if (networkSelector) {
        return await generateESPHomeController(buttonElement);
    }
    
    // Otherwise, this is "Add Existing Controller" modal
    const esphomeSelector = document.getElementById('esphome-device-selector');
    let name = document.getElementById('controller-name')?.value.trim() || '';
    
    // If ESPHome device selected, use that name
    if (esphomeSelector && esphomeSelector.value && esphomeSelector.value !== 'manual') {
        name = esphomeSelector.value;
    }
    
    const ip = ipField ? ipField.value.trim() : '';
    const mac = document.getElementById('controller-mac')?.value.trim() || '';
    const latField = document.getElementById('controller-lat');
    const lonField = document.getElementById('controller-lon');
    const lat = latField ? parseFloat(latField.value) : NaN;
    const lon = lonField ? parseFloat(lonField.value) : NaN;
    
    if (!name) {
        showNotification('Please select a device or enter a controller name', 'error');
        return;
    }
    
    // Default to HA instance location if not specified
    let location;
    if (lat && lon && !isNaN(lat) && !isNaN(lon)) {
        location = { x: lon, y: lat };
    } else if (config.latitude && config.longitude) {
        location = { x: config.longitude, y: config.latitude };
    } else if (config.map_latitude && config.map_longitude) {
        location = { x: config.map_longitude, y: config.map_latitude };
    } else {
        location = { x: 0, y: 0 };
    }
    
    const controllerData = {
        name,
        ip: ip || null,
        mac: mac || null,
        generate_esphome: false,  // Existing controller, no config generation
        location
    };
    
    try {
        // Disable button to prevent double-submission
        if (buttonElement) {
            buttonElement.disabled = true;
            buttonElement.textContent = '⏳ Adding...';
        }
        
        const response = await fetch('api/controllers', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(controllerData)
        });
        
        if (response.ok) {
            showNotification('Controller added successfully!', 'success');
            // Remove all modals to prevent duplicates
            document.querySelectorAll('.modal:not(#import-modal):not(#color-picker-modal)').forEach(m => m.remove());
            await loadControllers();
        } else {
            const error = await response.json();
            showNotification('Failed to add controller: ' + (error.error || 'Unknown error'), 'error');
            if (buttonElement) {
                buttonElement.disabled = false;
                buttonElement.textContent = '💾 Add Controller';
            }
        }
    } catch (error) {
        console.error('Failed to add controller:', error);
        showNotification('Failed to add controller: ' + error.message, 'error');
        if (buttonElement) {
            buttonElement.disabled = false;
            buttonElement.textContent = '💾 Add Controller';
        }
    }
}

async function generateESPHomeController(buttonElement) {
    const networkSelector = document.getElementById('wifi-network-selector');
    const selectedNetwork = networkSelector.value;
    const name = document.getElementById('controller-name').value.trim();
    
    let wifiSsid, wifiPassword, networkId;
    
    if (selectedNetwork === 'new') {
        // Using manual WiFi credentials
        wifiSsid = document.getElementById('wifi-ssid')?.value.trim();
        wifiPassword = document.getElementById('wifi-password')?.value.trim();
        
        // Validate WiFi credentials
        if (!wifiSsid) {
            showNotification('Please enter your WiFi SSID', 'error');
            return;
        }
        
        if (!wifiPassword) {
            showNotification('Please enter your WiFi password', 'error');
            return;
        }
        
        networkId = null;
    } else {
        // Using pre-configured network
        networkId = parseInt(selectedNetwork);
        wifiSsid = null;
        wifiPassword = null;
    }
    
    const boardSelect = document.getElementById('controller-board');
    const controllerData = {
        name: name || null,  // Auto-generate if empty
        board: boardSelect ? boardSelect.value : null,
        wifi_ssid: wifiSsid,
        wifi_password: wifiPassword,
        network_id: networkId,
        generate_esphome: true,  // Generate config for new controller
        location: null  // Will be set later
    };
    
    try {
        // Disable button to prevent double-submission
        if (buttonElement) {
            buttonElement.disabled = true;
            buttonElement.textContent = '⏳ Creating...';
        }
        
        showNotification('📝 Creating controller and generating configuration...', 'info');
        
        const response = await fetch('api/controllers', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(controllerData)
        });
        
        if (response.ok) {
            const result = await response.json();
            showNotification(`✅ Controller "${result.name}" created and config generated!`, 'success');
            // Remove all modals to prevent duplicates
            document.querySelectorAll('.modal:not(#import-modal):not(#color-picker-modal)').forEach(m => m.remove());
            await loadControllers();
            
            // Show build instructions
            if (result.esphome_path) {
                const instructions = `
✅ Config generated: ${result.esphome_path}

🔧 Next: click ⚡ Install on the controller card.
   The first install on a new ESP32 must be over USB (plug it into the Home Assistant machine),
   later updates go over Wi-Fi. You can also install it from the ESPHome dashboard.
   Then add the device in Settings → Devices & Services → ESPHome.
                `.trim();
                
                showNotification(instructions, 'success', 15000);
            }
        } else {
            const error = await response.json();
            showNotification('Failed to create controller: ' + (error.error || 'Unknown error'), 'error');
        }
    } catch (error) {
        console.error('Failed to create controller:', error);
        showNotification('Failed to create controller: ' + error.message, 'error');
    }
}

// Map Functions
function initMap() {
    const lat = config.latitude || config.map_latitude || 0;
    const lon = config.longitude || config.map_longitude || 0;
    const zoom = config.zoom || config.map_zoom || 18;
    
    map = L.map('map').setView([lat, lon], zoom);
    
    // OpenStreetMap standard layer (reliable, no API key required)
    const osmLayer = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19,
        attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    });
    
    // ESRI World Imagery satellite layer
    const satelliteLayer = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
        maxZoom: 19,
        attribution: '© <a href="https://www.esri.com/">Esri</a>, Earthstar Geographics'
    });
    
    // Add OpenStreetMap by default (most reliable)
    osmLayer.addTo(map);
    
    // Layer control to switch between views
    const baseMaps = {
        "Street Map": osmLayer,
        "Satellite": satelliteLayer
    };
    
    L.control.layers(baseMaps).addTo(map);
    
    // Add address search control
    addAddressSearch();
}

function addAddressSearch() {
    const searchControl = L.Control.extend({
        options: {
            position: 'topright'
        },
        
        onAdd: function(map) {
            const container = L.DomUtil.create('div', 'leaflet-bar leaflet-control address-search-control');
            container.style.background = 'white';
            container.style.padding = '10px';
            container.style.borderRadius = '4px';
            container.style.boxShadow = '0 1px 5px rgba(0,0,0,0.4)';
            
            container.innerHTML = `
                <div style="display: flex; flex-direction: column; gap: 8px; min-width: 250px;">
                    <input type="text" id="address-search-input" placeholder="🔍 Search address..." 
                        style="padding: 6px; border: 1px solid #ccc; border-radius: 3px; width: 100%;" />
                    <select id="country-filter" style="padding: 6px; border: 1px solid #ccc; border-radius: 3px;">
                        <option value="">🌍 All Countries</option>
                        <option value="us" selected>🇺🇸 United States</option>
                        <option value="ca">🇨🇦 Canada</option>
                        <option value="gb">🇬🇧 United Kingdom</option>
                        <option value="au">🇦🇺 Australia</option>
                        <option value="de">🇩🇪 Germany</option>
                        <option value="fr">🇫🇷 France</option>
                        <option value="es">🇪🇸 Spain</option>
                        <option value="it">🇮🇹 Italy</option>
                        <option value="jp">🇯🇵 Japan</option>
                        <option value="cn">🇨🇳 China</option>
                    </select>
                    <div id="address-results" style="max-height: 200px; overflow-y: auto; display: none;"></div>
                </div>
            `;
            
            // Prevent map interactions when using the search control
            L.DomEvent.disableClickPropagation(container);
            L.DomEvent.disableScrollPropagation(container);
            
            return container;
        }
    });
    
    map.addControl(new searchControl());
    
    // Add search functionality
    const searchInput = document.getElementById('address-search-input');
    const countryFilter = document.getElementById('country-filter');
    const resultsDiv = document.getElementById('address-results');
    
    let searchTimeout;
    searchInput.addEventListener('input', () => {
        clearTimeout(searchTimeout);
        const query = searchInput.value.trim();
        
        if (query.length < 3) {
            resultsDiv.style.display = 'none';
            return;
        }
        
        searchTimeout = setTimeout(() => searchAddress(query), 500);
    });
}

async function searchAddress(query) {
    const resultsDiv = document.getElementById('address-results');
    const countryFilter = document.getElementById('country-filter').value;
    
    try {
        resultsDiv.innerHTML = '<div style="padding: 8px;">🔄 Searching...</div>';
        resultsDiv.style.display = 'block';
        
        // Build URL with optional country filter
        let url = `https://nominatim.openstreetmap.org/search?format=json&q=${encodeURIComponent(query)}&limit=5&addressdetails=1`;
        if (countryFilter) {
            url += `&countrycodes=${countryFilter}`;
        }
        
        const response = await fetch(url, {
            headers: {
                'User-Agent': 'ESP-BLE-Bridge/1.0'
            }
        });
        
        const results = await response.json();
        
        if (results.length === 0) {
            resultsDiv.innerHTML = '<div style="padding: 8px; color: #666;">No results found</div>';
            return;
        }
        
        resultsDiv.innerHTML = results.map(result => `
            <div class="address-result-item" onclick="selectAddress(${result.lat}, ${result.lon}, '${result.display_name.replace(/'/g, "\\'")}')" 
                style="padding: 8px; cursor: pointer; border-bottom: 1px solid #eee; font-size: 13px;"
                onmouseover="this.style.background='#f0f0f0'" 
                onmouseout="this.style.background='white'">
                📍 ${result.display_name}
            </div>
        `).join('');
        
    } catch (error) {
        console.error('Address search failed:', error);
        resultsDiv.innerHTML = '<div style="padding: 8px; color: red;">Search failed</div>';
    }
}

function selectAddress(lat, lon, address) {
    // Update map center
    map.setView([lat, lon], 18);
    
    // Hide results
    document.getElementById('address-results').style.display = 'none';
    document.getElementById('address-search-input').value = '';
    
    // Add a temporary marker
    const marker = L.marker([lat, lon], {
        icon: L.divIcon({
            className: 'search-marker',
            html: '📍',
            iconSize: [30, 30]
        })
    }).addTo(map);
    
    marker.bindPopup(`<b>Selected Location</b><br>${address}<br><br><button onclick="saveMapCenter(${lat}, ${lon})">💾 Set as Property Center</button>`).openPopup();
    
    // Remove marker after 10 seconds
    setTimeout(() => map.removeLayer(marker), 10000);
}

async function saveMapCenter(lat, lon) {
    config.map_latitude = lat;
    config.map_longitude = lon;
    
    // Update the settings form
    document.getElementById('map-latitude').value = lat;
    document.getElementById('map-longitude').value = lon;
    
    showNotification('📍 Property center updated! Click "Save Settings" to persist.', 'info');
}

function updateMapMarkers() {
    // Check if map is initialized
    if (!map) {
        return;
    }
    
    // Clear existing markers
    Object.values(lightMarkers).forEach(marker => map.removeLayer(marker));
    Object.values(controllerMarkers).forEach(marker => map.removeLayer(marker));
    lightMarkers = {};
    controllerMarkers = {};
    
    // Add light markers
    lights.forEach(light => {
        if (light.location?.x !== null && light.location?.y !== null) {
            const marker = L.marker([light.location.y, light.location.x], {
                draggable: true,
                icon: L.divIcon({
                    className: 'light-marker',
                    html: '💡',
                    iconSize: [30, 30]
                })
            }).addTo(map);
            
            marker.bindPopup(`<b>${light.name}</b><br>ID: ${light.id}`);
            
            marker.on('dragend', async (e) => {
                const pos = e.target.getLatLng();
                await updateLightLocation(light.id, pos.lng, pos.lat);
            });
            
            lightMarkers[light.id] = marker;
        }
    });
    
    // Add controller markers
    if (document.getElementById('show-controllers')?.checked) {
        controllers.forEach(controller => {
            if (controller.location?.x !== null && controller.location?.y !== null) {
                const marker = L.marker([controller.location.y, controller.location.x], {
                    draggable: true,
                    icon: L.divIcon({
                        className: 'controller-marker',
                        html: '📡',
                        iconSize: [40, 40]
                    })
                }).addTo(map);
                
                marker.bindPopup(`<b>${controller.name}</b><br>IP: ${controller.ip}`);
                
                marker.on('dragend', async (e) => {
                    const pos = e.target.getLatLng();
                    await updateControllerLocation(controller.id, pos.lng, pos.lat);
                });
                
                controllerMarkers[controller.name] = marker;
            }
        });
    }
}

async function updateLightLocation(lightId, x, y) {
    try {
        await fetch(`api/lights/${lightId}/location`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ x, y })
        });
    } catch (error) {
        console.error('Failed to update light location:', error);
    }
}

async function updateControllerLocation(controllerId, x, y) {
    try {
        await fetch(`api/controllers/${controllerId}/location`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ x, y })
        });
        showNotification('Controller location updated', 'success');
    } catch (error) {
        console.error('Failed to update controller location:', error);
        showNotification('Failed to update controller location', 'error');
    }
}

async function saveLayout() {
    alert('Layout saved successfully!');
}

// Utility Functions
function rgbToHex(r, g, b) {
    return '#' + [r, g, b].map(x => {
        const hex = Math.round(x).toString(16);
        return hex.length === 1 ? '0' + hex : hex;
    }).join('');
}

function hexToRgb(hex) {
    const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
    return result ? {
        r: parseInt(result[1], 16),
        g: parseInt(result[2], 16),
        b: parseInt(result[3], 16)
    } : { r: 255, g: 255, b: 255 };
}

async function scanForLights() {
    const scanBtn = document.getElementById('scan-btn');
    const originalText = scanBtn.textContent;
    
    try {
        // Show progress
        scanBtn.disabled = true;
        scanBtn.textContent = '🔍 Scanning... (30s)';
        showNotification('Scanning for BRMesh lights... This will take 30 seconds.', 'info');
        
        const response = await fetch('api/scan', { method: 'POST' });
        const result = await response.json();
        
        if (result.error) {
            showNotification(`Scan failed: ${result.error}`, 'error');
        } else {
            showNotification(`Found ${result.count || 0} new lights!`, result.count > 0 ? 'success' : 'info');
            if (result.count > 0) {
                await loadLights();
            }
        }
    } catch (error) {
        console.error('Failed to scan for lights:', error);
        showNotification('Scan failed. Check add-on logs for details.', 'error');
    } finally {
        scanBtn.disabled = false;
        scanBtn.textContent = originalText;
    }
}

async function refreshAll() {
    await loadLights();
    await loadControllers();
}

// Settings Management
async function loadSettings() {
    try {
        const response = await fetch('api/settings');
        const settings = await response.json();
        document.getElementById('mesh-key').value = settings.mesh_key || '';
        document.getElementById('map-enabled').checked = settings.map_enabled !== false;
        document.getElementById('map-latitude').value = settings.latitude || 0;
        document.getElementById('map-longitude').value = settings.longitude || 0;
        document.getElementById('map-zoom').value = settings.zoom || 18;
        updateZoomDisplay();
        document.getElementById('generate-esphome').checked = settings.generate_esphome !== false;
        document.getElementById('include-all-lights').checked = settings.include_all_lights_entity !== false;
        document.getElementById('enable-web-server').checked = !!settings.enable_web_server;
        document.getElementById('adv-duration').value = settings.adv_duration || 40;
        document.getElementById('adv-repeats').value = settings.adv_repeats || 3;
        document.getElementById('enable-nspanel').checked = settings.enable_nspanel || false;
        toggleNSPanelSettings();
        document.getElementById('nspanel-entity').value = settings.nspanel_entity_id || '';
    } catch (error) {
        console.error('Failed to load settings:', error);
        showNotification('Failed to load settings', 'error');
    }
}

async function saveSettings() {
    try {
        const settings = {
            mesh_key: document.getElementById('mesh-key').value.trim(),
            map_enabled: document.getElementById('map-enabled').checked,
            latitude: parseFloat(document.getElementById('map-latitude').value),
            longitude: parseFloat(document.getElementById('map-longitude').value),
            zoom: parseInt(document.getElementById('map-zoom').value),
            generate_esphome: document.getElementById('generate-esphome').checked,
            include_all_lights_entity: document.getElementById('include-all-lights').checked,
            enable_web_server: document.getElementById('enable-web-server').checked,
            adv_duration: parseInt(document.getElementById('adv-duration').value),
            adv_repeats: parseInt(document.getElementById('adv-repeats').value),
            enable_nspanel: document.getElementById('enable-nspanel').checked,
            nspanel_entity_id: document.getElementById('nspanel-entity').value
        };
        const response = await fetch('api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settings)
        });
        const result = await response.json();
        if (response.ok) {
            showNotification(result.message || 'Settings saved', 'success');
            await loadConfig();
        } else {
            showNotification(result.error || 'Failed to save settings', 'error');
        }
    } catch (error) {
        console.error('Failed to save settings:', error);
        showNotification('Failed to save settings', 'error');
    }
}

function toggleMQTTSection() {
    const useAddonMQTT = document.getElementById('use-addon-mqtt').checked;
    const section = document.getElementById('custom-mqtt-section');
    if (section) section.style.display = useAddonMQTT ? 'none' : 'block';
}

function toggleNSPanelSettings() {
    const enableNSPanel = document.getElementById('enable-nspanel').checked;
    document.getElementById('nspanel-settings').style.display = enableNSPanel ? 'block' : 'none';
}

function toggleMapSettings() {
    const mapEnabled = document.getElementById('map-enabled').checked;
    document.getElementById('map-settings-fields').style.display = mapEnabled ? 'block' : 'none';
}

// WiFi Network Management
async function loadWiFiNetworks() {
    try {
        const response = await fetch('api/wifi-networks');
        const data = await response.json();
        const networks = data.networks || [];
        
        const list = document.getElementById('wifi-networks-list');
        if (!networks || networks.length === 0) {
            list.innerHTML = '<p>No WiFi networks configured. Add one to easily reuse credentials when creating controllers.</p>';
            return;
        }
        
        list.innerHTML = networks
            .filter(net => net.id >= 0)  // Skip legacy network ID (-1)
            .map((net) => `
            <div class="wifi-network-item" style="display: flex; justify-content: space-between; align-items: center; padding: 10px; border: 1px solid #ddd; border-radius: 5px; margin-bottom: 10px;">
                <div>
                    <strong>📶 ${net.ssid}</strong>
                    ${net.is_default ? '<span style="background: #4CAF50; color: white; padding: 2px 8px; border-radius: 3px; font-size: 0.8em; margin-left: 8px;">DEFAULT</span>' : ''}
                    <br>
                    <small>Network ID: ${net.id}</small>
                </div>
                <div style="display: flex; gap: 8px;">
                    ${!net.is_default ? `<button class="btn btn-primary btn-sm" onclick="setDefaultWiFiNetwork(${net.id})">⭐ Set Default</button>` : ''}
                    <button class="btn btn-danger btn-sm" onclick="deleteWiFiNetwork(${net.id})">🗑️ Delete</button>
                </div>
            </div>
        `).join('');
    } catch (error) {
        console.error('Failed to load WiFi networks:', error);
        showNotification('Failed to load WiFi networks', 'error');
    }
}

function addWiFiNetworkDialog() {
    const modal = document.createElement('div');
    modal.className = 'modal';
    modal.style.display = 'block';
    modal.innerHTML = `
        <div class="modal-content">
            <span class="close" onclick="this.parentElement.parentElement.remove()">&times;</span>
            <h2>➕ Add WiFi Network</h2>
            <p>Add WiFi credentials to reuse when creating controllers.</p>
            
            <div class="form-group">
                <label for="new-wifi-ssid">WiFi SSID:</label>
                <input type="text" id="new-wifi-ssid" placeholder="Network Name" required />
            </div>
            
            <div class="form-group">
                <label for="new-wifi-password">WiFi Password:</label>
                <input type="password" id="new-wifi-password" placeholder="Password" required />
            </div>
            
            <div class="modal-buttons">
                <button class="btn btn-success" onclick="saveWiFiNetwork()">💾 Save Network</button>
                <button class="btn btn-secondary" onclick="this.parentElement.parentElement.parentElement.remove()">Cancel</button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);
}

async function saveWiFiNetwork() {
    const ssid = document.getElementById('new-wifi-ssid').value.trim();
    const password = document.getElementById('new-wifi-password').value.trim();
    
    if (!ssid || !password) {
        showNotification('Please enter both SSID and password', 'error');
        return;
    }
    
    try {
        const response = await fetch('api/wifi-networks', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ssid, password })
        });
        
        if (response.ok) {
            showNotification('✅ WiFi network saved!', 'success');
            document.querySelector('.modal').remove();
            await loadWiFiNetworks();
        } else {
            const error = await response.json();
            showNotification('Failed to save network: ' + (error.error || 'Unknown error'), 'error');
        }
    } catch (error) {
        console.error('Failed to save WiFi network:', error);
        showNotification('Failed to save WiFi network: ' + error.message, 'error');
    }
}

async function setDefaultWiFiNetwork(index) {
    try {
        const response = await fetch(`api/wifi-networks/${index}/set-default`, {
            method: 'POST'
        });
        
        if (response.ok) {
            showNotification('✅ WiFi network set as default', 'success');
            await loadWiFiNetworks();
        } else {
            const error = await response.json();
            showNotification('Failed to set default: ' + (error.error || 'Unknown error'), 'error');
        }
    } catch (error) {
        console.error('Failed to set default WiFi network:', error);
        showNotification('Failed to set default: ' + error.message, 'error');
    }
}

async function deleteWiFiNetwork(index) {
    if (!confirm('Delete this WiFi network?')) return;
    
    try {
        const response = await fetch(`api/wifi-networks/${index}`, {
            method: 'DELETE'
        });
        
        if (response.ok) {
            showNotification('✅ WiFi network deleted', 'success');
            await loadWiFiNetworks();
        } else {
            const error = await response.json();
            showNotification('Failed to delete network: ' + (error.error || 'Unknown error'), 'error');
        }
    } catch (error) {
        console.error('Failed to delete WiFi network:', error);
        showNotification('Failed to delete WiFi network: ' + error.message, 'error');
    }
}

async function testGitConnectivity() {
    const resultsDiv = document.getElementById('git-test-results');
    resultsDiv.style.display = 'block';
    resultsDiv.textContent = '🔄 Testing git and GitHub connectivity... This may take up to 60 seconds...';
    
    try {
        const response = await fetch('api/diagnostics/git-test');
        const results = await response.json();
        
        let output = '=== Git/GitHub Connectivity Test Results ===\n\n';
        
        output += '1. Git Version:\n' + results.git_version + '\n\n';
        output += '2. Git Configuration:\n' + results.git_config + '\n\n';
        output += '3. DNS Resolution (github.com):\n' + results.dns_github + '\n\n';
        output += '4. Repository Clone Test:\n';
        
        if (typeof results.clone_test === 'string') {
            output += results.clone_test;
        } else {
            output += 'Success: ' + results.clone_test.success + '\n';
            output += 'Return Code: ' + results.clone_test.returncode + '\n';
            output += 'STDOUT:\n' + results.clone_test.stdout + '\n';
            output += 'STDERR:\n' + results.clone_test.stderr;
        }
        
        resultsDiv.textContent = output;
        
        if (typeof results.clone_test === 'object' && results.clone_test.success) {
            showNotification('✅ Git/GitHub connectivity test passed!', 'success');
        } else {
            showNotification('⚠️ Git/GitHub connectivity test failed. Check results below.', 'warning');
        }
    } catch (error) {
        console.error('Failed to run git test:', error);
        resultsDiv.textContent = 'Error running test: ' + error.message;
        showNotification('Failed to run git test: ' + error.message, 'error');
    }
}

function updateZoomDisplay() {
    const zoom = document.getElementById('map-zoom').value;
    document.getElementById('zoom-value').textContent = zoom;
}

async function resetSettings() {
    if (!confirm('Reset all settings to defaults? This will restart the add-on.')) return;
    
    try {
        const response = await fetch('api/settings/reset', { method: 'POST' });
        if (response.ok) {
            showNotification('Settings reset! Restarting...', 'success');
            setTimeout(() => location.reload(), 2000);
        } else {
            throw new Error('Failed to reset settings');
        }
    } catch (error) {
        console.error('Failed to reset settings:', error);
        showNotification('Failed to reset settings', 'error');
    }
}

function importFromApp() { openImportModal(); }
function importFromAppHeader() { openImportModal(); }

// --- Import of the BRMesh app export ---
let importPayload = null;  // {file} or {text} last previewed

function setupImportModal() {
    const drop = document.getElementById('import-drop');
    const input = document.getElementById('import-file');
    if (!drop || !input) return;
    input.addEventListener('change', () => { if (input.files[0]) previewImport(input.files[0]); });
    ['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('drag'); }));
    ['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('drag'); }));
    drop.addEventListener('drop', e => { if (e.dataTransfer.files[0]) previewImport(e.dataTransfer.files[0]); });
}

function openImportModal() {
    importPayload = null;
    importMesh = null;
    document.getElementById('import-file').value = '';
    document.getElementById('import-text').value = '';
    document.getElementById('import-preview').style.display = 'none';
    document.getElementById('import-error').style.display = 'none';
    document.getElementById('import-modal').style.display = 'block';
}

function closeImportModal() {
    document.getElementById('import-modal').style.display = 'none';
}

let importMesh = null;

async function sendImport(apply) {
    const params = new URLSearchParams({
        apply: apply ? '1' : '0',
        mesh: importMesh || '',
        replace: document.getElementById('import-replace').checked ? '1' : '0',
        update_names: document.getElementById('import-names').checked ? '1' : '0'
    });
    let options;
    if (importPayload.file) {
        const form = new FormData();
        form.append('file', importPayload.file);
        options = { method: 'POST', body: form };
    } else {
        options = { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: importPayload.text }) };
    }
    const response = await fetch(`api/import/upload?${params}`, options);
    const result = await response.json();
    if (!response.ok || !result.success) throw new Error(result.error || 'Import failed');
    return result;
}

async function previewImport(file = null) {
    const errorBox = document.getElementById('import-error');
    errorBox.style.display = 'none';
    if (file) {
        importPayload = { file };
        importMesh = null;
    } else if (file === false) {
        // re-preview with another mesh selected
    } else {
        const text = document.getElementById('import-text').value.trim();
        if (!text) return;
        importPayload = { text };
    }
    try {
        const { preview } = await sendImport(false);
        const newCount = preview.lights.filter(l => !l.exists).length;
        let summary = `<p><strong>${preview.lights.length}</strong> lights (${newCount} new)`;
        summary += preview.mesh_key_found ? `, mesh key <code>${preview.mesh_key_masked}</code>` : ', no mesh key in this file';
        summary += '.</p>';
        if (preview.meshes && preview.meshes.length > 1) {
            summary += '<p>This export has more than one mesh (each has its own key). Import: ' +
                preview.meshes.map(m => `<label style="margin-right:12px;"><input type="radio" name="import-mesh" value="${m.id}" ${m.id === preview.mesh_id ? 'checked' : ''} onchange="importMesh=this.value; previewImport(false)"> ${escapeHtml(m.label)} (${m.lights})</label>`).join('') + '</p>';
        }
        if (preview.mesh_key_differs) {
            summary += '<p class="warn">⚠️ The mesh key differs from the current one, so the current lights belong to another mesh. "Replace" is selected; reinstall the controllers afterwards.</p>';
        }
        document.getElementById('import-replace').checked = !!preview.mesh_key_differs;
        if (preview.skipped && preview.skipped.length) {
            summary += `<details><summary class="warn">${preview.skipped.length} device(s) will not be imported</summary><ul>` +
                preview.skipped.map(d => `<li>${d.light_id ?? '-'} · ${escapeHtml(d.name || '')}: ${escapeHtml(d.reason)}</li>`).join('') + '</ul></details>';
        }
        document.getElementById('import-summary').innerHTML = summary;
        document.getElementById('import-table').innerHTML = '<tr><th>ID</th><th>Name</th><th>Type</th><th></th></tr>' +
            preview.lights.map(l => `<tr><td>${l.light_id}</td><td>${escapeHtml(l.name)}</td><td>${escapeHtml(l.type_label || '')}</td><td>${l.exists ? 'existing' : 'new'}</td></tr>`).join('');
        document.getElementById('import-preview').style.display = 'block';
    } catch (error) {
        errorBox.textContent = '❌ ' + error.message;
        errorBox.style.display = 'block';
        document.getElementById('import-preview').style.display = 'none';
    }
}

async function applyImport() {
    if (!importPayload) return;
    const btn = document.getElementById('import-apply-btn');
    btn.disabled = true;
    try {
        const { result, needs_install } = await sendImport(true);
        closeImportModal();
        let message = `✅ Imported: ${result.added.length} new, ${result.updated.length} updated, ${result.total_lights} lights in total.`;
        if (needs_install) message += ' Install the firmware on your controllers (Controllers tab) to apply it.';
        else message += ' Next: add an ESP32 controller in the Controllers tab.';
        showNotification(message, 'success');
        await loadConfig();
        await loadLights();
        await loadControllers();
    } catch (error) {
        const errorBox = document.getElementById('import-error');
        errorBox.textContent = '❌ ' + error.message;
        errorBox.style.display = 'block';
    } finally {
        btn.disabled = false;
    }
}

async function exportConfig() {
    try {
        const response = await fetch('api/settings/export');
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'brmesh_config.json';
        a.click();
        window.URL.revokeObjectURL(url);
        showNotification('Configuration exported', 'success');
    } catch (error) {
        console.error('Failed to export config:', error);
        showNotification('Failed to export configuration', 'error');
    }
}

// Show notification banner
function showNotification(message, type = 'info') {
    // Create notification element if it doesn't exist
    let notification = document.getElementById('notification-banner');
    if (!notification) {
        notification = document.createElement('div');
        notification.id = 'notification-banner';
        notification.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            max-width: 400px;
            padding: 15px 20px;
            border-radius: 8px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.3);
            z-index: 10000;
            font-size: 14px;
            font-weight: 500;
            animation: slideIn 0.3s ease-out;
        `;
        document.body.appendChild(notification);
    }
    
    // Set color based on type
    const colors = {
        success: '#4caf50',
        error: '#f44336',
        warning: '#ff9800',
        info: '#2196f3'
    };
    
    notification.style.backgroundColor = colors[type] || colors.info;
    notification.style.color = 'white';
    notification.textContent = message;
    notification.style.display = 'block';
    
    // Auto-hide after 5 seconds
    setTimeout(() => {
        notification.style.display = 'none';
    }, 5000);
}

// ESP32 Build & Flash Functions
async function downloadESPHomeConfig(controllerName) {
    try {
        const response = await fetch(`api/esphome/download/${controllerName}`);
        
        if (!response.ok) {
            const error = await response.json();
            showNotification(`Failed to download config: ${error.error}`, 'error');
            return;
        }
        
        // Trigger browser download
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${controllerName}.yaml`;
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);
        
        showNotification(`✅ ESPHome config downloaded: ${controllerName}.yaml\n\n� You can also build directly in the addon using the Build button, or build locally with: esphome run ${controllerName}.yaml`, 'success', 10000);
    } catch (error) {
        console.error('Download error:', error);
        showNotification(`Failed to download config: ${error.message}`, 'error');
    }
}

async function editControllerLights(controllerId) {
    const controller = controllers.find(c => c.id === controllerId);
    if (!controller) return;
    const selected = new Set(controller.light_ids || lights.map(l => l.id));
    const modal = document.createElement('div');
    modal.className = 'modal';
    modal.style.display = 'block';
    modal.innerHTML = `
        <div class="modal-content import-modal-content">
            <h2>💡 Lights on ${escapeHtml(controller.name)}</h2>
            <p>Every controller can reach every light through the mesh. With several controllers, give each one
               the lights near it (each light then appears once in Home Assistant), or keep "all lights" for
               the best coverage.</p>
            <label><input type="checkbox" id="assign-all" ${controller.light_ids ? '' : 'checked'}> All lights, including ones imported later</label>
            <div class="import-table-wrap" id="assign-list" style="margin-top: 10px;">
                ${lights.map(l => `<label style="display:block;"><input type="checkbox" class="assign-light" value="${l.id}" ${selected.has(l.id) ? 'checked' : ''}> ${l.id} · ${escapeHtml(l.name)}</label>`).join('')}
            </div>
            <div class="modal-actions">
                <button class="btn btn-secondary" id="assign-cancel">Cancel</button>
                <button class="btn btn-primary" id="assign-save">Save and regenerate</button>
            </div>
        </div>`;
    document.body.appendChild(modal);
    const allBox = modal.querySelector('#assign-all');
    const list = modal.querySelector('#assign-list');
    const sync = () => { list.style.opacity = allBox.checked ? 0.5 : 1; list.querySelectorAll('input').forEach(i => i.disabled = allBox.checked); };
    allBox.addEventListener('change', sync);
    sync();
    modal.querySelector('#assign-cancel').onclick = () => modal.remove();
    modal.querySelector('#assign-save').onclick = async () => {
        const ids = allBox.checked ? null : [...list.querySelectorAll('.assign-light:checked')].map(i => parseInt(i.value));
        const response = await fetch(`api/controllers/${controllerId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ light_ids: ids })
        });
        const result = await response.json();
        if (response.ok) {
            modal.remove();
            showNotification('Saved. Install the firmware on this controller to apply it.', 'success');
            await loadControllers();
            await loadLights();
        } else {
            showNotification(result.error || 'Could not save', 'error');
        }
    };
}

const pollingJobs = new Set();

async function startFirmwareJob(controllerName, endpoint, body, label) {
    const response = await fetch(`api/esphome/${endpoint}/${controllerName}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body || {})
    });
    const result = await response.json();
    if (!response.ok || !result.success) {
        showNotification(`❌ ${result.error || 'Could not start ' + label}`, 'error');
        return false;
    }
    showNotification(`${label} started for ${controllerName}. The first build downloads the toolchain and can take 10+ minutes; you can leave this page.`, 'info');
    pollFirmwareJob(controllerName);
    return true;
}

function pollFirmwareJob(controllerName) {
    if (pollingJobs.has(controllerName)) return;
    pollingJobs.add(controllerName);
    const tick = async () => {
        try {
            const response = await fetch(`api/esphome/job/${controllerName}`);
            if (!response.ok) { pollingJobs.delete(controllerName); return; }
            const job = await response.json();
            const badge = document.getElementById(`job-${controllerName}`);
            if (badge) badge.textContent = job.running ? `⏳ ${job.action === 'compile' ? 'Building' : 'Installing'} (${job.elapsed}s)` : '';
            if (currentLogController === controllerName) renderLogText(job.output);
            if (job.running) { setTimeout(tick, 3000); return; }
            pollingJobs.delete(controllerName);
            showNotification(job.success ? `✅ ${controllerName}: ${job.action.replace('_', ' + ')} finished` :
                                           `❌ ${controllerName}: ${job.error || 'failed'} (see 📋 Output)`, job.success ? 'success' : 'error');
            loadControllers();
        } catch (e) {
            pollingJobs.delete(controllerName);
        }
    };
    tick();
}

async function buildFirmware(controllerName, button = null) {
    await startFirmwareJob(controllerName, 'build', {}, 'Build');
}

const ESP_WEB_TOOLS = 'https://unpkg.com/esp-web-tools@10.2.1/dist/web/install-button.js?module';

function canFlashFromBrowser() {
    // Web Serial only exists in Chrome/Edge, and only on HTTPS (or localhost) pages.
    return window.isSecureContext && 'serial' in navigator;
}

async function waitForJob(controllerName) {
    while (true) {
        await new Promise(r => setTimeout(r, 3000));
        const response = await fetch(`api/esphome/job/${controllerName}`);
        if (!response.ok) return null;
        const job = await response.json();
        if (!job.running) return job;
    }
}

function closeModal(el) {
    const modal = el.closest('.modal');
    if (modal) modal.remove();
}

async function installFirmware(controllerName, button = null) {
    const controller = controllers.find(c => c.name.toLowerCase().replace(/ /g, '-') === controllerName) || {};
    const modal = document.createElement('div');
    modal.className = 'modal';
    modal.style.display = 'block';
    document.body.appendChild(modal);

    const render = async () => {
        let ports = [];
        try {
            ports = (await (await fetch('api/esphome/ports')).json()).ports || [];
        } catch (e) { /* no ports */ }
        const usb = ports.length
            ? ports.map((p, i) => `<label class="install-option"><input type="radio" name="install-target" value="usb" data-port="${escapeHtml(p.path)}" ${i === 0 ? 'checked' : ''}> ${escapeHtml(p.label)}</label>`).join('')
            : '<div class="install-note">No ESP32 found. Plug it into a USB port of the Home Assistant machine and click Refresh.</div>';
        const browser = canFlashFromBrowser()
            ? 'Flash the ESP32 connected to the computer you are using right now (Chrome or Edge).'
            : 'Download the firmware and flash it from this computer with web.esphome.io (this page is not HTTPS, so the browser cannot flash directly).';
        modal.innerHTML = `
            <div class="modal-content import-modal-content">
                <h2>⚡ Install on which ESP32?</h2>
                <p><strong>${escapeHtml(controller.name || controllerName)}</strong> · board ${escapeHtml(controller.board || 'esp32dev')} · ${controller.light_count ?? ''} lights</p>
                <h3>USB on the Home Assistant machine</h3>
                ${usb}
                <h3>This computer</h3>
                <label class="install-option"><input type="radio" name="install-target" value="browser" ${ports.length ? '' : 'checked'}> ${browser}</label>
                <h3>Wi-Fi</h3>
                <label class="install-option"><input type="radio" name="install-target" value="ota"> Over the network (OTA) - only for an ESP32 that already runs ESP BLE Bridge firmware${controller.online ? ' (online)' : ''}.</label>
                <p class="install-note">The first install on a new ESP32 (or one that ran older firmware) must use USB or this computer.</p>
                <div class="modal-actions">
                    <button class="btn btn-secondary" data-act="refresh">🔄 Refresh</button>
                    <button class="btn btn-secondary" data-act="cancel">Cancel</button>
                    <button class="btn btn-primary" data-act="go">Continue</button>
                </div>
            </div>`;
        modal.querySelector('[data-act=refresh]').onclick = render;
        modal.querySelector('[data-act=cancel]').onclick = () => modal.remove();
        modal.querySelector('[data-act=go]').onclick = async () => {
            const choice = modal.querySelector('input[name=install-target]:checked');
            if (!choice) { showNotification('Choose where to install', 'warning'); return; }
            modal.remove();
            if (choice.value === 'browser') {
                await installFromThisComputer(controllerName);
            } else {
                const port = choice.value === 'usb' ? choice.dataset.port : 'OTA';
                await startFirmwareJob(controllerName, 'flash', { port }, 'Install');
            }
        };
    };
    await render();
}

async function installFromThisComputer(controllerName) {
    let status = await (await fetch(`api/esphome/build-status/${controllerName}`)).json();
    if (!status.up_to_date) {
        if (!(status.job && status.job.running)) {
            const started = await startFirmwareJob(controllerName, 'build', {}, 'Build');
            if (!started) return;
        }
        showNotification('Building the firmware first; the installer opens when it is ready.', 'info');
        const job = await waitForJob(controllerName);
        if (!job || !job.success) return;  // pollFirmwareJob already reported the error
    }

    const modal = document.createElement('div');
    modal.className = 'modal';
    modal.style.display = 'block';
    const manifest = `api/esphome/manifest/${controllerName}`;
    const firmware = `api/esphome/firmware/${controllerName}`;
    if (canFlashFromBrowser()) {
        if (!document.querySelector(`script[src="${ESP_WEB_TOOLS}"]`)) {
            const script = document.createElement('script');
            script.type = 'module';
            script.src = ESP_WEB_TOOLS;
            document.head.appendChild(script);
        }
        modal.innerHTML = `
            <div class="modal-content import-modal-content">
                <h2>⚡ Install from this computer</h2>
                <p>Connect the ESP32 to this computer with a USB data cable, click Connect and choose its port.
                   If no port appears, install the USB driver of your board (CP210x or CH340).</p>
                <esp-web-install-button manifest="${manifest}">
                    <button slot="activate" class="btn btn-success">🔌 Connect</button>
                    <span slot="unsupported">This browser cannot flash; use Chrome or Edge.</span>
                </esp-web-install-button>
                <p class="install-note">Or <a href="${firmware}">download the firmware</a> and flash it with web.esphome.io.</p>
                <div class="modal-actions"><button class="btn btn-secondary" onclick="closeModal(this)">Close</button></div>
            </div>`;
    } else {
        modal.innerHTML = `
            <div class="modal-content import-modal-content">
                <h2>⚡ Install from this computer</h2>
                <ol>
                    <li><a class="btn btn-success" href="${firmware}">💾 Download the firmware</a></li>
                    <li>Connect the ESP32 to this computer with a USB data cable.</li>
                    <li>Open <a href="https://web.esphome.io" target="_blank" rel="noopener">web.esphome.io</a> in Chrome or Edge,
                        click <strong>Connect</strong>, choose the ESP32's port, then <strong>Install</strong> and pick the downloaded file.</li>
                </ol>
                <p class="install-note">Flashing directly from this page needs Home Assistant to be opened over HTTPS.</p>
                <div class="modal-actions"><button class="btn btn-secondary" onclick="closeModal(this)">Close</button></div>
            </div>`;
    }
    document.body.appendChild(modal);
}

async function flashFirmware(controllerName, button = null) {
    await installFirmware(controllerName, button);
}

async function buildAndFlash(controllerName, button = null) {
    await installFirmware(controllerName, button);
}

// Dark mode functionality
function setupDarkMode() {
    // Check localStorage for saved preference
    const darkMode = localStorage.getItem('darkMode') === 'true';
    if (darkMode) {
        document.body.classList.add('dark-mode');
        updateDarkModeIcon();
    }
}

function toggleDarkMode() {
    document.body.classList.toggle('dark-mode');
    const isDark = document.body.classList.contains('dark-mode');
    localStorage.setItem('darkMode', isDark);
    updateDarkModeIcon();
}

function updateDarkModeIcon() {
    const btn = document.getElementById('dark-mode-toggle');
    if (btn) {
        btn.textContent = document.body.classList.contains('dark-mode') ? '☀️' : '🌙';
    }
}

// Event listeners for settings
document.addEventListener('DOMContentLoaded', () => {
    const useMqttCheckbox = document.getElementById('use-addon-mqtt');
    const nspanelCheckbox = document.getElementById('enable-nspanel');
    const zoomSlider = document.getElementById('map-zoom');
    const saveBtn = document.getElementById('save-settings-btn');
    const resetBtn = document.getElementById('reset-settings-btn');
    
    if (useMqttCheckbox) useMqttCheckbox.addEventListener('change', toggleMQTTSection);
    if (nspanelCheckbox) nspanelCheckbox.addEventListener('change', toggleNSPanelSettings);
    
    const mapCheckbox = document.getElementById('map-enabled');
    if (mapCheckbox) mapCheckbox.addEventListener('change', toggleMapSettings);
    
    if (zoomSlider) zoomSlider.addEventListener('input', updateZoomDisplay);
    if (saveBtn) saveBtn.addEventListener('click', saveSettings);
    if (resetBtn) resetBtn.addEventListener('click', resetSettings);
});

// Reset functionality
async function factoryResetLight(lightId, lightName) {
    if (!confirm(`⚠️ Factory reset light "${lightName}" (ID: ${lightId})?\n\nThis will:\n- Clear the light's pairing data\n- Return it to pairing mode\n- Require re-pairing with the mesh\n\nPower cycle the light after reset to activate pairing mode.`)) {
        return;
    }
    
    try {
        showNotification(`Resetting light ${lightName}...`, 'info');
        const response = await fetch(`api/lights/${lightId}/reset`, {
            method: 'POST'
        });
        
        const result = await response.json();
        
        if (result.success) {
            showNotification(`✅ ${result.message}`, 'success');
        } else {
            showNotification(`❌ Reset failed: ${result.error}`, 'error');
        }
    } catch (error) {
        console.error('Factory reset error:', error);
        showNotification(`❌ Failed to reset light: ${error.message}`, 'error');
    }
}

async function unpairLight(lightId, lightName) {
    if (!confirm(`🗑️ Remove light "${lightName}" (ID: ${lightId}) from the system?\n\nThis will:\n- Remove the light from Home Assistant\n- Delete it from your configuration\n- Remove it from all ESPHome configs\n\nThe light will remain paired with the mesh. To factory reset it, use the Reset button instead.\n\nThis action cannot be undone.`)) {
        return;
    }
    
    try {
        showNotification(`Removing light ${lightName}...`, 'info');
        const response = await fetch(`api/lights/${lightId}/unpair`, {
            method: 'POST'
        });
        
        const result = await response.json();
        
        if (result.success) {
            showNotification(`✅ ${result.message}`, 'success');
            await loadLights();
        } else {
            showNotification(`❌ Remove failed: ${result.error}`, 'error');
        }
    } catch (error) {
        console.error('Unpair error:', error);
        showNotification(`❌ Failed to remove light: ${error.message}`, 'error');
    }
}

function visitController(controllerName) {
    // Open controller web interface - IP is stored in the visit button's onclick
    // This is just a placeholder, actual URL is set in checkESPHomeStatus
}

function viewLogs(controllerName) {
    // Show the build/install output of this controller in the Logs tab.
    document.querySelector('.tab-btn[data-tab="logs"]').click();
    const select = document.getElementById('log-controller');
    if (select) select.value = controllerName;
    switchLogController();
}

async function regenerateYAML(controllerName) {
    // Check firmware version first
    let firmwareInfo = { current_version: '1.1', latest_version: '1.1', update_available: false };
    try {
        const versionResponse = await fetch('api/firmware/check');
        firmwareInfo = await versionResponse.json();
    } catch (error) {
        console.warn('Could not check firmware version:', error);
    }
    
    // Show confirmation modal with version info
    const updateBadge = firmwareInfo.update_available 
        ? `<span style="background: #FF9800; color: white; padding: 2px 8px; border-radius: 12px; font-size: 0.9em; margin-left: 10px;">🎉 Update Available!</span>`
        : `<span style="background: #4CAF50; color: white; padding: 2px 8px; border-radius: 12px; font-size: 0.9em; margin-left: 10px;">✅ Up to date</span>`;
    
    const modal = document.createElement('div');
    modal.className = 'modal';
    modal.style.display = 'block';
    modal.innerHTML = `
        <div class="modal-content">
            <span class="close" onclick="this.parentElement.parentElement.remove()">&times;</span>
            <h2>🔄 Regenerate Firmware</h2>
            <p>Regenerate ESPHome configuration for <strong>${controllerName}</strong></p>
            
            <div class="info-box" style="background: ${firmwareInfo.update_available ? 'linear-gradient(135deg, #FF9800 0%, #F57C00 100%)' : 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)'}; color: white; margin: 20px 0;">
                <div style="display: flex; align-items: center; justify-content: space-between;">
                    <div>
                        <strong>📦 Current Firmware: ${firmwareInfo.current_version}</strong>
                        ${firmwareInfo.update_available ? `<br><strong>🎉 Latest Available: ${firmwareInfo.latest_version}</strong>` : ''}
                    </div>
                    ${updateBadge}
                </div>
                ${firmwareInfo.update_available ? `
                    <p style="margin: 10px 0 0 0; opacity: 0.9;">
                        A new firmware version is available! 
                        <a href="${firmwareInfo.release_url}" target="_blank" style="color: white; text-decoration: underline;">View Release Notes</a>
                    </p>
                ` : `
                    <p style="margin: 10px 0 0 0; opacity: 0.9;">You're running the latest firmware version</p>
                `}
            </div>
            
            <div class="info-box">
                <strong>What happens:</strong>
                <ul style="margin: 10px 0 0 20px;">
                    <li>ESPHome YAML will be regenerated with current settings</li>
                    ${firmwareInfo.update_available ? '<li>⚠️ <strong>Download the latest firmware config from GitHub to get new features</strong></li>' : '<li>Configuration stays at current version</li>'}
                    <li>You'll need to re-flash your ESP32 for changes to take effect</li>
                </ul>
            </div>
            
            <div class="modal-buttons">
                <button class="btn btn-success" onclick="confirmRegenerateYAML('${controllerName}'); this.parentElement.parentElement.parentElement.remove();">🔄 Regenerate</button>
                <button class="btn btn-secondary" onclick="this.parentElement.parentElement.parentElement.remove()">Cancel</button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);
}

async function confirmRegenerateYAML(controllerName) {
    try {
        showNotification(`Regenerating YAML for ${controllerName}...`, 'info');
        const response = await fetch(`api/esphome/generate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ force: true }) // Force regeneration
        });
        
        const result = await response.json();
        
        if (result.success) {
            // Check result for this specific controller
            const controllerResult = result.results[controllerName];
            if (controllerResult && controllerResult.status === 'manual_override') {
                showNotification(`⚠️ Skipped ${controllerName}: Manual config override active`, 'warning');
            } else {
                showNotification(`✅ YAML regenerated for ${controllerName}`, 'success');
            }
            await loadControllers();
        } else {
            showNotification(`❌ Regeneration failed: ${result.error}`, 'error');
        }
    } catch (error) {
        console.error('Regenerate YAML error:', error);
        showNotification(`❌ Failed to regenerate YAML: ${error.message}`, 'error');
    }
}

async function resetController(controllerName) {
    if (!confirm(`⚠️ Reset controller "${controllerName}"?\n\nThis will:\n- Remove the controller from configuration\n- Delete its ESPHome YAML file\n- Require reconfiguration if you want to use it again\n\nLights will still work with other controllers.\n\nThis action cannot be undone.`)) {
        return;
    }
    
    try {
        showNotification(`Resetting controller ${controllerName}...`, 'info');
        const response = await fetch(`api/controllers/${controllerName}/reset`, {
            method: 'POST'
        });
        
        const result = await response.json();
        
        if (result.success) {
            showNotification(`✅ ${result.message}`, 'success');
            await loadControllers();
        } else {
            showNotification(`❌ Reset failed: ${result.error}`, 'error');
        }
    } catch (error) {
        console.error('Controller reset error:', error);
        showNotification(`❌ Failed to reset controller: ${error.message}`, 'error');
    }
}

async function systemReset() {
    if (!confirm(`🚨 FULL SYSTEM RESET 🚨\n\nThis will permanently remove:\n- All lights\n- All controllers\n- All scenes\n- All effects\n- All ESPHome configurations\n\nYour mesh key and MQTT settings will be preserved.\n\nTHIS CANNOT BE UNDONE!\n\nAre you absolutely sure?`)) {
        return;
    }
    
    // Double confirmation
    const confirmText = prompt(`Type "RESET" (in capital letters) to confirm full system reset:`);
    if (confirmText !== 'RESET') {
        showNotification('❌ System reset cancelled', 'info');
        return;
    }
    
    try {
        showNotification(`⚠️ Performing full system reset...`, 'warning');
        const response = await fetch(`api/system/reset`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ confirm: true })
        });
        
        const result = await response.json();
        
        if (result.success) {
            showNotification(`✅ ${result.message}`, 'success');
            // Reload the entire page after reset
            setTimeout(() => {
                window.location.reload();
            }, 2000);
        } else {
            showNotification(`❌ System reset failed: ${result.error}`, 'error');
        }
    } catch (error) {
        console.error('System reset error:', error);
        showNotification(`❌ Failed to reset system: ${error.message}`, 'error');
    }
}

// ========================================
// Log Viewer Functions
// ========================================

let logStreamActive = false;
let logStreamInterval = null;
let allLogEntries = [];
let currentLogController = null;

async function initLogViewer() {
    // Populate controller dropdown
    const controllerSelect = document.getElementById('log-controller');
    if (!controllerSelect) return;
    
    controllerSelect.innerHTML = '<option value="">Select Controller...</option>';
    controllers.forEach(c => {
        const option = document.createElement('option');
        option.value = c.name.toLowerCase().replace(/ /g, '-');
        option.textContent = c.name;
        controllerSelect.appendChild(option);
    });
    // Keep the current selection; output is only polled once a controller is picked.
    if (currentLogController) controllerSelect.value = currentLogController;
}

function renderLogText(text) {
    const logViewer = document.getElementById('log-viewer');
    if (!logViewer) return;
    const atBottom = logViewer.scrollTop + logViewer.clientHeight >= logViewer.scrollHeight - 20;
    logViewer.textContent = text || '';
    if (atBottom) logViewer.scrollTop = logViewer.scrollHeight;
}

function switchLogController() {
    const controllerSelect = document.getElementById('log-controller');
    const controllerName = controllerSelect?.value;
    
    if (!controllerName) {
        stopLogStream();
        document.getElementById('log-viewer').innerHTML = '<div class="log-placeholder">Select a controller to view logs...</div>';
        return;
    }
    
    currentLogController = controllerName;
    allLogEntries = [];
    document.getElementById('log-viewer').innerHTML = '<div class="log-placeholder">Connecting to log stream...</div>';
    
    startLogStream();
}

function startLogStream() {
    if (logStreamActive || !currentLogController) return;
    
    logStreamActive = true;
    document.querySelector('[onclick="toggleLogStream()"]').textContent = '⏸️ Pause';
    
    // Fetch logs every 2 seconds
    fetchLogs();
    logStreamInterval = setInterval(fetchLogs, 2000);
}

function stopLogStream() {
    logStreamActive = false;
    if (logStreamInterval) {
        clearInterval(logStreamInterval);
        logStreamInterval = null;
    }
    document.querySelector('[onclick="toggleLogStream()"]').textContent = '▶️ Resume';
}

function toggleLogStream() {
    if (logStreamActive) {
        stopLogStream();
    } else {
        startLogStream();
    }
}

async function fetchLogs() {
    if (!currentLogController) return;
    
    try {
        const response = await fetch(`api/esphome/logs/${currentLogController}`);
        if (!response.ok) {
            const logViewer = document.getElementById('log-viewer');
            if (logViewer && response.status === 404) {
                logViewer.textContent = 'Controller offline or not found';
            }
            return;
        }
        
        const data = await response.json();
        
        // Handle raw text logs from ESPHome
        if (data.raw && typeof data.logs === 'string') {
            renderLogText(data.logs);
            // Nothing will change until a build runs; stop polling.
            if (!data.job || !data.job.running) stopLogStream();
            return;
        }
        
        // Handle parsed log entries (legacy format)
        if (data.logs && Array.isArray(data.logs)) {
            allLogEntries.push(...data.logs);
            
            // Keep only last 500 entries
            if (allLogEntries.length > 500) {
                allLogEntries = allLogEntries.slice(-500);
            }
            
            filterLogs();
        }
    } catch (error) {
        console.error('Failed to fetch logs:', error);
        const logViewer = document.getElementById('log-viewer');
        if (logViewer) {
            logViewer.textContent = `Error: ${error.message}`;
        }
    }
}

function filterLogs() {
    const logLevel = document.getElementById('log-level')?.value || 'all';
    const logCategory = document.getElementById('log-category')?.value || 'all';
    const logViewer = document.getElementById('log-viewer');
    
    if (!logViewer || allLogEntries.length === 0) return;
    
    // Map log levels to priorities
    const levelPriority = { 'V': 0, 'D': 1, 'I': 2, 'W': 3, 'E': 4 };
    const minPriority = logLevel === 'all' ? -1 : levelPriority[logLevel];
    
    const filtered = allLogEntries.filter(entry => {
        // Filter by log level
        if (minPriority >= 0 && levelPriority[entry.level] < minPriority) {
            return false;
        }
        
        // Filter by category
        if (logCategory !== 'all' && !entry.tag.includes(logCategory)) {
            return false;
        }
        
        return true;
    });
    
    // Render logs
    if (filtered.length === 0) {
        logViewer.innerHTML = '<div class="log-placeholder">No logs match current filters</div>';
        return;
    }
    
    let html = '';
    filtered.forEach(entry => {
        html += `<div class="log-entry">`;
        html += `<span class="log-timestamp">${entry.timestamp}</span>`;
        html += `<span class="log-level-${entry.level}">[${entry.level}]</span>`;
        html += `<span class="log-tag">[${entry.tag}]</span>`;
        html += `<span class="log-message">${escapeHtml(entry.message)}</span>`;
        html += `</div>`;
    });
    
    logViewer.innerHTML = html;
    
    // Auto-scroll to bottom if not manually scrolled up
    if (logViewer.scrollHeight - logViewer.scrollTop - logViewer.clientHeight < 100) {
        logViewer.scrollTop = logViewer.scrollHeight;
    }
}

function clearLogDisplay() {
    allLogEntries = [];
    const logViewer = document.getElementById('log-viewer');
    if (logViewer) {
        logViewer.innerHTML = '<div class="log-placeholder">Logs cleared. Streaming continues...</div>';
    }
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ============================================
// Pairing Functions
// ============================================

let discoveredDevices = [];

async function scanForPairingDevices() {
    const btn = document.getElementById('scan-pairing-btn');
    const status = document.getElementById('pairing-status');
    const devicesList = document.getElementById('devices-list');
    const discoveredDiv = document.getElementById('discovered-devices');
    
    btn.disabled = true;
    btn.textContent = '📡 Scanning...';
    status.style.display = 'block';
    status.className = 'status-message info';
    status.textContent = 'Initializing BLE scan...';
    
    // Use EventSource for Server-Sent Events (SSE) progress updates
    const eventSource = new EventSource('api/pairing/discover', {
        headers: { 'Accept': 'text/event-stream' }
    });
    
    let scanCompleted = false;
    
    eventSource.addEventListener('status', (event) => {
        const data = JSON.parse(event.data);
        status.className = 'status-message info';
        status.textContent = `📡 ${data.message}`;
        console.log(`Progress: ${data.progress}%`, data.message);
    });
    
    eventSource.addEventListener('complete', (event) => {
        const data = JSON.parse(event.data);
        scanCompleted = true;
        eventSource.close();
        
        discoveredDevices = data.devices;
        
        if (data.devices.length === 0) {
            status.className = 'status-message warning';
            status.textContent = '⚠️ No devices found.';
            discoveredDiv.style.display = 'none';
        } else {
            const pairableCount = data.devices.filter(d => d.pairing_mode && !d.ignored).length;
            status.className = 'status-message success';
            status.textContent = `✅ Found ${data.count} devices (${pairableCount} pairable)`;
            discoveredDiv.style.display = 'block';
            
            // Render devices list
            let html = '';
            
            // Filter out ignored devices unless we want to show them (maybe add a toggle later)
            const visibleDevices = data.devices.filter(d => !d.ignored);
            
            if (visibleDevices.length === 0) {
                html = '<p>No visible devices found (check ignored list).</p>';
            } else {
                // Group by pairable status
                const pairable = visibleDevices.filter(d => d.pairing_mode);
                const others = visibleDevices.filter(d => !d.pairing_mode);
                
                if (pairable.length > 0) {
                    html += '<h4>✨ Ready to Pair</h4>';
                    pairable.forEach((device, index) => {
                        html += renderDeviceCard(device, index, true);
                    });
                }
                
                if (others.length > 0) {
                    html += '<h4 style="margin-top: 20px; border-top: 1px solid #eee; padding-top: 10px;">Other Devices</h4>';
                    others.forEach((device, index) => {
                        html += renderDeviceCard(device, index + pairable.length, false);
                    });
                }
            }
            
            devicesList.innerHTML = html;
            
            // Check if auto-pair is enabled (only for pairable devices)
            const autoPairMode = document.getElementById('auto-pair-mode').checked;
            const pairableDevices = visibleDevices.filter(d => d.pairing_mode);
            if (autoPairMode && pairableDevices.length > 0) {
                status.className = 'status-message info';
                status.textContent = '🤖 Auto-pair enabled: Pairing devices automatically...';
                autoPairDevices(pairableDevices);
            }
        }
        
        btn.disabled = false;
        btn.textContent = '📡 Scan for Devices';
    });
    
    eventSource.addEventListener('error', (event) => {
        scanCompleted = true;
        eventSource.close();
        
        let errorMsg = 'Unknown error';
        try {
            if (event.data) {
                const data = JSON.parse(event.data);
                errorMsg = data.message || errorMsg;
            }
        } catch (e) {
            errorMsg = 'Connection error';
        }
        
        status.className = 'status-message error';
        status.textContent = '❌ Error: ' + errorMsg;
        discoveredDiv.style.display = 'none';
        
        btn.disabled = false;
        btn.textContent = '📡 Scan for Devices';
    });
    
    // Fallback timeout (20 seconds)
    setTimeout(() => {
        if (!scanCompleted) {
            eventSource.close();
            status.className = 'status-message error';
            status.textContent = '❌ Scan timed out';
            btn.disabled = false;
            btn.textContent = '📡 Scan for Devices';
        }
    }, 20000);
}

function renderDeviceCard(device, index, isPairable) {
    const deviceId = `device-${device.mac.replace(/:/g, '')}`;
    const manufacturer = device.manufacturer !== 'Unknown' ? device.manufacturer : 
                        (device.manufacturer_id ? `ID: 0x${device.manufacturer_id.toString(16)}` : 'Unknown');
    
    return `
        <div class="device-card ${isPairable ? 'pairable' : ''}" id="${deviceId}" data-mac="${device.mac}">
            <div class="device-info">
                <strong>${device.name || (isPairable ? 'BRMesh Light' : 'Unknown Device')}</strong>
                <div class="device-details">
                    <span>📍 ${device.mac}</span>
                    <span>📶 ${device.rssi} dBm</span>
                    <span>🏷️ ${manufacturer}</span>
                </div>
            </div>
            <div class="device-actions">
                <button class="btn btn-secondary btn-sm" onclick="showDeviceInfo('${device.mac}')" title="Show Details">
                    ℹ️ Info
                </button>
                <button class="btn btn-secondary btn-sm" onclick="ignoreDevice('${device.mac}', '${deviceId}')" title="Ignore this device">
                    ⛔ Ignore
                </button>
                ${isPairable ? `
                <button class="btn btn-success btn-sm" onclick="pairDevice('${device.mac}', ${index})">
                    🔗 Pair
                </button>` : ''}
            </div>
        </div>
    `;
}

async function ignoreDevice(mac, deviceId) {
    if (!confirm(`Ignore device ${mac}? It will be hidden from future scans.`)) return;

    try {
        const response = await fetch('api/pairing/ignore', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mac })
        });
        
        if (response.ok) {
            const deviceCard = document.getElementById(deviceId);
            if (deviceCard) {
                deviceCard.style.opacity = '0.3';
                deviceCard.style.pointerEvents = 'none';
                
                // Add ignored badge
                const deviceInfo = deviceCard.querySelector('.device-info');
                const badge = document.createElement('span');
                badge.className = 'device-badge ignored';
                badge.textContent = '🚫 Ignored';
                badge.style.color = '#999';
                badge.style.marginLeft = '10px';
                deviceInfo.querySelector('strong').appendChild(badge);
                
                showNotification(`Device ${mac} ignored`, 'info');
            }
        } else {
            showNotification('Failed to ignore device', 'error');
        }
    } catch (error) {
        console.error('Ignore error:', error);
        showNotification('Failed to ignore device: ' + error.message, 'error');
    }
}

function showDeviceInfo(mac) {
    const device = discoveredDevices.find(d => d.mac === mac);
    if (!device) return;
    
    const info = `
        Device Details:
        ---------------
        MAC: ${device.mac}
        Name: ${device.name || 'N/A'}
        RSSI: ${device.rssi} dBm
        Manufacturer: ${device.manufacturer}
        Manufacturer ID: ${device.manufacturer_id ? '0x' + device.manufacturer_id.toString(16) : 'N/A'}
        Pairing Mode: ${device.pairing_mode ? 'Yes' : 'No'}
        Raw Data: ${device.raw_data || 'N/A'}
    `;
    
    alert(info);
}

async function autoPairDevices(devices) {
    const status = document.getElementById('pairing-status');
    
    for (let i = 0; i < devices.length; i++) {
        const device = devices[i];
        status.className = 'status-message info';
        status.textContent = `🤖 Auto-pairing device ${i + 1}/${devices.length}: ${device.mac}...`;
        
        try {
            await pairDevice(device.mac, i);
            // Wait 2 seconds between pairings
            await new Promise(resolve => setTimeout(resolve, 2000));
        } catch (error) {
            console.error(`Auto-pair failed for ${device.mac}:`, error);
            status.className = 'status-message error';
            status.textContent = `❌ Auto-pair failed for ${device.mac}: ${error.message}`;
            // Continue to next device
        }
    }
    
    status.className = 'status-message success';
    status.textContent = `✅ Auto-pairing complete! Paired ${devices.length} device(s)`;
}

async function sendTestCommand(commandType) {
    const testStatus = document.getElementById('test-status');
    const address = parseInt(document.getElementById('pairing-address').value) - 1; // Test on last paired device
    
    if (address < 1) {
        testStatus.style.display = 'block';
        testStatus.className = 'status-message warning';
        testStatus.textContent = '⚠️ No devices paired yet. Pair a device first.';
        return;
    }
    
    testStatus.style.display = 'block';
    testStatus.className = 'status-message info';
    testStatus.textContent = `⏳ Sending ${commandType} command to address ${address}...`;
    
    // Generate payload based on command type
    let payload = '';
    switch (commandType) {
        case 'on':
            payload = '0164ffffff'; // Turn on, brightness 100, white
            break;
        case 'off':
            payload = '0000000000'; // Turn off
            break;
        case 'brightness':
            payload = '0132ffffff'; // Turn on, brightness 50, white
            break;
    }
    
    try {
        const response = await fetch('api/control/send', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                address: address,
                command_type: 1, // Control command
                payload: payload,
                seq: Math.floor(Math.random() * 256)
            })
        });
        
        const result = await response.json();
        
        if (result.success) {
            testStatus.className = 'status-message success';
            testStatus.textContent = `✅ Command sent! Command: ${result.command} (${result.length} bytes)`;
        } else {
            testStatus.className = 'status-message error';
            testStatus.textContent = `❌ Command failed: ${result.error}`;
        }
        
    } catch (error) {
        testStatus.className = 'status-message error';
        testStatus.textContent = '❌ Error sending command: ' + error.message;
    }
}

// Setup pairing event listeners
document.addEventListener('DOMContentLoaded', () => {
    const scanBtn = document.getElementById('scan-pairing-btn');
    if (scanBtn) {
        scanBtn.addEventListener('click', scanForPairingDevices);
    }
    
    const testOnBtn = document.getElementById('test-on-btn');
    if (testOnBtn) {
        testOnBtn.addEventListener('click', () => sendTestCommand('on'));
    }
    
    const testOffBtn = document.getElementById('test-off-btn');
    if (testOffBtn) {
        testOffBtn.addEventListener('click', () => sendTestCommand('off'));
    }
    
    const testBrightnessBtn = document.getElementById('test-brightness-btn');
    if (testBrightnessBtn) {
        testBrightnessBtn.addEventListener('click', () => sendTestCommand('brightness'));
    }
});
