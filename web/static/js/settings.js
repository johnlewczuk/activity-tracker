/**
 * Activity Tracker - Settings Page JavaScript
 * Extracted from settings.html
 */

let config = {};           // Current saved config
let originalConfig = {};   // Copy of original for comparison
let pendingChanges = {};   // Track unsaved changes: { elementId: { section, key, value } }
let requiresRestart = false;

// Settings that require restart
const restartRequiredKeys = new Set([
    'interval_seconds',
    'timeout_seconds',
    'host',
    'port',
    'data_dir'
]);

// Load configuration on page load
async function loadConfig() {
    try {
        const response = await fetch('/api/config');
        config = await response.json();
        originalConfig = JSON.parse(JSON.stringify(config)); // Deep copy
        pendingChanges = {};
        requiresRestart = false;
        updateUI(config);
        updateSaveBar();
        loadStatus();
        loadOllamaModels();  // Load available models from Ollama
    } catch (error) {
        showToast('Failed to load configuration', 'error');
        console.error('Error loading config:', error);
    }
}

// Load available models from Ollama API
async function loadOllamaModels() {
    const select = document.getElementById('model');
    const refreshBtn = document.querySelector('.refresh-btn');

    if (refreshBtn) refreshBtn.classList.add('loading');

    try {
        const response = await fetch('/api/ollama/models');
        const data = await response.json();

        // Clear existing options
        select.innerHTML = '';

        if (data.available && data.models.length > 0) {
            data.models.forEach(model => {
                const option = document.createElement('option');
                option.value = model.name;
                option.textContent = `${model.name} (${model.size})`;
                select.appendChild(option);
            });

            // Set current value from config
            if (config.summarization && config.summarization.model) {
                select.value = config.summarization.model;
                // If current model not in list, add it
                if (!select.value) {
                    const option = document.createElement('option');
                    option.value = config.summarization.model;
                    option.textContent = `${config.summarization.model} (not installed)`;
                    select.insertBefore(option, select.firstChild);
                    select.value = config.summarization.model;
                }
            }
        } else {
            // Ollama not available - show current model only
            const option = document.createElement('option');
            option.value = config.summarization?.model || '';
            option.textContent = config.summarization?.model
                ? `${config.summarization.model} (Ollama unavailable)`
                : 'Ollama unavailable';
            select.appendChild(option);

            if (data.error) {
                console.warn('Ollama models:', data.error);
            }
        }
    } catch (error) {
        console.error('Error loading Ollama models:', error);
        select.innerHTML = '<option value="">Failed to load models</option>';
        if (config.summarization?.model) {
            const option = document.createElement('option');
            option.value = config.summarization.model;
            option.textContent = config.summarization.model;
            select.appendChild(option);
            select.value = config.summarization.model;
        }
    } finally {
        if (refreshBtn) refreshBtn.classList.remove('loading');
    }
}

// Update UI with config values
function updateUI(cfg) {
    // Capture
    setValue('interval_seconds', cfg.capture.interval_seconds);
    setValue('format', cfg.capture.format);
    setValue('quality', cfg.capture.quality);
    setChecked('capture_active_monitor_only', cfg.capture.capture_active_monitor_only);

    // AFK
    setValue('timeout_seconds', cfg.afk.timeout_seconds);
    setValue('min_session_minutes', cfg.afk.min_session_minutes);

    // Summarization - User-facing settings
    setChecked('summarization_enabled', cfg.summarization.enabled);
    setValue('model', cfg.summarization.model);
    setValue('frequency_minutes', cfg.summarization.frequency_minutes || 15);
    setValue('quality_preset', cfg.summarization.quality_preset || 'balanced');

    // Content mode checkboxes
    setChecked('include_focus_context', cfg.summarization.include_focus_context !== false);
    setChecked('include_screenshots', cfg.summarization.include_screenshots !== false);
    setChecked('include_ocr', cfg.summarization.include_ocr !== false);

    // Advanced settings
    setValue('ollama_host', cfg.summarization.ollama_host);
    setChecked('crop_to_window', cfg.summarization.crop_to_window);
    setValue('max_samples', cfg.summarization.max_samples);
    setChecked('include_previous_summary', cfg.summarization.include_previous_summary);
    setChecked('focus_weighted_sampling', cfg.summarization.focus_weighted_sampling);

    // Update computed trigger threshold display
    updateTriggerThresholdDisplay();
    updatePresetBadge();

    // Storage
    setValue('data_dir', cfg.storage.data_dir);
    setValue('max_days_retention', cfg.storage.max_days_retention);
    setValue('max_gb_storage', cfg.storage.max_gb_storage);
    document.getElementById('storage_max').textContent = cfg.storage.max_gb_storage;

    // Privacy
    updateTags('excluded_apps', cfg.privacy.excluded_apps);
}

function setValue(id, value) {
    const el = document.getElementById(id);
    if (!el) return;

    if (el.type === 'range') {
        el.value = value;
        updateSliderValue(id, value);
    } else {
        el.value = value;
    }
}

function setChecked(id, checked) {
    const el = document.getElementById(id);
    if (el) el.checked = checked;
}

function updateSliderValue(id, value) {
    const valueEl = document.getElementById(id + '_value');
    if (valueEl) valueEl.textContent = value;
}

function updateTags(containerId, tags) {
    const container = document.getElementById(containerId + '_container');
    if (!container) return;

    const input = container.querySelector('.tag-input');
    container.innerHTML = '';

    tags.forEach(tag => {
        const tagEl = document.createElement('span');
        tagEl.className = 'tag';
        tagEl.innerHTML = `${escapeHtml(tag)} <span class="tag-remove" data-container="${containerId}" data-tag="${tag}">&times;</span>`;
        container.appendChild(tagEl);
    });

    container.appendChild(input);
}

function removeTag(containerId, tag) {
    if (containerId === 'excluded_apps') {
        // Get current working copy of excluded apps
        let apps = pendingChanges['excluded_apps']?.value
            || [...config.privacy.excluded_apps];
        const index = apps.indexOf(tag);
        if (index > -1) {
            apps.splice(index, 1);
            trackChange('excluded_apps', 'privacy', 'excluded_apps', apps);
            updateTags('excluded_apps', apps);
        }
    }
}

// Load system status
async function loadStatus() {
    try {
        const response = await fetch('/api/status');
        const status = await response.json();

        // Ollama status
        const ollamaStatus = document.getElementById('ollama_status');
        if (status.ollama_available) {
            ollamaStatus.textContent = 'Available';
            ollamaStatus.className = 'status-badge running';
        } else {
            ollamaStatus.textContent = 'Unavailable';
            ollamaStatus.className = 'status-badge stopped';
        }

        // Screenshot count
        document.getElementById('screenshot_count').textContent = status.screenshot_count.toLocaleString();

        // Storage usage
        const used = status.storage_used_gb;
        const max = config.storage.max_gb_storage || 50;
        const percent = max > 0 ? (used / max * 100) : 0;
        document.getElementById('storage_used').textContent = used.toFixed(2);
        document.getElementById('storage_progress').style.width = Math.min(percent, 100) + '%';

        // Monitors
        const monitorList = document.getElementById('monitor_list');
        monitorList.innerHTML = '';
        status.monitors.forEach(monitor => {
            const li = document.createElement('li');
            li.className = 'monitor-item' + (monitor.is_primary ? ' primary' : '');
            li.innerHTML = `
                <svg class="monitor-icon" viewBox="0 0 24 24">
                    <rect x="2" y="3" width="20" height="14" rx="2" ry="2"></rect>
                    <line x1="8" y1="21" x2="16" y2="21"></line>
                    <line x1="12" y1="17" x2="12" y2="21"></line>
                </svg>
                <span>${monitor.name}: ${monitor.width}x${monitor.height}${monitor.is_primary ? ' (primary)' : ''}</span>
            `;
            monitorList.appendChild(li);
        });

    } catch (error) {
        console.error('Error loading status:', error);
    }
}

// Track a pending change (no auto-save)
function trackChange(elementId, section, key, value) {
    pendingChanges[elementId] = { section, key, value };

    // Check if this change requires restart
    if (restartRequiredKeys.has(key)) {
        requiresRestart = true;
    }

    updateSaveBar();
    markInputChanged(elementId, true);
}

// Update save bar and Actions section visibility
function updateSaveBar() {
    const saveBar = document.getElementById('saveBar');
    const unsavedIndicator = document.getElementById('unsavedIndicator');
    const saveBtn = document.getElementById('saveBtn');
    const discardBtn = document.getElementById('discardBtn');
    const saveRestartBtn = document.getElementById('saveRestartBtn');
    const floatingSaveRestartBtn = document.getElementById('floatingSaveRestartBtn');
    const hasChanges = Object.keys(pendingChanges).length > 0;

    if (hasChanges) {
        // Show unsaved indicator in Actions section
        unsavedIndicator.classList.remove('hidden');
        document.body.classList.add('has-unsaved');

        // Enable save/discard buttons
        saveBtn.disabled = false;
        discardBtn.disabled = false;

        // Show Save & Restart button if any pending change requires restart
        if (requiresRestart) {
            saveRestartBtn.classList.add('visible');
            floatingSaveRestartBtn.style.display = 'flex';
        } else {
            saveRestartBtn.classList.remove('visible');
            floatingSaveRestartBtn.style.display = 'none';
        }

        // Floating bar visibility based on scroll position
        updateFloatingBarVisibility();
    } else {
        // Hide unsaved indicator
        unsavedIndicator.classList.add('hidden');
        saveBar.classList.add('hidden');
        document.body.classList.remove('has-unsaved');

        // Disable save/discard buttons
        saveBtn.disabled = true;
        discardBtn.disabled = true;

        // Hide Save & Restart buttons
        saveRestartBtn.classList.remove('visible');
        floatingSaveRestartBtn.style.display = 'none';

        requiresRestart = false;
    }
}

// Show floating bar only when Actions section is scrolled out of view
function updateFloatingBarVisibility() {
    const actionsSection = document.getElementById('actionsSection');
    const saveBar = document.getElementById('saveBar');
    const hasChanges = Object.keys(pendingChanges).length > 0;

    if (!hasChanges) {
        saveBar.classList.add('hidden');
        return;
    }

    const rect = actionsSection.getBoundingClientRect();
    const isActionsVisible = rect.top < window.innerHeight && rect.bottom > 0;

    if (isActionsVisible) {
        saveBar.classList.add('hidden');
    } else {
        saveBar.classList.remove('hidden');
    }
}

// Mark input as changed
function markInputChanged(elementId, changed) {
    const el = document.getElementById(elementId);
    if (!el) return;

    if (el.type === 'checkbox') {
        const slider = el.nextElementSibling;
        if (slider) {
            if (changed) slider.classList.add('changed');
            else slider.classList.remove('changed');
        }
    } else {
        if (changed) el.classList.add('input-changed');
        else el.classList.remove('input-changed');
    }
}

// Save all pending changes
async function saveChanges() {
    if (Object.keys(pendingChanges).length === 0) {
        showToast('No changes to save', 'warning');
        return;
    }

    try {
        // Save each pending change
        for (const [elementId, change] of Object.entries(pendingChanges)) {
            const response = await fetch('/api/config', {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(change)
            });

            const result = await response.json();
            if (result.success) {
                config = result.config;
                markInputChanged(elementId, false);
            }
        }

        originalConfig = JSON.parse(JSON.stringify(config));
        pendingChanges = {};

        if (requiresRestart) {
            showToast('Saved! Restart required for some changes to take effect.', 'warning');
        } else {
            showToast('Settings saved', 'success');
        }

        requiresRestart = false;
        updateSaveBar();

    } catch (error) {
        showToast('Failed to save settings', 'error');
        console.error('Error saving config:', error);
    }
}

// Save and restart
async function saveAndRestart() {
    if (Object.keys(pendingChanges).length === 0) {
        // No changes, just restart
        await restartService();
        return;
    }

    try {
        // Save all changes first
        for (const [elementId, change] of Object.entries(pendingChanges)) {
            await fetch('/api/config', {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(change)
            });
        }

        showToast('Settings saved. Restarting service...', 'success');
        pendingChanges = {};
        requiresRestart = false;
        updateSaveBar();

        // Now restart
        await restartService();

    } catch (error) {
        showToast('Failed to save and restart', 'error');
        console.error('Error:', error);
    }
}

// Restart the service
async function restartService() {
    try {
        showToast('Restarting service...', 'warning');

        const response = await fetch('/api/restart', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });

        const result = await response.json();

        if (result.success) {
            showToast('Service restarting. Page will reload...', 'success');

            // Wait for service to restart, then reload
            setTimeout(() => {
                window.location.reload();
            }, 3000);
        } else {
            showToast('Failed to restart: ' + result.error, 'error');
        }

    } catch (error) {
        showToast('Failed to restart service', 'error');
        console.error('Error restarting:', error);
    }
}

// Discard pending changes
function discardChanges() {
    // Reset UI to original values
    updateUI(originalConfig);

    // Clear all changed indicators
    for (const elementId of Object.keys(pendingChanges)) {
        markInputChanged(elementId, false);
    }

    pendingChanges = {};
    requiresRestart = false;
    updateSaveBar();

    showToast('Changes discarded', 'success');
}

// Reset configuration
async function resetConfig() {
    if (!confirm('Reset all settings to defaults?')) return;

    try {
        const response = await fetch('/api/config/reset', { method: 'POST' });
        const result = await response.json();

        if (result.success) {
            config = result.config;
            updateUI(config);
            showToast('Reset to defaults', 'success');
        }
    } catch (error) {
        showToast('Failed to reset', 'error');
        console.error('Error resetting config:', error);
    }
}

// Export configuration
function exportConfig() {
    const json = JSON.stringify(config, null, 2);
    const blob = new Blob([json], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'activity-tracker-config.json';
    a.click();
    URL.revokeObjectURL(url);
    showToast('Configuration exported', 'success');
}

// Show toast notification (use utils.js version if available, fallback otherwise)
function showToast(message, type = 'success') {
    const toast = document.getElementById('toast');
    toast.textContent = message;
    toast.className = `toast ${type} show`;

    setTimeout(() => {
        toast.classList.remove('show');
    }, 3000);
}

// Quality preset definitions
const qualityPresets = {
    quick: { max_samples: 5, include_previous_summary: false, focus_weighted_sampling: false },
    balanced: { max_samples: 10, include_previous_summary: true, focus_weighted_sampling: true },
    thorough: { max_samples: 15, include_previous_summary: true, focus_weighted_sampling: true }
};

// Apply quality preset to underlying settings
function applyQualityPreset(preset) {
    const settings = qualityPresets[preset];
    if (!settings) return;

    // Update UI
    setValue('max_samples', settings.max_samples);
    setChecked('include_previous_summary', settings.include_previous_summary);
    setChecked('focus_weighted_sampling', settings.focus_weighted_sampling);

    // Track changes
    trackChange('max_samples', 'summarization', 'max_samples', settings.max_samples);
    trackChange('include_previous_summary', 'summarization', 'include_previous_summary', settings.include_previous_summary);
    trackChange('focus_weighted_sampling', 'summarization', 'focus_weighted_sampling', settings.focus_weighted_sampling);

    updatePresetBadge();
    updatePromptTemplate();  // Preset affects include_previous_summary
}

// Update the preset badge display
function updatePresetBadge() {
    const preset = document.getElementById('quality_preset')?.value || 'balanced';
    const badge = document.getElementById('currentPresetBadge');
    if (badge) badge.textContent = preset;
}

// Update computed trigger threshold display (display only, no tracking)
function updateTriggerThresholdDisplay() {
    const frequency = parseInt(document.getElementById('frequency_minutes')?.value || 15);
    const captureInterval = config?.capture?.interval_seconds || 30;
    const threshold = Math.round(frequency * 60 / captureInterval);
    const display = document.getElementById('trigger_threshold_display');
    if (display) display.textContent = `${threshold} screenshots`;
    return threshold;  // Return for use when tracking is needed
}

// Toggle advanced settings visibility
function toggleAdvancedSettings() {
    const container = document.getElementById('advancedSettings');
    const arrow = document.getElementById('advancedArrow');
    container.classList.toggle('collapsed');
    arrow.classList.toggle('expanded');
}

// Toggle prompt preview visibility
function togglePromptPreview() {
    const container = document.getElementById('promptPreview');
    const toggleText = document.getElementById('promptToggleText');
    const isCollapsed = container.classList.toggle('collapsed');
    toggleText.textContent = isCollapsed ? 'Show' : 'Hide';

    // Update prompt template when shown
    if (!isCollapsed) {
        updatePromptTemplate();
    }
}

// Build prompt template dynamically based on selected content options
function updatePromptTemplate() {
    const includeFocus = document.getElementById('include_focus_context')?.checked;
    const includeScreenshots = document.getElementById('include_screenshots')?.checked;
    const includeOcr = document.getElementById('include_ocr')?.checked;
    const includePrevious = document.getElementById('include_previous_summary')?.checked;

    let parts = ['You are summarizing a developer\'s work activity.', ''];

    if (includePrevious) {
        parts.push('[Previous context: {previous_summary}]');
        parts.push('');
    }

    if (includeFocus) {
        parts.push('## Time Breakdown (from focus tracking)');
        parts.push('- VS Code (daemon.py): 45m 23s [longest: 18m]');
        parts.push('- Firefox (GitHub PR #1234): 12m 5s [3 visits]');
        parts.push('- Terminal: 8m 12s [5 visits]');
        parts.push('');
        parts.push('Total tracked: 65m, 12 context switches');
        parts.push('');
    }

    if (includeOcr) {
        parts.push('## Window Content (OCR)');
        parts.push('{ocr_section}');
        parts.push('');
    }

    if (includeScreenshots) {
        parts.push('## Screenshots');
        parts.push('{num_screenshots} screenshots attached showing actual screen content.');
        parts.push('');
    }

    // Build the "based on" clause dynamically
    let sources = [];
    if (includeFocus) sources.push('the time breakdown');
    if (includeOcr) sources.push('OCR text');
    if (includeScreenshots) sources.push('screenshots');

    const basis = sources.length > 0 ? sources.join(', ') : 'the available data';

    parts.push(`Based on ${basis}, write ONE sentence (max 25 words) describing the PRIMARY activity.`);
    parts.push('');
    parts.push('IMPORTANT: Output ONLY the summary sentence. No explanation, no reasoning, no preamble.');
    parts.push('');
    parts.push('Guidelines:');
    parts.push('- Focus on where the most time was spent');
    parts.push('- Use specific project names, filenames, or URLs visible in the content');
    parts.push('- Format: "[Action verb] [what] in/for [project/context]"');
    parts.push('- If multiple distinct activities, mention the dominant one');
    parts.push('- Do NOT assume different apps/windows are related unless clearly the same project');
    parts.push('');
    parts.push('Good examples:');
    parts.push('Implementing window focus tracking in activity-tracker daemon.py (45 min)');
    parts.push('Reviewing PR #1234 for authentication service and responding to comments');
    parts.push('Debugging API endpoint issues in Acusight backend with Docker logs');
    parts.push('');
    parts.push('Be specific. Avoid generic descriptions like "coding" or "browsing".');

    const template = parts.join('\n');
    document.getElementById('promptTemplateText').textContent = template;

    // Add visual indicator for disabled sections
    const preEl = document.getElementById('promptTemplateText');
    if (!includeFocus && !includeScreenshots && !includeOcr) {
        preEl.style.color = 'var(--danger)';
        preEl.textContent = '⚠️ Warning: No content selected! At least one option must be enabled.\n\n' + template;
    } else {
        preEl.style.color = '';
    }
}

// Event listeners for all inputs
document.addEventListener('DOMContentLoaded', () => {
    loadConfig();

    // Listen for scroll to update floating bar visibility
    window.addEventListener('scroll', updateFloatingBarVisibility);

    // Range inputs
    ['interval_seconds', 'quality', 'timeout_seconds', 'min_session_minutes',
     'max_days_retention', 'max_gb_storage', 'max_samples'].forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            el.addEventListener('input', (e) => {
                updateSliderValue(id, e.target.value);
                const { section, key } = getConfigInfo(id);
                const value = id === 'max_gb_storage'
                    ? parseFloat(e.target.value)
                    : parseInt(e.target.value);
                trackChange(id, section, key, value);
            });
        }
    });

    // Checkboxes
    const promptAffectingCheckboxes = ['include_focus_context', 'include_screenshots', 'include_ocr', 'include_previous_summary'];

    ['capture_active_monitor_only', 'summarization_enabled', 'include_previous_summary',
     'crop_to_window', 'focus_weighted_sampling',
     'include_focus_context', 'include_screenshots', 'include_ocr'].forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            el.addEventListener('change', (e) => {
                const { section, key } = getConfigInfo(id);
                trackChange(id, section, key, e.target.checked);

                // Update prompt template preview if this checkbox affects it
                if (promptAffectingCheckboxes.includes(id)) {
                    updatePromptTemplate();
                }
            });
        }
    });

    // Select inputs (basic)
    ['format', 'model'].forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            el.addEventListener('change', (e) => {
                const { section, key } = getConfigInfo(id);
                trackChange(id, section, key, e.target.value);
            });
        }
    });

    // Frequency dropdown - compute trigger threshold
    const frequencyEl = document.getElementById('frequency_minutes');
    if (frequencyEl) {
        frequencyEl.addEventListener('change', (e) => {
            trackChange('frequency_minutes', 'summarization', 'frequency_minutes', parseInt(e.target.value));
            const threshold = updateTriggerThresholdDisplay();
            trackChange('trigger_threshold', 'summarization', 'trigger_threshold', threshold);
        });
    }

    // Quality preset dropdown - apply preset settings
    const qualityEl = document.getElementById('quality_preset');
    if (qualityEl) {
        qualityEl.addEventListener('change', (e) => {
            trackChange('quality_preset', 'summarization', 'quality_preset', e.target.value);
            applyQualityPreset(e.target.value);
        });
    }

    // Text inputs
    const ollamaHostInput = document.getElementById('ollama_host');
    if (ollamaHostInput) {
        ollamaHostInput.addEventListener('change', (e) => {
            trackChange('ollama_host', 'summarization', 'ollama_host', e.target.value);
        });
    }

    // Tag input - track changes for excluded apps
    const tagInput = document.getElementById('excluded_apps_input');
    if (tagInput) {
        tagInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && e.target.value.trim()) {
                const value = e.target.value.trim();
                // Create a working copy of excluded apps
                let apps = pendingChanges['excluded_apps']?.value
                    || [...config.privacy.excluded_apps];
                if (!apps.includes(value)) {
                    apps.push(value);
                    trackChange('excluded_apps', 'privacy', 'excluded_apps', apps);
                    updateTags('excluded_apps', apps);
                }
                e.target.value = '';
                e.preventDefault();
            }
        });
    }
});

// Mapping from element IDs to config {section, key}
const configMapping = {
    // Capture
    interval_seconds: { section: 'capture', key: 'interval_seconds' },
    format: { section: 'capture', key: 'format' },
    quality: { section: 'capture', key: 'quality' },
    capture_active_monitor_only: { section: 'capture', key: 'capture_active_monitor_only' },
    // AFK
    timeout_seconds: { section: 'afk', key: 'timeout_seconds' },
    min_session_minutes: { section: 'afk', key: 'min_session_minutes' },
    // Summarization - User-facing settings
    summarization_enabled: { section: 'summarization', key: 'enabled' },
    model: { section: 'summarization', key: 'model' },
    frequency_minutes: { section: 'summarization', key: 'frequency_minutes' },
    quality_preset: { section: 'summarization', key: 'quality_preset' },
    // Content mode (multi-select)
    include_focus_context: { section: 'summarization', key: 'include_focus_context' },
    include_screenshots: { section: 'summarization', key: 'include_screenshots' },
    include_ocr: { section: 'summarization', key: 'include_ocr' },
    // Advanced settings
    ollama_host: { section: 'summarization', key: 'ollama_host' },
    crop_to_window: { section: 'summarization', key: 'crop_to_window' },
    trigger_threshold: { section: 'summarization', key: 'trigger_threshold' },
    max_samples: { section: 'summarization', key: 'max_samples' },
    include_previous_summary: { section: 'summarization', key: 'include_previous_summary' },
    focus_weighted_sampling: { section: 'summarization', key: 'focus_weighted_sampling' },
    sample_interval_minutes: { section: 'summarization', key: 'sample_interval_minutes' },
    // Storage
    data_dir: { section: 'storage', key: 'data_dir' },
    max_days_retention: { section: 'storage', key: 'max_days_retention' },
    max_gb_storage: { section: 'storage', key: 'max_gb_storage' },
    // Privacy
    excluded_apps: { section: 'privacy', key: 'excluded_apps' },
    excluded_titles: { section: 'privacy', key: 'excluded_titles' },
    blur_screenshots: { section: 'privacy', key: 'blur_screenshots' },
    // Web
    host: { section: 'web', key: 'host' },
    port: { section: 'web', key: 'port' },
};

function getConfigInfo(elementId) {
    return configMapping[elementId] || { section: 'capture', key: elementId };
}

// ==================== Tag Management Functions ====================

let tagConsolidationData = [];  // Store suggested consolidations
let allTagsData = [];  // Store all tags for cloud
let selectedTags = new Set();  // Currently selected tags for manual merge

// Load tags and render cloud
async function loadTagCloud() {
    const cloud = document.getElementById('tagCloud');
    try {
        const response = await fetch('/api/tags');
        const data = await response.json();
        allTagsData = data.tags || [];

        document.getElementById('uniqueTagCount').textContent = data.total_unique || allTagsData.length;

        // Render tag cloud
        cloud.innerHTML = '';
        if (allTagsData.length === 0) {
            cloud.innerHTML = '<span class="loading-text">No tags found</span>';
            return;
        }

        allTagsData.forEach(t => {
            const tag = document.createElement('span');
            tag.className = 'tag-cloud-item';
            tag.dataset.tag = t.tag;
            tag.innerHTML = `${escapeHtml(t.tag)} <span class="tag-count">(${t.count})</span>`;
            tag.onclick = () => toggleTagSelection(t.tag, tag);
            cloud.appendChild(tag);
        });
    } catch (error) {
        console.error('Error loading tags:', error);
        cloud.innerHTML = '<span class="loading-text">Error loading tags</span>';
    }
}

// Toggle tag selection for manual merge
function toggleTagSelection(tagName, element) {
    if (selectedTags.has(tagName)) {
        selectedTags.delete(tagName);
        element.classList.remove('selected');
    } else {
        selectedTags.add(tagName);
        element.classList.add('selected');
    }
    updateMergeControls();
}

// Update merge controls visibility and state
function updateMergeControls() {
    const controls = document.getElementById('mergeControls');
    const countEl = document.getElementById('selectedTagsCount');
    const input = document.getElementById('canonicalTagInput');

    countEl.textContent = selectedTags.size;

    if (selectedTags.size >= 2) {
        controls.style.display = 'block';
        // Auto-suggest canonical name (most common tag or first selected)
        if (!input.value) {
            const tags = Array.from(selectedTags);
            const mostCommon = tags.reduce((a, b) => {
                const countA = allTagsData.find(t => t.tag === a)?.count || 0;
                const countB = allTagsData.find(t => t.tag === b)?.count || 0;
                return countA >= countB ? a : b;
            });
            input.value = mostCommon.toLowerCase().replace(/\s+/g, '-');
        }
    } else {
        controls.style.display = 'none';
    }
}

// Clear tag selection
function clearTagSelection() {
    selectedTags.clear();
    document.querySelectorAll('.tag-cloud-item.selected').forEach(el => el.classList.remove('selected'));
    document.getElementById('canonicalTagInput').value = '';
    updateMergeControls();
}

// Filter tag cloud by search input
function filterTagCloud() {
    const filter = document.getElementById('tagSearchInput').value.toLowerCase();
    document.querySelectorAll('.tag-cloud-item').forEach(el => {
        const tag = el.dataset.tag.toLowerCase();
        el.classList.toggle('hidden', filter && !tag.includes(filter));
    });
}

// Merge selected tags manually
async function mergeSelectedTags() {
    const canonical = document.getElementById('canonicalTagInput').value.trim();
    if (!canonical) {
        showToast('Please enter a canonical tag name', 'error');
        return;
    }
    if (selectedTags.size < 2) {
        showToast('Select at least 2 tags to merge', 'error');
        return;
    }

    const variants = Array.from(selectedTags);
    const btn = document.getElementById('mergeTagsBtn');
    btn.disabled = true;

    try {
        const response = await fetch('/api/tags/consolidate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                consolidations: [{ canonical, variants }]
            })
        });

        const data = await response.json();
        if (data.error) throw new Error(data.error);

        showToast(`Merged ${variants.length} tags into "${canonical}", updated ${data.updated_summaries} summaries`, 'success');
        clearTagSelection();
        loadTagCloud();  // Refresh cloud

    } catch (error) {
        showToast(`Error: ${error.message}`, 'error');
    } finally {
        btn.disabled = false;
    }
}

// Analyze tags for auto-detected duplicates
async function analyzeTagsForConsolidation() {
    const btn = document.getElementById('analyzeTagsBtn');
    const resultsDiv = document.getElementById('tagConsolidationResults');

    btn.disabled = true;
    resultsDiv.style.display = 'none';

    try {
        const response = await fetch('/api/tags/suggest-consolidation', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ min_count: 1 })
        });

        const data = await response.json();
        if (data.error) throw new Error(data.error);

        tagConsolidationData = data.consolidations || [];

        if (tagConsolidationData.length === 0) {
            showToast('No duplicate tags found', 'success');
            return;
        }

        renderConsolidationSuggestions(tagConsolidationData);
        resultsDiv.style.display = 'block';

    } catch (error) {
        showToast(`Error: ${error.message}`, 'error');
    } finally {
        btn.disabled = false;
    }
}

// Render consolidation suggestions
function renderConsolidationSuggestions(consolidations) {
    const list = document.getElementById('consolidationList');
    list.innerHTML = '';

    consolidations.forEach((group, index) => {
        const item = document.createElement('div');
        item.className = 'consolidation-item';
        item.dataset.index = index;

        const variantsHtml = group.variants.map(v =>
            `<span class="variant-tag ${v === group.canonical ? 'canonical' : ''}">${escapeHtml(v)}</span>`
        ).join('');

        item.innerHTML = `
            <input type="checkbox" class="consolidation-checkbox" data-index="${index}" onchange="updateConsolidationSelection()">
            <div class="consolidation-content">
                <div class="consolidation-canonical">
                    <span class="arrow">→</span> ${escapeHtml(group.canonical)}
                </div>
                <div class="consolidation-variants">
                    ${variantsHtml}
                </div>
                <div class="consolidation-count">
                    ${group.total_count || 0} occurrences across ${group.variants.length} variants
                </div>
            </div>
        `;

        // Click on item to toggle checkbox
        item.addEventListener('click', (e) => {
            if (e.target.type !== 'checkbox') {
                const checkbox = item.querySelector('.consolidation-checkbox');
                checkbox.checked = !checkbox.checked;
                updateConsolidationSelection();
            }
        });

        list.appendChild(item);
    });
}

// Toggle all consolidation checkboxes
function toggleAllConsolidations() {
    const selectAll = document.getElementById('selectAllConsolidations').checked;
    document.querySelectorAll('.consolidation-checkbox').forEach(cb => {
        cb.checked = selectAll;
    });
    updateConsolidationSelection();
}

// Update selection state and enable/disable apply button
function updateConsolidationSelection() {
    const checkboxes = document.querySelectorAll('.consolidation-checkbox');
    const applyBtn = document.getElementById('applyConsolidationsBtn');
    const selectedCount = Array.from(checkboxes).filter(cb => cb.checked).length;

    applyBtn.disabled = selectedCount === 0;
    applyBtn.textContent = selectedCount > 0 ? `Apply Selected (${selectedCount})` : 'Apply Selected';

    // Update item selected state
    checkboxes.forEach(cb => {
        const item = cb.closest('.consolidation-item');
        item.classList.toggle('selected', cb.checked);
    });

    // Update select all checkbox state
    const selectAll = document.getElementById('selectAllConsolidations');
    selectAll.checked = selectedCount === checkboxes.length && checkboxes.length > 0;
    selectAll.indeterminate = selectedCount > 0 && selectedCount < checkboxes.length;
}

// Apply selected consolidations
async function applySelectedConsolidations() {
    const checkboxes = document.querySelectorAll('.consolidation-checkbox:checked');
    const selectedIndices = Array.from(checkboxes).map(cb => parseInt(cb.dataset.index));

    if (selectedIndices.length === 0) {
        showToast('No consolidations selected', 'error');
        return;
    }

    const selectedConsolidations = selectedIndices.map(i => tagConsolidationData[i]);

    const applyBtn = document.getElementById('applyConsolidationsBtn');
    applyBtn.disabled = true;
    applyBtn.textContent = 'Applying...';

    try {
        const response = await fetch('/api/tags/consolidate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ consolidations: selectedConsolidations })
        });

        const data = await response.json();

        if (data.error) {
            throw new Error(data.error);
        }

        showToast(`Consolidated ${data.tags_consolidated} tag groups, updated ${data.updated_summaries} summaries`, 'success');

        // Hide results and refresh tag cloud
        cancelConsolidation();
        loadTagCloud();

    } catch (error) {
        showToast(`Error: ${error.message}`, 'error');
        console.error('Consolidation error:', error);
    } finally {
        applyBtn.disabled = false;
        updateConsolidationSelection();
    }
}

// Cancel consolidation (hide results)
function cancelConsolidation() {
    document.getElementById('tagConsolidationResults').style.display = 'none';
    document.getElementById('selectAllConsolidations').checked = false;
    tagConsolidationData = [];
}

// Event listeners
document.addEventListener('DOMContentLoaded', function() {
    loadTagCloud();

    // Button event listeners
    document.getElementById('refreshModelsBtn').addEventListener('click', loadOllamaModels);
    document.getElementById('advancedToggle').addEventListener('click', toggleAdvancedSettings);
    document.getElementById('promptPreviewToggle').addEventListener('click', togglePromptPreview);
    document.getElementById('tagSearchInput').addEventListener('input', filterTagCloud);
    document.getElementById('analyzeTagsBtn').addEventListener('click', analyzeTagsForConsolidation);
    document.getElementById('clearTagsBtn').addEventListener('click', clearTagSelection);
    document.getElementById('mergeTagsBtn').addEventListener('click', mergeSelectedTags);
    document.getElementById('selectAllConsolidations').addEventListener('change', toggleAllConsolidations);
    document.getElementById('applyConsolidationsBtn').addEventListener('click', applySelectedConsolidations);
    document.getElementById('cancelConsolidationBtn').addEventListener('click', cancelConsolidation);
    document.getElementById('saveBtn').addEventListener('click', saveChanges);
    document.getElementById('saveRestartBtn').addEventListener('click', saveAndRestart);
    document.getElementById('discardBtn').addEventListener('click', discardChanges);
    document.getElementById('exportConfigBtn').addEventListener('click', exportConfig);
    document.getElementById('resetConfigBtn').addEventListener('click', resetConfig);
    document.getElementById('floatingSaveBtn').addEventListener('click', saveChanges);
    document.getElementById('floatingSaveRestartBtn').addEventListener('click', saveAndRestart);

    // Event delegation for dynamically created tag remove buttons
    document.addEventListener('click', function(e) {
        if (e.target.classList.contains('tag-remove')) {
            const containerId = e.target.dataset.container;
            const tag = e.target.dataset.tag;
            if (containerId && tag) {
                removeTag(containerId, tag);
            }
        }
    });
});
