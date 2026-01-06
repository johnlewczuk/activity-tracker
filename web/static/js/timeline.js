/**
 * Activity Tracker - Timeline Page JavaScript
 * Extracted from timeline.html to reduce file size and enable browser caching
 *
 * Dependencies:
 * - Chart.js (loaded via CDN)
 * - utils.js (provides: escapeHtml, formatDuration, getLocalDateString, getThemeColors, showToast)
 */

function applyTimelineChartTheme() {
    if (!state.chart) return;
    const theme = getThemeColors();
    const dataset = state.chart.data.datasets[0];
    dataset.backgroundColor = theme.accent;
    dataset.borderColor = theme.accentStrong;
    dataset.hoverBackgroundColor = theme.accentSoft;

    state.chart.options.scales.y.ticks.color = theme.muted;
    state.chart.options.scales.y.grid.color = theme.border;
    state.chart.options.scales.x.ticks.color = theme.muted;
    state.chart.options.scales.x.grid.color = theme.border;

    const tooltip = state.chart.options.plugins.tooltip;
    tooltip.backgroundColor = theme.surface;
    tooltip.borderColor = theme.border;
    tooltip.titleColor = theme.text;
    tooltip.bodyColor = theme.text;

    state.chart.update();
}

// State management
const state = {
    currentYear: new Date().getFullYear(),
    currentMonth: new Date().getMonth() + 1,
    selectedDate: null,
    selectedHour: null,
    calendarData: null,
    chart: null,
    summaries: {},  // hour -> summary text
    generatingStatus: null,
    pollInterval: null,
    selectedSummaries: new Set(),  // Selected summary IDs for bulk actions
    lastClickedCheckboxIndex: null, // For shift+click multi-select
    // Horizontal timeline state
    timeline: {
        viewStart: 0,      // Start time (ms from day start)
        viewEnd: 86400000, // End time (ms, full day = 24h)
        isDragging: false,
        dragStartX: 0,
        dragStartViewStart: 0,
        sessions: [],
        focusEvents: [],
        screenshots: []
    }
};

// App color palette for consistent coloring
const appColors = {};
const colorPalette = [
    '#58a6ff', '#3fb950', '#f0883e', '#a371f7', '#f778ba',
    '#79c0ff', '#56d364', '#ffa657', '#bc8cff', '#ff9bce',
    '#1f6feb', '#238636', '#9e6a03', '#8957e5', '#bf4b8a',
    '#388bfd', '#2ea043', '#d29922', '#a475f9', '#db61a2'
];

function getAppColor(appName) {
    if (!appName) return '#484f58';
    if (!appColors[appName]) {
        const index = Object.keys(appColors).length % colorPalette.length;
        appColors[appName] = colorPalette[index];
    }
    return appColors[appName];
}

// ==================== Horizontal Timeline (ActivityWatch style) ====================

function renderHorizontalTimeline(dateStr) {
    const container = document.getElementById('detailContent');
    const dayStart = new Date(`${dateStr}T00:00:00`).getTime();

    // Reset view to full day, then restore saved zoom if available
    state.timeline.viewStart = 0;
    state.timeline.viewEnd = 86400000; // 24 hours in ms
    state.timeline.dayStart = dayStart;
    loadSavedZoom(); // Restore user's preferred zoom level

    // Create timeline HTML
    const timelineHtml = `
        <div class="horizontal-timeline" id="horizontalTimeline">
            <div class="timeline-header">
                <div class="timeline-header-left">
                    <h3>Activity Timeline</h3>
                    <span class="timeline-hint">Drag to pan, scroll to zoom</span>
                </div>
                <div class="summarization-status" id="summarizationStatus">
                    <span class="status-item" id="nextRunStatus" title="Next scheduled summarization">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>
                        <span id="nextRunText">--</span>
                    </span>
                    <span class="status-item" id="pendingStatus" title="Screenshots waiting for summarization">
                        <span class="pending-badge" id="pendingCount">0</span> pending
                    </span>
                    <span class="status-item" id="workerStatus" title="Summarization worker status">
                        Worker: <span class="status-badge idle" id="workerBadge">Idle</span>
                    </span>
                </div>
                <div class="timeline-controls">
                    <div class="time-presets">
                        <button class="preset-btn" data-start="6" data-end="12">Morning</button>
                        <button class="preset-btn" data-start="12" data-end="18">Afternoon</button>
                        <button class="preset-btn" data-start="18" data-end="24">Evening</button>
                        <button class="preset-btn" data-start="9" data-end="17">Work</button>
                    </div>
                    <button class="btn-generate-missing" id="btnGenerateMissing" title="Generate summaries for unsummarized sessions on this day">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/></svg>
                        Generate Missing
                    </button>
                    <button id="zoomInBtn">Zoom In</button>
                    <button id="zoomOutBtn">Zoom Out</button>
                    <button id="resetZoomBtn">Reset</button>
                </div>
            </div>
            <div class="timeline-body">
                <div class="timeline-lanes" id="timelineLanes">
                    <div class="timeline-lane" data-lane="sessions">
                        <div class="lane-label">Sessions</div>
                        <div class="lane-content">
                            <div class="lane-events" id="laneSessions"></div>
                        </div>
                    </div>
                    <div class="timeline-lane" data-lane="screenshots">
                        <div class="lane-label">Screenshots</div>
                        <div class="lane-content">
                            <div class="lane-events" id="laneScreenshots"></div>
                        </div>
                    </div>
                    <div class="timeline-lane" data-lane="activity">
                        <div class="lane-label">Activity</div>
                        <div class="lane-content">
                            <div class="lane-events" id="laneAppEvents"></div>
                        </div>
                    </div>
                    <div class="timeline-lane" data-lane="summaries">
                        <div class="lane-label">AI Summaries</div>
                        <div class="lane-content">
                            <div class="lane-events" id="laneSummaries"></div>
                        </div>
                    </div>
                </div>
                <div class="timeline-axis">
                    <div class="axis-label-spacer"></div>
                    <div class="axis-content">
                        <div class="axis-ticks" id="axisTicks"></div>
                    </div>
                </div>
            </div>
            <div class="filmstrip-section" id="filmstripSection">
                <div class="filmstrip-header">
                    <h4>Screenshots</h4>
                    <span class="filmstrip-count" id="filmstripCount">0 in view</span>
                    <button class="toggle-filmstrip" id="toggleFilmstrip">Hide</button>
                    <a href="/day/${state.selectedDate}" class="view-all-link" id="viewAllScreenshots">View All →</a>
                </div>
                <div class="filmstrip-container" id="filmstripContainer"></div>
            </div>
        </div>
    `;

    // Insert timeline at start of container
    container.insertAdjacentHTML('afterbegin', timelineHtml);

    // Render events
    updateTimelineView();

    // Set up interaction handlers
    setupTimelineInteractions();
}

function updateTimelineView() {
    const { viewStart, viewEnd, dayStart, sessions, focusEvents, screenshots } = state.timeline;
    const viewDuration = viewEnd - viewStart;

    // Get container width
    const laneContent = document.querySelector('.lane-content');
    if (!laneContent) return;
    const containerWidth = laneContent.offsetWidth;

    // Helper: convert time to pixel position
    const timeToX = (timeMs) => {
        const offsetFromViewStart = timeMs - viewStart;
        return (offsetFromViewStart / viewDuration) * containerWidth;
    };

    // Helper: convert duration to width
    const durationToWidth = (durationMs) => {
        return (durationMs / viewDuration) * containerWidth;
    };

    // Render Active lane (sessions only, no AFK gaps)
    const sessionsContainer = document.getElementById('laneSessions');
    if (sessionsContainer) {
        sessionsContainer.innerHTML = '';

        sessions.forEach(session => {
            const startTime = new Date(session.start_time).getTime() - dayStart;
            const endTime = session.end_time
                ? new Date(session.end_time).getTime() - dayStart
                : startTime + (session.duration_minutes * 60 * 1000);

            // Skip if outside view
            if (endTime < viewStart || startTime > viewEnd) return;

            const x = timeToX(Math.max(startTime, viewStart));
            const width = durationToWidth(Math.min(endTime, viewEnd) - Math.max(startTime, viewStart));

            if (width < 1) return;

            const el = document.createElement('div');
            el.className = 'lane-event active';
            el.style.left = `${x}px`;
            el.style.width = `${width}px`;
            el.style.background = 'var(--success, #238636)';

            if (width > 40) {
                el.innerHTML = `<span class="lane-event-label">Active</span>`;
            }

            // Tooltip on hover
            const durationMs = endTime - startTime;
            el.addEventListener('mouseenter', (e) => {
                showTimelineTooltip(e, {
                    app_name: 'Active Session',
                    window_title: `${session.screenshot_count || 0} screenshots, ${session.unique_windows || 0} windows`,
                    start_time: session.start_time,
                    duration_seconds: durationMs / 1000
                });
            });
            el.addEventListener('mouseleave', hideTimelineTooltip);

            sessionsContainer.appendChild(el);
        });
    }

    // Render screenshots lane
    const screenshotsContainer = document.getElementById('laneScreenshots');
    if (screenshotsContainer) {
        screenshotsContainer.innerHTML = '';
        screenshots.forEach(screenshot => {
            const timestamp = screenshot.timestamp * 1000; // Convert to ms
            const screenshotTime = timestamp - dayStart;

            // Skip if outside view
            if (screenshotTime < viewStart || screenshotTime > viewEnd) return;

            const x = timeToX(screenshotTime);

            const el = document.createElement('div');
            el.className = 'lane-event screenshot-marker';
            el.style.left = `${x}px`;
            el.style.width = '4px';
            el.style.background = 'var(--text-secondary, #8b949e)';
            el.style.cursor = 'pointer';
            el.dataset.screenshotId = screenshot.id;

            // Click to open modal
            el.addEventListener('click', () => {
                window.showModalWithScreenshots(screenshot.id, screenshots);
            });

            // Tooltip on hover
            el.addEventListener('mouseenter', (e) => {
                showTimelineTooltip(e, {
                    app_name: screenshot.app_name || 'Unknown',
                    window_title: screenshot.window_title || '',
                    start_time: new Date(timestamp).toISOString(),
                    duration_seconds: 0
                }, screenshot.id);
            });
            el.addEventListener('mouseleave', hideTimelineTooltip);

            screenshotsContainer.appendChild(el);
        });
    }

    // Render activity lane (focus events)
    const appsContainer = document.getElementById('laneAppEvents');
    if (appsContainer) {
        appsContainer.innerHTML = '';
        focusEvents.forEach(event => {
            const startTime = new Date(event.start_time).getTime() - dayStart;
            const endTime = event.end_time ? new Date(event.end_time).getTime() - dayStart : startTime + (event.duration_seconds * 1000);

            // Skip if outside view
            if (endTime < viewStart || startTime > viewEnd) return;

            const x = timeToX(Math.max(startTime, viewStart));
            const width = durationToWidth(Math.min(endTime, viewEnd) - Math.max(startTime, viewStart));

            if (width < 1) return;

            const el = document.createElement('div');
            el.className = 'lane-event';
            el.style.left = `${x}px`;
            el.style.width = `${width}px`;
            el.style.background = getAppColor(event.app_name);
            el.dataset.app = event.app_name || '';
            el.dataset.title = event.window_title || '';
            el.dataset.start = event.start_time;
            el.dataset.duration = event.duration_seconds;

            if (width > 40) {
                const label = event.app_name || 'Unknown';
                el.innerHTML = `<span class="lane-event-label">${label}</span>`;
            }

            // Tooltip on hover
            el.addEventListener('mouseenter', (e) => showTimelineTooltip(e, event));
            el.addEventListener('mouseleave', hideTimelineTooltip);

            appsContainer.appendChild(el);
        });
    }

    // Render summaries lane
    const summariesContainer = document.getElementById('laneSummaries');
    if (summariesContainer && state.thresholdSummaries) {
        summariesContainer.innerHTML = '';
        state.thresholdSummaries.forEach(summary => {
            const startTime = new Date(summary.start_time).getTime() - dayStart;
            const endTime = new Date(summary.end_time).getTime() - dayStart;

            // Skip if outside view
            if (endTime < viewStart || startTime > viewEnd) return;

            const x = timeToX(Math.max(startTime, viewStart));
            const width = durationToWidth(Math.min(endTime, viewEnd) - Math.max(startTime, viewStart));

            if (width < 1) return;

            const el = document.createElement('div');
            el.className = 'lane-event summary';
            el.style.left = `${x}px`;
            el.style.width = `${width}px`;
            el.style.background = 'var(--accent, #58a6ff)';
            el.style.cursor = 'pointer';
            el.dataset.summaryId = summary.id;

            if (width > 30) {
                el.innerHTML = `<span class="lane-event-label">Summary</span>`;
            }

            el.addEventListener('click', () => {
                // Scroll to and highlight the summary in the table
                const row = document.querySelector(`tr[data-summary-id="${summary.id}"]`);
                if (row) {
                    row.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    row.style.background = 'rgba(88, 166, 255, 0.3)';
                    setTimeout(() => row.style.background = '', 2000);
                }
            });

            el.addEventListener('mouseenter', (e) => {
                showTimelineTooltip(e, {
                    app_name: 'AI Summary',
                    window_title: summary.summary.substring(0, 100) + (summary.summary.length > 100 ? '...' : ''),
                    start_time: summary.start_time,
                    duration_seconds: (new Date(summary.end_time) - new Date(summary.start_time)) / 1000
                });
            });
            el.addEventListener('mouseleave', hideTimelineTooltip);

            summariesContainer.appendChild(el);
        });
    }

    // Render time axis
    renderTimeAxis();

    // Update filmstrip with screenshots in current view
    renderFilmstrip();

    // Save zoom state to localStorage
    saveZoomState();
}

function renderTimeAxis() {
    const { viewStart, viewEnd } = state.timeline;
    const viewDuration = viewEnd - viewStart;
    const axisTicks = document.getElementById('axisTicks');
    if (!axisTicks) return;

    const containerWidth = axisTicks.parentElement.offsetWidth;

    // Determine tick interval based on zoom level
    const hourMs = 3600000;
    const minuteMs = 60000;
    let tickInterval;
    let labelFormat;

    if (viewDuration <= hourMs) {
        tickInterval = 5 * minuteMs; // 5 min
        labelFormat = 'minute';
    } else if (viewDuration <= 4 * hourMs) {
        tickInterval = 15 * minuteMs; // 15 min
        labelFormat = 'minute';
    } else if (viewDuration <= 12 * hourMs) {
        tickInterval = 30 * minuteMs; // 30 min
        labelFormat = 'hour';
    } else {
        tickInterval = hourMs; // 1 hour
        labelFormat = 'hour';
    }

    // Calculate first tick
    const firstTick = Math.ceil(viewStart / tickInterval) * tickInterval;

    axisTicks.innerHTML = '';
    for (let t = firstTick; t <= viewEnd; t += tickInterval) {
        const x = ((t - viewStart) / viewDuration) * containerWidth;
        const tick = document.createElement('div');
        tick.className = 'axis-tick';
        tick.style.left = `${x}px`;

        // Format time
        const date = new Date(state.timeline.dayStart + t);
        if (labelFormat === 'minute') {
            tick.textContent = date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', hour12: true });
        } else {
            tick.textContent = date.toLocaleTimeString([], { hour: 'numeric', hour12: true });
        }

        axisTicks.appendChild(tick);
    }
}

// Store references to document-level listeners for cleanup
let timelineMouseMoveHandler = null;
let timelineMouseUpHandler = null;

function cleanupTimelineListeners() {
    if (timelineMouseMoveHandler) {
        document.removeEventListener('mousemove', timelineMouseMoveHandler);
        timelineMouseMoveHandler = null;
    }
    if (timelineMouseUpHandler) {
        document.removeEventListener('mouseup', timelineMouseUpHandler);
        timelineMouseUpHandler = null;
    }
}

function setupTimelineInteractions() {
    const lanes = document.getElementById('timelineLanes');
    if (!lanes) return;

    // Clean up any existing document-level listeners
    cleanupTimelineListeners();

    // Pan with drag
    lanes.addEventListener('mousedown', (e) => {
        state.timeline.isDragging = true;
        state.timeline.dragStartX = e.clientX;
        state.timeline.dragStartViewStart = state.timeline.viewStart;
        lanes.style.cursor = 'grabbing';
    });

    timelineMouseMoveHandler = (e) => {
        if (!state.timeline.isDragging) return;

        const dx = e.clientX - state.timeline.dragStartX;
        const containerWidth = document.querySelector('.lane-content')?.offsetWidth || 1;
        const viewDuration = state.timeline.viewEnd - state.timeline.viewStart;
        const timeDelta = (dx / containerWidth) * viewDuration;

        let newStart = state.timeline.dragStartViewStart - timeDelta;
        let newEnd = newStart + viewDuration;

        // Clamp to day bounds
        if (newStart < 0) {
            newStart = 0;
            newEnd = viewDuration;
        }
        if (newEnd > 86400000) {
            newEnd = 86400000;
            newStart = newEnd - viewDuration;
        }

        state.timeline.viewStart = newStart;
        state.timeline.viewEnd = newEnd;
        updateTimelineView();
    };

    timelineMouseUpHandler = () => {
        if (state.timeline.isDragging) {
            state.timeline.isDragging = false;
            const lanes = document.getElementById('timelineLanes');
            if (lanes) lanes.style.cursor = 'grab';
        }
    };

    document.addEventListener('mousemove', timelineMouseMoveHandler);
    document.addEventListener('mouseup', timelineMouseUpHandler);

    // Zoom with scroll
    lanes.addEventListener('wheel', (e) => {
        e.preventDefault();
        const zoomFactor = e.deltaY > 0 ? 1.2 : 0.8;
        zoomTimelineAt(e.clientX, zoomFactor);
    });

    // Timeline control buttons
    document.querySelectorAll('.preset-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            applyTimePreset(parseInt(btn.dataset.start), parseInt(btn.dataset.end));
        });
    });

    const generateMissingBtn = document.getElementById('btnGenerateMissing');
    if (generateMissingBtn) {
        generateMissingBtn.addEventListener('click', triggerSummarization);
    }

    const zoomInBtn = document.getElementById('zoomInBtn');
    if (zoomInBtn) zoomInBtn.addEventListener('click', () => zoomTimeline(0.5));

    const zoomOutBtn = document.getElementById('zoomOutBtn');
    if (zoomOutBtn) zoomOutBtn.addEventListener('click', () => zoomTimeline(2));

    const resetZoomBtn = document.getElementById('resetZoomBtn');
    if (resetZoomBtn) resetZoomBtn.addEventListener('click', resetTimelineZoom);

    const toggleFilmstripBtn = document.getElementById('toggleFilmstrip');
    if (toggleFilmstripBtn) toggleFilmstripBtn.addEventListener('click', toggleFilmstrip);
}

function zoomTimeline(factor) {
    const containerWidth = document.querySelector('.lane-content')?.offsetWidth || 1;
    const centerX = containerWidth / 2;
    zoomTimelineAt(centerX + document.querySelector('.lane-label').offsetWidth, factor);
}

function zoomTimelineAt(clientX, factor) {
    const laneContent = document.querySelector('.lane-content');
    if (!laneContent) return;

    const rect = laneContent.getBoundingClientRect();
    const mouseX = clientX - rect.left;
    const containerWidth = laneContent.offsetWidth;

    const { viewStart, viewEnd } = state.timeline;
    const viewDuration = viewEnd - viewStart;
    const mouseTime = viewStart + (mouseX / containerWidth) * viewDuration;

    let newDuration = viewDuration * factor;

    // Clamp zoom level
    const minDuration = 5 * 60000; // 5 minutes minimum
    const maxDuration = 86400000; // 24 hours maximum
    newDuration = Math.max(minDuration, Math.min(maxDuration, newDuration));

    // Calculate new view centered on mouse position
    const mouseRatio = mouseX / containerWidth;
    let newStart = mouseTime - (mouseRatio * newDuration);
    let newEnd = newStart + newDuration;

    // Clamp to day bounds
    if (newStart < 0) {
        newStart = 0;
        newEnd = newDuration;
    }
    if (newEnd > 86400000) {
        newEnd = 86400000;
        newStart = Math.max(0, newEnd - newDuration);
    }

    state.timeline.viewStart = newStart;
    state.timeline.viewEnd = newEnd;
    updateTimelineView();
}

function resetTimelineZoom() {
    state.timeline.viewStart = 0;
    state.timeline.viewEnd = 86400000;
    clearPresetActive();
    updateTimelineView();
}

// Zoom persistence
const ZOOM_STORAGE_KEY = 'activity-tracker-timeline-zoom';

function saveZoomState() {
    localStorage.setItem(ZOOM_STORAGE_KEY, JSON.stringify({
        viewStart: state.timeline.viewStart,
        viewEnd: state.timeline.viewEnd
    }));
}

function loadSavedZoom() {
    try {
        const saved = localStorage.getItem(ZOOM_STORAGE_KEY);
        if (saved) {
            const { viewStart, viewEnd } = JSON.parse(saved);
            state.timeline.viewStart = viewStart;
            state.timeline.viewEnd = viewEnd;
        }
    } catch (e) {
        console.warn('Failed to load saved zoom state:', e);
    }
}

// Time presets
function applyTimePreset(startHour, endHour) {
    state.timeline.viewStart = startHour * 3600000; // Hours to ms
    state.timeline.viewEnd = endHour * 3600000;

    // Update active button
    clearPresetActive();
    document.querySelectorAll('.preset-btn').forEach(btn => {
        if (parseInt(btn.dataset.start) === startHour && parseInt(btn.dataset.end) === endHour) {
            btn.classList.add('active');
        }
    });

    updateTimelineView();
}

function clearPresetActive() {
    document.querySelectorAll('.preset-btn').forEach(btn => btn.classList.remove('active'));
}

// Summarization status polling
let statusPollInterval = null;

async function updateSummarizationStatus() {
    try {
        const [pendingRes, workerRes] = await Promise.all([
            fetch('/api/threshold-summaries/pending'),
            fetch('/api/threshold-summaries/worker-status')
        ]);

        const pending = await pendingRes.json();
        const worker = await workerRes.json();

        // Update pending count
        const pendingCountEl = document.getElementById('pendingCount');
        if (pendingCountEl) {
            const count = pending.unsummarized_count || 0;
            pendingCountEl.textContent = count;
            pendingCountEl.classList.toggle('zero', count === 0);
        }

        // Update next run time
        const nextRunTextEl = document.getElementById('nextRunText');
        if (nextRunTextEl && pending.minutes_until_next !== undefined) {
            const mins = Math.round(pending.minutes_until_next);
            if (mins <= 0) {
                nextRunTextEl.textContent = 'Now';
            } else if (mins < 60) {
                nextRunTextEl.textContent = `${mins}m`;
            } else {
                const hrs = Math.floor(mins / 60);
                const remainMins = mins % 60;
                nextRunTextEl.textContent = `${hrs}h ${remainMins}m`;
            }
        }

        // Update worker status
        const workerBadge = document.getElementById('workerBadge');
        if (workerBadge) {
            if (worker.current_task) {
                workerBadge.textContent = 'Active';
                workerBadge.className = 'status-badge active';
            } else {
                workerBadge.textContent = 'Idle';
                workerBadge.className = 'status-badge idle';
            }
        }
    } catch (e) {
        console.warn('Failed to fetch summarization status:', e);
    }
}

function startStatusPolling() {
    updateSummarizationStatus();
    if (statusPollInterval) {
        clearInterval(statusPollInterval);
    }
    statusPollInterval = setInterval(updateSummarizationStatus, 30000); // Poll every 30s
}

function stopStatusPolling() {
    if (statusPollInterval) {
        clearInterval(statusPollInterval);
        statusPollInterval = null;
    }
}

// Screenshot Filmstrip
let filmstripHidden = false;

function renderFilmstrip() {
    const container = document.getElementById('filmstripContainer');
    const countEl = document.getElementById('filmstripCount');
    const viewAllLink = document.getElementById('viewAllScreenshots');
    if (!container) return;

    // Update the View All link to point to current date
    if (viewAllLink && state.selectedDate) {
        viewAllLink.href = `/day/${state.selectedDate}`;
    }

    const { viewStart, viewEnd, dayStart, screenshots } = state.timeline;
    if (!screenshots || screenshots.length === 0) {
        container.innerHTML = '<div style="color: var(--muted); font-size: 0.75rem; padding: 10px;">No screenshots</div>';
        if (countEl) countEl.textContent = '0 in view';
        return;
    }

    // Filter screenshots to those in the current zoom window
    const visibleScreenshots = screenshots.filter(s => {
        const timeMs = (s.timestamp * 1000) - dayStart;
        return timeMs >= viewStart && timeMs <= viewEnd;
    });

    if (countEl) countEl.textContent = `${visibleScreenshots.length} in view`;

    if (visibleScreenshots.length === 0) {
        container.innerHTML = '<div style="color: var(--muted); font-size: 0.75rem; padding: 10px;">No screenshots in current view</div>';
        return;
    }

    container.innerHTML = visibleScreenshots.map(s => {
        const time = new Date(s.timestamp * 1000);
        const timeStr = time.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', hour12: true });
        const appName = s.app_name || 'Unknown';

        return `
            <div class="filmstrip-item" data-screenshot-id="${s.id}">
                <img class="filmstrip-thumb" src="/thumbnail/${s.id}" alt="Screenshot at ${timeStr}" loading="lazy">
                <div class="filmstrip-meta">
                    <span class="filmstrip-time">${timeStr}</span>
                    <span class="filmstrip-app" title="${appName}">${appName}</span>
                </div>
            </div>
        `;
    }).join('');
}

function toggleFilmstrip() {
    const section = document.getElementById('filmstripSection');
    const btn = document.getElementById('toggleFilmstrip');
    if (!section || !btn) return;

    filmstripHidden = !filmstripHidden;
    section.classList.toggle('hidden', filmstripHidden);
    btn.textContent = filmstripHidden ? 'Show' : 'Hide';
}

// Week View
state.calendarView = 'month'; // 'month' or 'week'

function toggleCalendarView(view) {
    state.calendarView = view;

    // Update toggle button states
    document.querySelectorAll('.view-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.view === view);
    });

    // Show/hide appropriate panels
    const calendarGrid = document.getElementById('calendarGrid');
    const calendarLegend = document.querySelector('.calendar-legend');
    const calendarWeekdays = document.querySelector('.calendar-weekdays');
    const weekViewPanel = document.getElementById('weekViewPanel');

    // Update navigation button tooltips
    const prevBtn = document.getElementById('prevMonth');
    const nextBtn = document.getElementById('nextMonth');

    if (view === 'week') {
        if (prevBtn) prevBtn.title = 'Previous week (H key)';
        if (nextBtn) nextBtn.title = 'Next week (L key)';
        if (calendarGrid) calendarGrid.style.display = 'none';
        if (calendarLegend) calendarLegend.style.display = 'none';
        if (calendarWeekdays) calendarWeekdays.style.display = 'none';
        if (weekViewPanel) weekViewPanel.style.display = 'block';
        renderWeekView();
    } else {
        if (prevBtn) prevBtn.title = 'Previous month (H key)';
        if (nextBtn) nextBtn.title = 'Next month (L key)';
        if (calendarGrid) calendarGrid.style.display = '';
        if (calendarLegend) calendarLegend.style.display = '';
        if (calendarWeekdays) calendarWeekdays.style.display = '';
        if (weekViewPanel) weekViewPanel.style.display = 'none';
        // Restore month header
        updateMonthDisplay();
    }
}

function updateMonthDisplay() {
    const monthDisplay = document.getElementById('monthDisplay');
    if (monthDisplay) {
        const monthNames = ['January', 'February', 'March', 'April', 'May', 'June',
                          'July', 'August', 'September', 'October', 'November', 'December'];
        monthDisplay.textContent = `${monthNames[state.currentMonth - 1]} ${state.currentYear}`;
    }
}

function getWeekStart(dateStr) {
    const date = new Date(dateStr + 'T12:00:00');
    const day = date.getDay(); // 0 = Sunday
    date.setDate(date.getDate() - day);
    return date;
}

async function renderWeekView() {
    const strip = document.getElementById('weekDaysStrip');
    if (!strip) return;

    // Get week containing selected date (or today)
    const selectedDate = state.selectedDate || getLocalDateString(new Date());
    const weekStart = getWeekStart(selectedDate);
    const todayStr = getLocalDateString(new Date());

    const days = [];
    for (let i = 0; i < 7; i++) {
        const day = new Date(weekStart);
        day.setDate(day.getDate() + i);
        days.push(day);
    }

    // Update header to show week range
    const monthDisplay = document.getElementById('monthDisplay');
    if (monthDisplay) {
        const weekEnd = days[6];
        const startMonth = weekStart.toLocaleDateString('en-US', { month: 'short' });
        const endMonth = weekEnd.toLocaleDateString('en-US', { month: 'short' });
        const startDay = weekStart.getDate();
        const endDay = weekEnd.getDate();
        const startYear = weekStart.getFullYear();
        const endYear = weekEnd.getFullYear();

        let weekLabel;
        if (startYear !== endYear) {
            weekLabel = `${startMonth} ${startDay}, ${startYear} - ${endMonth} ${endDay}, ${endYear}`;
        } else if (startMonth !== endMonth) {
            weekLabel = `${startMonth} ${startDay} - ${endMonth} ${endDay}, ${endYear}`;
        } else {
            weekLabel = `${startMonth} ${startDay} - ${endDay}, ${startYear}`;
        }
        monthDisplay.textContent = weekLabel;
    }

    // Show loading state
    strip.innerHTML = days.map(() => `
        <div class="week-day-column" style="opacity: 0.5;">
            <div class="week-day-header">
                <div class="week-day-name">...</div>
                <div class="week-day-date">-</div>
            </div>
            <div class="week-mini-timeline">
                <div class="no-activity-message">Loading...</div>
            </div>
        </div>
    `).join('');

    // Fetch activity data for all 7 days in parallel
    const dayDataPromises = days.map(d => {
        const dateStr = getLocalDateString(d);
        return fetch(`/api/analytics/focus/timeline?start=${dateStr}T00:00:00&end=${dateStr}T23:59:59`)
            .then(r => r.json())
            .catch(() => ({ events: [] }));
    });

    const dayData = await Promise.all(dayDataPromises);

    // Render week view
    strip.innerHTML = days.map((day, i) => {
        const dateStr = getLocalDateString(day);
        const events = dayData[i].events || [];
        const isSelected = dateStr === state.selectedDate;
        const isToday = dateStr === todayStr;

        // Generate mini-timeline bars
        const miniTimelineHtml = generateMiniTimeline(events);

        // Calculate stats
        const totalSeconds = events.reduce((sum, e) => sum + (e.duration_seconds || 0), 0);
        const hours = Math.floor(totalSeconds / 3600);
        const mins = Math.round((totalSeconds % 3600) / 60);
        const statsText = totalSeconds > 0 ? `${hours}h ${mins}m` : 'No activity';

        return `
            <div class="week-day-column ${isSelected ? 'selected' : ''} ${isToday ? 'today' : ''}"
                 data-date="${dateStr}">
                <div class="week-day-header">
                    <div class="week-day-name">${day.toLocaleDateString('en-US', {weekday: 'short'})}</div>
                    <div class="week-day-date">${day.getDate()}</div>
                </div>
                <div class="week-mini-timeline">
                    ${miniTimelineHtml}
                </div>
                <div class="week-day-stats">${statsText}</div>
            </div>
        `;
    }).join('');
}

function generateMiniTimeline(events) {
    if (!events || events.length === 0) {
        return '<div class="no-activity-message">No activity</div>';
    }

    return events.map(e => {
        const start = new Date(e.start_time);
        const duration = e.duration_seconds || 60;
        // Position as percentage of day (0-24 hours)
        const startMinutes = start.getHours() * 60 + start.getMinutes();
        const startPct = (startMinutes / 1440) * 100;
        const widthPct = Math.max(0.5, (duration / 86400) * 100);
        const color = getAppColor(e.app_name);

        return `<div class="mini-activity-bar" style="left:${startPct}%;width:${widthPct}%;background:${color};"></div>`;
    }).join('');
}

function selectDateFromWeek(dateStr) {
    // Update week view selection
    document.querySelectorAll('.week-day-column').forEach(col => {
        col.classList.toggle('selected', col.dataset.date === dateStr);
    });

    // Select the date (this will load day data)
    const [year, month] = dateStr.split('-').map(Number);
    if (month !== state.currentMonth || year !== state.currentYear) {
        state.currentMonth = month;
        state.currentYear = year;
        loadCalendar().then(() => selectDate(dateStr));
    } else {
        selectDate(dateStr);
    }
}

// Tooltip
let tooltipEl = null;

function showTimelineTooltip(e, event, screenshotId = null) {
    if (!tooltipEl) {
        tooltipEl = document.createElement('div');
        tooltipEl.className = 'timeline-tooltip';
        document.body.appendChild(tooltipEl);
    }

    const startTime = new Date(event.start_time);
    const timeStr = startTime.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', hour12: true });
    const duration = event.duration_seconds ? formatDuration(event.duration_seconds) : '';

    // Include thumbnail if screenshotId is provided
    const thumbnailHtml = screenshotId
        ? `<img class="tooltip-thumbnail" src="/thumbnail/${screenshotId}" alt="Screenshot">`
        : '';

    tooltipEl.innerHTML = `
        ${thumbnailHtml}
        <div class="tooltip-time">${timeStr}${duration ? ` (${duration})` : ''}</div>
        <div class="tooltip-app">${event.app_name || 'Unknown'}</div>
        ${event.window_title ? `<div class="tooltip-title">${event.window_title}</div>` : ''}
    `;

    tooltipEl.style.display = 'block';
    tooltipEl.style.left = `${e.clientX + 10}px`;
    tooltipEl.style.top = `${e.clientY + 10}px`;
}

function hideTimelineTooltip() {
    if (tooltipEl) {
        tooltipEl.style.display = 'none';
    }
}

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    // Initialize modal click handlers (with empty screenshots array initially)
    initializeTimelineModalClickHandler([]);

    // Load calendar and auto-select today on page load
    const today = new Date();
    const todayStr = getLocalDateString(today);

    loadCalendar().then(() => {
        // Automatically select today's date when page loads
        selectDate(todayStr);
    });

    setupEventListeners();
    const themeMedia = window.matchMedia('(prefers-color-scheme: dark)');
    themeMedia.addEventListener('change', applyTimelineChartTheme);

    // Today link handler - navigate to today's date and select it
    const todayLink = document.getElementById('todayNav');
    todayLink.addEventListener('click', (e) => {
        e.preventDefault();
        const today = new Date();
        const todayStr = getLocalDateString(today);

        // Check if we need to change month
        const currentMonth = today.getMonth() + 1;
        const currentYear = today.getFullYear();

        if (currentMonth !== state.currentMonth || currentYear !== state.currentYear) {
            state.currentMonth = currentMonth;
            state.currentYear = currentYear;
            loadCalendar().then(() => selectDate(todayStr));
        } else {
            selectDate(todayStr);
        }
    });

    // Cleanup on page unload to prevent memory leaks
    window.addEventListener('pagehide', cleanupPageResources);
});

function cleanupPageResources() {
    cleanupTimelineListeners();
    stopStatusPolling();
    if (state.pollInterval) {
        clearInterval(state.pollInterval);
        state.pollInterval = null;
    }
}

// Event listeners
function setupEventListeners() {
    const prevMonth = document.getElementById('prevMonth');
    const nextMonth = document.getElementById('nextMonth');
    if (prevMonth) prevMonth.addEventListener('click', () => navigateCalendar(-1));
    if (nextMonth) nextMonth.addEventListener('click', () => navigateCalendar(1));

    // Calendar toggle (collapse/expand)
    const calendarToggle = document.getElementById('calendarToggle');
    const calendarContent = document.getElementById('calendarContent');
    if (calendarToggle && calendarContent) {
        calendarToggle.addEventListener('click', () => {
            const isExpanded = calendarToggle.getAttribute('aria-expanded') === 'true';
            calendarToggle.setAttribute('aria-expanded', !isExpanded);
            calendarContent.classList.toggle('collapsed', isExpanded);
        });
    }

    // Day navigation buttons
    const prevDayBtn = document.getElementById('prevDayBtn');
    const nextDayBtn = document.getElementById('nextDayBtn');
    if (prevDayBtn) prevDayBtn.addEventListener('click', () => navigateDay(-1));
    if (nextDayBtn) nextDayBtn.addEventListener('click', () => navigateDay(1));

    // Date picker input handler
    const datePicker = document.getElementById('datePickerInput');
    if (datePicker) {
        datePicker.addEventListener('change', (e) => {
            const dateStr = e.target.value;
            if (dateStr) {
                const [year, month] = dateStr.split('-').map(Number);

                // Update month view if needed
                if (month !== state.currentMonth || year !== state.currentYear) {
                    state.currentMonth = month;
                    state.currentYear = year;
                    loadCalendar().then(() => selectDate(dateStr));
                } else {
                    selectDate(dateStr);
                }
            }
        });
    }

    // View toggle (Month/Week)
    document.querySelectorAll('.view-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            toggleCalendarView(btn.dataset.view);
        });
    });

    // Keyboard navigation
    document.addEventListener('keydown', (e) => {
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

        // Check if screenshot modal is open - if so, let modal handler deal with it
        if (ScreenshotModal.isActive()) {
            // Only allow Escape to close modal, modal handler will handle arrow keys
            return;
        }

        switch(e.key.toLowerCase()) {
            // Global navigation
            case 't':
                if (!e.metaKey && !e.ctrlKey) {
                    window.location.href = '/timeline';
                }
                break;
            case 'a':
                if (!e.metaKey && !e.ctrlKey) {
                    window.location.href = '/analytics';
                }
                break;
            case 'd':
                if (!e.metaKey && !e.ctrlKey) {
                    const today = getLocalDateString(new Date());
                    selectDate(today);
                }
                break;
            // Timeline-specific navigation
            case 'h':
                navigateCalendar(-1);
                break;
            case 'l':
                navigateCalendar(1);
                break;
            case 'arrowleft':
                navigateDay(-1);
                break;
            case 'arrowright':
                navigateDay(1);
                break;
            case 'arrowup':
                navigateDay(-7);
                break;
            case 'arrowdown':
                navigateDay(7);
                break;
        }
    });

    // Event delegation for dynamically generated content
    document.addEventListener('click', function(e) {
        // Week day column click
        const weekDayCol = e.target.closest('.week-day-column');
        if (weekDayCol && weekDayCol.dataset.date) {
            selectDateFromWeek(weekDayCol.dataset.date);
            return;
        }

        // Filmstrip item click
        const filmstripItem = e.target.closest('.filmstrip-item');
        if (filmstripItem && filmstripItem.dataset.screenshotId) {
            window.showModalWithScreenshots(parseInt(filmstripItem.dataset.screenshotId), state.timeline.screenshots);
            return;
        }

        // Generate button click (for individual hours)
        if (e.target.classList.contains('generate-btn') && e.target.dataset.hour) {
            e.stopPropagation();
            generateSummaries([parseInt(e.target.dataset.hour)]);
            return;
        }

        // Generate All button click
        if (e.target.id === 'generateAllBtn' || e.target.closest('#generateAllBtn')) {
            generateSummaries();
            return;
        }

        // Bulk action buttons
        if (e.target.id === 'bulkRegenerateBtn' || e.target.closest('#bulkRegenerateBtn')) {
            bulkRegenerate();
            return;
        }
        if (e.target.id === 'bulkDeleteBtn' || e.target.closest('#bulkDeleteBtn')) {
            bulkDelete();
            return;
        }
        if (e.target.id === 'bulkCancelBtn' || e.target.closest('#bulkCancelBtn')) {
            clearSelection();
            return;
        }

        // Summary table row click
        const tableRow = e.target.closest('tr[data-summary-id]');
        if (tableRow && !e.target.closest('.checkbox-cell') && !e.target.classList.contains('explanation-icon')) {
            handleRowClick(e, parseInt(tableRow.dataset.summaryId));
            return;
        }

        // Explanation icon click
        if (e.target.classList.contains('explanation-icon')) {
            e.stopPropagation();
            showExplanation(e.target.dataset.explanation);
            return;
        }

        // Summary checkbox click
        if (e.target.classList.contains('summary-checkbox') && e.target.dataset.id) {
            handleCheckboxClick(e, parseInt(e.target.dataset.id));
            return;
        }

        // Tag badge click
        if (e.target.classList.contains('tag-badge') || e.target.closest('.tag-badge')) {
            const badge = e.target.classList.contains('tag-badge') ? e.target : e.target.closest('.tag-badge');
            toggleTagFilter(badge.dataset.tag);
            return;
        }
    });

    // Select all checkbox
    document.addEventListener('change', function(e) {
        if (e.target.id === 'selectAllCheckbox') {
            toggleSelectAll(e);
        }
    });

    // Tag badge hover effects via delegation
    document.addEventListener('mouseenter', function(e) {
        if (e.target.classList && e.target.classList.contains('tag-badge')) {
            highlightTagRows(e.target.dataset.tag);
        }
    }, true);

    document.addEventListener('mouseleave', function(e) {
        if (e.target.classList && e.target.classList.contains('tag-badge')) {
            clearTagHighlight();
        }
    }, true);
}

// Change month
function changeMonth(delta) {
    state.currentMonth += delta;
    if (state.currentMonth > 12) {
        state.currentMonth = 1;
        state.currentYear++;
    } else if (state.currentMonth < 1) {
        state.currentMonth = 12;
        state.currentYear--;
    }
    loadCalendar();
}

// Change week (navigate by 7 days)
function changeWeek(delta) {
    const currentDate = state.selectedDate
        ? new Date(state.selectedDate + 'T12:00:00')
        : new Date();
    currentDate.setDate(currentDate.getDate() + (delta * 7));

    const newDateStr = getLocalDateString(currentDate);
    state.selectedDate = newDateStr;

    // Update month/year state if needed
    state.currentMonth = currentDate.getMonth() + 1;
    state.currentYear = currentDate.getFullYear();

    renderWeekView();
    loadDayData(newDateStr);
}

// Navigate calendar based on current view
function navigateCalendar(delta) {
    if (state.calendarView === 'week') {
        changeWeek(delta);
    } else {
        changeMonth(delta);
    }
}

// Navigate between days
function navigateDay(delta) {
    if (!state.selectedDate) return;

    const currentDate = new Date(state.selectedDate + 'T12:00:00');  // Use noon to avoid timezone issues
    currentDate.setDate(currentDate.getDate() + delta);

    const dateStr = getLocalDateString(currentDate);

    // Check if we need to change month
    const newMonth = currentDate.getMonth() + 1;
    const newYear = currentDate.getFullYear();

    if (newMonth !== state.currentMonth || newYear !== state.currentYear) {
        state.currentMonth = newMonth;
        state.currentYear = newYear;
        loadCalendar().then(() => selectDate(dateStr));
    } else {
        selectDate(dateStr);
    }
}

// Load calendar data
async function loadCalendar() {
    updateMonthDisplay();

    try {
        const response = await fetch(`/api/calendar/${state.currentYear}/${state.currentMonth}`);
        const data = await response.json();

        if (data.error) {
            console.error('Calendar error:', data.error);
            return;
        }

        state.calendarData = data.days;
        renderCalendar();
    } catch (error) {
        console.error('Failed to load calendar:', error);
        document.getElementById('calendarGrid').innerHTML =
            '<div class="empty-state"><p>Failed to load calendar data</p></div>';
    }
}

// Render calendar grid
function renderCalendar() {
    const grid = document.getElementById('calendarGrid');
    grid.innerHTML = '';

    // Calculate starting day of month
    const firstDay = new Date(state.currentYear, state.currentMonth - 1, 1);
    const startingDayOfWeek = firstDay.getDay();

    // Add empty cells for days before month starts
    for (let i = 0; i < startingDayOfWeek; i++) {
        const emptyDay = document.createElement('div');
        emptyDay.className = 'calendar-day empty';
        grid.appendChild(emptyDay);
    }

    // Get today's date for highlighting
    const today = new Date();
    const todayStr = getLocalDateString(today);

    // Add days
    state.calendarData.forEach(day => {
        // Use button for keyboard accessibility
        const dayElement = document.createElement('button');
        dayElement.type = 'button';
        dayElement.className = 'calendar-day';

        const dayNumber = parseInt(day.date.split('-')[2]);
        dayElement.textContent = dayNumber;
        dayElement.dataset.date = day.date;

        // Set intensity
        const intensity = getIntensityLevel(day.intensity);
        dayElement.dataset.intensity = intensity;

        // Mark selected
        if (day.date === state.selectedDate) {
            dayElement.classList.add('selected');
            dayElement.setAttribute('aria-current', 'date');
        }

        // Accessible label
        const dateObj = new Date(day.date + 'T12:00:00');
        const dateLabel = dateObj.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' });
        dayElement.setAttribute('aria-label', `${dateLabel}, ${day.screenshot_count} screenshots, ${day.active_hours} active hours`);

        // Click handler
        dayElement.addEventListener('click', () => selectDate(day.date));

        // Tooltip for mouse users
        dayElement.title = `${day.date}\n${day.screenshot_count} screenshots\n${day.active_hours} active hours`;

        grid.appendChild(dayElement);
    });
}

// Convert intensity (0-1) to level (0-4)
function getIntensityLevel(intensity) {
    if (intensity === 0) return 0;
    if (intensity < 0.25) return 1;
    if (intensity < 0.5) return 2;
    if (intensity < 0.75) return 3;
    return 4;
}

// Select a date
async function selectDate(dateStr) {
    state.selectedDate = dateStr;
    state.selectedHour = null;

    // Update calendar UI
    document.querySelectorAll('.calendar-day').forEach(day => {
        day.classList.remove('selected');
        day.removeAttribute('aria-current');
        if (day.dataset.date === dateStr) {
            day.classList.add('selected');
            day.setAttribute('aria-current', 'date');
        }
    });

    // Update header
    const date = new Date(dateStr + 'T00:00:00');
    const options = { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' };
    document.getElementById('selectedDate').textContent = date.toLocaleDateString('en-US', options);

    // Sync date picker input
    const datePicker = document.getElementById('datePickerInput');
    if (datePicker) {
        datePicker.value = dateStr;
    }

    // Load day data
    await loadDayData(dateStr);
}

// Load day summary and hourly data
async function loadDayData(dateStr) {
    const detailContent = document.getElementById('detailContent');
    detailContent.innerHTML = '<div class="loading"><div class="spinner"></div><div>Loading...</div></div>';

    try {
        // Fetch summary, hourly data, threshold summaries, sessions, and focus events
        const startISO = `${dateStr}T00:00:00`;
        const endISO = `${dateStr}T23:59:59`;

        const [summaryRes, hourlyRes, thresholdRes, sessionsRes, focusRes, screenshotsRes] = await Promise.all([
            fetch(`/api/day/${dateStr}/summary`),
            fetch(`/api/day/${dateStr}/hourly`),
            fetch(`/api/threshold-summaries/${dateStr}`),
            fetch(`/api/sessions/${dateStr}`),
            fetch(`/api/analytics/focus/timeline?start=${startISO}&end=${endISO}`),
            fetch(`/api/screenshots/${dateStr}`)
        ]);

        const summaryData = await summaryRes.json();
        const hourlyData = await hourlyRes.json();
        const thresholdData = await thresholdRes.json();
        const sessionsData = await sessionsRes.json();
        const focusData = await focusRes.json();
        const screenshotsData = await screenshotsRes.json();

        // Store data in state
        state.thresholdSummaries = thresholdData.summaries || [];
        state.timeline.sessions = sessionsData.sessions || [];
        state.timeline.focusEvents = focusData.events || [];
        state.timeline.screenshots = screenshotsData.screenshots || [];

        if (summaryData.error || hourlyData.error) {
            throw new Error(summaryData.error || hourlyData.error);
        }

        const summary = summaryData.summary;
        const workLife = summaryData.work_life || {};
        const goals = summaryData.goals || {};
        const hourly = hourlyData.hourly;

        // Store hourly data for use in rendering
        state.hourlyData = hourly;

        // Update stats with work/life balance data and goals
        updateStats(summary, workLife, goals, dateStr);

        // Show the Day Overview section
        document.getElementById('leftChartSection').style.display = 'block';

        // Check if there's any data
        if (summary.total_screenshots === 0) {
            detailContent.innerHTML = `
                <div class="empty-state">
                    <div class="empty-state-icon">💤</div>
                    <p>No activity recorded for this day</p>
                </div>
            `;
            return;
        }

        // Clear loading spinner before rendering content
        detailContent.innerHTML = '';

        // Render horizontal timeline (ActivityWatch style)
        renderHorizontalTimeline(dateStr);

        // Render summaries table with hourly sections
        renderThresholdSummaries();

        // Start polling for summarization status
        startStatusPolling();

    } catch (error) {
        console.error('Failed to load day data:', error);
        detailContent.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">⚠️</div>
                <p>Failed to load activity data</p>
            </div>
        `;
    }
}

// Render only the hourly chart (for session view)
function renderHourlyChartOnly(hourlyData) {
    const leftChartSection = document.getElementById('leftChartSection');
    leftChartSection.style.display = 'block';

    const ctx = document.getElementById('hourlyChart').getContext('2d');

    if (state.chart) {
        state.chart.destroy();
    }

    // Clean up old scroll observer
    if (state.scrollObserver) {
        state.scrollObserver.disconnect();
        state.scrollObserver = null;
    }

    const labels = hourlyData.map(h => `${h.hour}:00`);
    const data = hourlyData.map(h => h.screenshot_count);
    const theme = getThemeColors();

    state.chart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [{
                label: 'Screenshots',
                data: data,
                backgroundColor: theme.accent,
                borderColor: theme.accentStrong,
                borderWidth: 1,
                hoverBackgroundColor: theme.accentSoft,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                y: {
                    beginAtZero: true,
                    ticks: { stepSize: 1, color: theme.muted },
                    grid: { color: theme.border }
                },
                x: {
                    ticks: { color: theme.muted },
                    grid: { color: theme.border }
                }
            },
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: theme.surface,
                    borderColor: theme.border,
                    borderWidth: 1,
                    titleColor: theme.text,
                    bodyColor: theme.text
                }
            },
            onClick: (event, elements) => {
                if (elements.length > 0) {
                    const index = elements[0].index;
                    const label = state.chart.data.labels[index];
                    const hour = parseInt(label);
                    scrollToHourSection(hour);
                }
            }
        }
    });
}

// Format seconds as human-readable duration
function formatDuration(seconds) {
    if (!seconds || seconds < 0) return '0m';
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    if (hours > 0) {
        return `${hours}h ${minutes}m`;
    }
    return `${minutes}m`;
}

// Update stats display with goal-based metrics (Rize-inspired layout)
function updateStats(summary, workLife, goals, dateStr) {
    const statsEl = document.getElementById('detailStats');

    // Get goal settings
    const goalHours = goals.daily_work_hours || 8;
    const weekdayGoalsOnly = goals.weekday_goals_only !== false;

    // Determine if this is a weekday (Mon=1 to Fri=5)
    // Parse date as local time to avoid timezone issues (new Date("YYYY-MM-DD") parses as UTC)
    const [year, month, day] = dateStr.split('-').map(Number);
    const selectedDate = new Date(year, month - 1, day);
    const dayOfWeek = selectedDate.getDay();
    const isWeekday = dayOfWeek >= 1 && dayOfWeek <= 5;
    const showGoal = !weekdayGoalsOnly || isWeekday;

    // Calculate work hours and goal percentage
    const activeSeconds = workLife.active_seconds || 0;
    const activeHours = activeSeconds / 3600;
    const goalPercent = showGoal ? Math.min((activeHours / goalHours) * 100, 125) : 0;
    const progressWidth = Math.min(goalPercent, 100);

    // Format work hours (e.g., "6h 30m")
    const workHours = formatDuration(activeSeconds);
    const percentDisplay = showGoal ? `${Math.round(goalPercent)}% of ${goalHours}h` : '';

    // Day span times
    const startTime = workLife.first_session_start
        ? new Date(workLife.first_session_start).toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'})
        : (summary.first_capture ? new Date(summary.first_capture * 1000).toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'}) : '--');
    const endTime = workLife.last_session_end
        ? new Date(workLife.last_session_end).toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'})
        : (summary.last_capture ? new Date(summary.last_capture * 1000).toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'}) : '--');

    // Break metrics
    const breakCount = workLife.break_count || 0;
    const breakSeconds = workLife.break_seconds || 0;
    const breakTime = formatDuration(breakSeconds);

    // Longest focus
    const longestFocusSeconds = workLife.longest_work_without_break_seconds || 0;
    const longestFocus = formatDuration(longestFocusSeconds);
    const focusStart = workLife.longest_work_without_break_start || '';
    const focusEnd = workLife.longest_work_without_break_end || '';

    // Calculate breakdown percentages for stacked bar
    const totalTime = activeSeconds + breakSeconds;
    const focusPercent = totalTime > 0 ? (activeSeconds / totalTime) * 100 : 100;
    const breakPercent = totalTime > 0 ? (breakSeconds / totalTime) * 100 : 0;

    // "Since Last Break" calculation for today
    const today = new Date().toISOString().slice(0, 10);
    const isToday = dateStr === today;
    const isActive = workLife.is_active || false;
    const lastBreakEnd = workLife.last_break_end;
    const firstSessionStart = workLife.first_session_start;
    let sinceLastBreakHtml = '';

    if (isToday && isActive) {
        // Use last break end if available, otherwise use session start (no breaks yet)
        const referenceTime = lastBreakEnd || firstSessionStart;
        if (referenceTime) {
            const refTime = new Date(referenceTime);
            const now = new Date();
            const sinceBreakSeconds = Math.floor((now - refTime) / 1000);
            const sinceBreakDuration = formatDuration(sinceBreakSeconds);

            // Determine urgency color class
            const sinceBreakMinutes = sinceBreakSeconds / 60;
            let urgencyClass = 'urgency-low';
            if (sinceBreakMinutes >= 120) {
                urgencyClass = 'urgency-high';
            } else if (sinceBreakMinutes >= 60) {
                urgencyClass = 'urgency-medium';
            }

            const label = lastBreakEnd ? 'Since Last Break' : 'Current Focus';
            sinceLastBreakHtml = `
                <div class="secondary-stat since-break" id="sinceBreakStat" data-last-break="${referenceTime}">
                    <span class="secondary-stat-label">${label}</span>
                    <span class="secondary-stat-value ${urgencyClass}" id="sinceBreakValue">${sinceBreakDuration}</span>
                </div>
            `;
        }
    }

    const noGoalClass = showGoal ? '' : 'no-goal';

    statsEl.innerHTML = `
        <div class="daily-summary ${noGoalClass}">
            <div class="daily-summary-stats">
                <!-- Primary Stats -->
                <div class="primary-stats">
                    <div class="primary-stat hours-worked">
                        <span class="primary-stat-label">Hours Worked</span>
                        <span class="primary-stat-value">${workHours}</span>
                    </div>
                    ${showGoal ? `
                    <div class="primary-stat goal-percent">
                        <span class="primary-stat-label">${Math.round(goalPercent)}%</span>
                        <span class="primary-stat-value${goalPercent > 100 ? ' exceeded' : ''}">${goalHours}h goal</span>
                    </div>
                    ` : ''}
                </div>

                <!-- Goal Progress Bar -->
                ${showGoal ? `
                <div class="goal-progress">
                    <div class="goal-progress-fill" style="width: ${progressWidth}%"></div>
                </div>
                ` : ''}

                <!-- Secondary Stats -->
                <div class="secondary-stats">
                    <div class="secondary-stat">
                        <span class="secondary-stat-label">${isToday ? 'Start' : 'Day Span'}</span>
                        <span class="secondary-stat-value">${isToday ? startTime : `${startTime} – ${endTime}`}</span>
                    </div>
                    <div class="secondary-stat">
                        <span class="secondary-stat-label">Breaks</span>
                        <span class="secondary-stat-value">${breakCount} (${breakTime})</span>
                    </div>
                    <div class="secondary-stat stat-focus-row">
                        <span class="secondary-stat-label">Longest Focus</span>
                        <span class="secondary-stat-value clickable"
                              data-focus-start="${focusStart}"
                              data-focus-end="${focusEnd}"
                              title="Click to jump to longest focus period">${longestFocus}</span>
                    </div>
                    ${sinceLastBreakHtml}
                </div>
            </div>

            <div class="daily-summary-breakdown">
                <div class="breakdown-header">Breakdown</div>
                <!-- Stacked Bar Chart -->
                <div class="stacked-bar">
                    <div class="stacked-bar-segment focus" style="width: ${focusPercent}%">
                        <span class="stacked-bar-label">${focusPercent >= 15 ? Math.round(focusPercent) + '%' : ''}</span>
                    </div>
                    <div class="stacked-bar-segment breaks" style="width: ${breakPercent}%">
                        <span class="stacked-bar-label">${breakPercent >= 15 ? Math.round(breakPercent) + '%' : ''}</span>
                    </div>
                </div>
                <!-- Legend -->
                <div class="breakdown-legend">
                    <div class="legend-item">
                        <span class="legend-dot focus"></span>
                        <span class="legend-label">Focus</span>
                        <span class="legend-value">${formatDuration(activeSeconds)}</span>
                    </div>
                    <div class="legend-item">
                        <span class="legend-dot breaks"></span>
                        <span class="legend-label">Breaks</span>
                        <span class="legend-value">${formatDuration(breakSeconds)}</span>
                    </div>
                </div>
            </div>
        </div>
    `;

    // Add click handler for longest focus
    const focusValue = statsEl.querySelector('.stat-focus-row .secondary-stat-value.clickable');
    if (focusValue && focusStart && focusEnd) {
        focusValue.addEventListener('click', () => {
            scrollToTimeRange(focusStart, focusEnd);
        });
    }

    // Start live timer for "Since Last Break" / "Current Focus" if showing today
    if (isToday && isActive) {
        const referenceTime = lastBreakEnd || firstSessionStart;
        if (referenceTime) {
            startSinceBreakTimer(referenceTime);
        }
    }
}

// Timer interval for "Since Last Break"
let sinceBreakTimerInterval = null;

// Start live timer for "Since Last Break"
function startSinceBreakTimer(lastBreakEnd) {
    // Clear any existing timer
    if (sinceBreakTimerInterval) {
        clearInterval(sinceBreakTimerInterval);
    }

    const updateTimer = () => {
        const valueEl = document.getElementById('sinceBreakValue');
        if (!valueEl) {
            clearInterval(sinceBreakTimerInterval);
            return;
        }

        const lastBreakTime = new Date(lastBreakEnd);
        const now = new Date();
        const sinceBreakSeconds = Math.floor((now - lastBreakTime) / 1000);
        const sinceBreakDuration = formatDuration(sinceBreakSeconds);

        // Update urgency color
        const sinceBreakMinutes = sinceBreakSeconds / 60;
        valueEl.classList.remove('urgency-low', 'urgency-medium', 'urgency-high');
        if (sinceBreakMinutes >= 120) {
            valueEl.classList.add('urgency-high');
        } else if (sinceBreakMinutes >= 60) {
            valueEl.classList.add('urgency-medium');
        } else {
            valueEl.classList.add('urgency-low');
        }

        valueEl.textContent = sinceBreakDuration;
    };

    // Update every 60 seconds
    sinceBreakTimerInterval = setInterval(updateTimer, 60000);
}

// Scroll timeline to a specific time range and highlight it
function scrollToTimeRange(startTime, endTime) {
    if (!startTime || !endTime) return;

    const startDate = new Date(startTime);
    const startHour = startDate.getHours();

    // Highlight the time range on the horizontal timeline
    highlightTimelineRange(startTime, endTime);

    // Scroll to the hour section in summaries
    scrollToHourSection(startHour);
}

// Highlight a time range on the horizontal timeline
function highlightTimelineRange(startTime, endTime) {
    const timeline = document.querySelector('.horizontal-timeline');
    if (!timeline) return;

    // Remove any existing highlights
    timeline.querySelectorAll('.timeline-highlight').forEach(el => el.remove());

    const startDate = new Date(startTime);
    const endDate = new Date(endTime);

    // Calculate position as percentage of day
    const dayStart = new Date(startDate);
    dayStart.setHours(0, 0, 0, 0);

    const startPercent = ((startDate - dayStart) / (24 * 60 * 60 * 1000)) * 100;
    const endPercent = ((endDate - dayStart) / (24 * 60 * 60 * 1000)) * 100;

    // Create highlight overlay
    const highlight = document.createElement('div');
    highlight.className = 'timeline-highlight';
    highlight.style.left = `${startPercent}%`;
    highlight.style.width = `${endPercent - startPercent}%`;

    // Find the activity lane content to append to
    const laneContent = timeline.querySelector('.timeline-lane[data-lane="activity"] .lane-content');
    if (laneContent) {
        laneContent.style.position = 'relative';
        laneContent.appendChild(highlight);

        // Auto-remove highlight after 3 seconds
        setTimeout(() => {
            highlight.classList.add('fade-out');
            setTimeout(() => highlight.remove(), 500);
        }, 3000);
    }
}

// Render hourly chart and all screenshots
async function renderHourlyChart(hourlyData, aiSummaryData) {
    const detailContent = document.getElementById('detailContent');
    const leftChartSection = document.getElementById('leftChartSection');

    // Show left chart section
    leftChartSection.style.display = 'block';

    // Calculate summary coverage
    const hoursWithScreenshots = aiSummaryData?.summaries?.length || 0;
    const hoursSummarized = Object.keys(state.summaries).length;
    const dailySummaryText = buildDailySummary();

    // Set up daily summary and screenshot grid in right panel
    detailContent.innerHTML = `
        <div class="daily-summary" id="dailySummary">
            <div class="daily-summary-header">
                <div class="daily-summary-title">
                    <span>✨</span>
                    <span>Daily Summary</span>
                </div>
                <div style="display: flex; align-items: center; gap: 10px;">
                    <span class="summary-coverage" id="summaryCoverage">${hoursSummarized} of ${hoursWithScreenshots} hours summarized</span>
                    <button class="generate-btn" id="generateAllBtn" ${hoursSummarized === hoursWithScreenshots ? 'style="display:none"' : ''}>
                        Generate All
                    </button>
                </div>
            </div>
            <div class="daily-summary-content" id="dailySummaryContent">
                ${dailySummaryText || '<span class="summary-placeholder">No summaries generated yet. Click "Generate All" to create AI summaries for each hour.</span>'}
            </div>
        </div>
        <div class="hour-groups" id="hourGroups">
            <div class="loading"><div class="spinner"></div><div>Loading screenshots...</div></div>
        </div>
    `;

    const ctx = document.getElementById('hourlyChart').getContext('2d');

    // Destroy previous chart
    if (state.chart) {
        state.chart.destroy();
    }

    // Prepare data
    const labels = hourlyData.map(h => `${h.hour}:00`);
    const data = hourlyData.map(h => h.screenshot_count);
    const theme = getThemeColors();

    // Create chart
    state.chart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [{
                label: 'Screenshots',
                data: data,
                backgroundColor: theme.accent,
                borderColor: theme.accentStrong,
                borderWidth: 1,
                hoverBackgroundColor: theme.accentSoft,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                y: {
                    beginAtZero: true,
                    ticks: {
                        stepSize: 1,
                        color: theme.muted
                    },
                    grid: {
                        color: theme.border
                    }
                },
                x: {
                    ticks: {
                        color: theme.muted
                    },
                    grid: {
                        color: theme.border
                    }
                }
            },
            plugins: {
                legend: {
                    display: false
                },
                tooltip: {
                    backgroundColor: theme.surface,
                    borderColor: theme.border,
                    borderWidth: 1,
                    titleColor: theme.text,
                    bodyColor: theme.text,
                    callbacks: {
                        afterBody: function(context) {
                            const hour = context[0].dataIndex;
                            const apps = hourlyData[hour].app_breakdown;
                            if (Object.keys(apps).length === 0) return '';

                            const appList = Object.entries(apps)
                                .sort((a, b) => b[1] - a[1])
                                .slice(0, 3)
                                .map(([app, count]) => `${app}: ${count}`)
                                .join('\n');

                            return '\n' + appList;
                        }
                    }
                }
            },
            onClick: (event, elements) => {
                if (elements.length > 0) {
                    const hour = elements[0].index;
                    scrollToHour(hour);
                }
            }
        }
    });

    // Load all screenshots for the day
    await loadAllScreenshots();
}

// Scroll to a specific hour group
function scrollToHour(hour) {
    const hourGroup = document.querySelector(`[data-hour="${hour}"]`);
    if (hourGroup) {
        hourGroup.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        // Expand the hour if collapsed
        hourGroup.classList.remove('collapsed');
    }
}

// Load all screenshots for the selected day
async function loadAllScreenshots() {
    const hourGroupsContainer = document.getElementById('hourGroups');

    try {
        const response = await fetch(`/api/day/${state.selectedDate}/screenshots`);
        const data = await response.json();

        if (data.error) {
            throw new Error(data.error);
        }

        if (data.screenshots.length === 0) {
            hourGroupsContainer.innerHTML = '<div class="empty-state"><p>No screenshots for this day</p></div>';
            return;
        }

        // Group screenshots by hour
        const hourlyGroups = {};
        data.screenshots.forEach(screenshot => {
            const hour = new Date(screenshot.timestamp * 1000).getHours();
            if (!hourlyGroups[hour]) {
                hourlyGroups[hour] = [];
            }
            hourlyGroups[hour].push(screenshot);
        });

        // Render hour groups
        hourGroupsContainer.innerHTML = '';
        const sortedHours = Object.keys(hourlyGroups).sort((a, b) => a - b);

        sortedHours.forEach(hour => {
            const screenshots = hourlyGroups[hour];
            const hourStr = `${hour}:00`;
            const hourEnd = `${(parseInt(hour) + 1)}:00`;
            const hourSummary = state.summaries[hour];

            const groupDiv = document.createElement('div');
            groupDiv.className = 'hour-group';
            groupDiv.dataset.hour = hour;

            const headerDiv = document.createElement('button');
            headerDiv.type = 'button';
            headerDiv.className = 'hour-header';
            headerDiv.setAttribute('aria-expanded', 'true');
            headerDiv.setAttribute('aria-controls', `hour-content-${hour}`);
            headerDiv.innerHTML = `
                <div class="hour-title-wrapper">
                    <span class="hour-title">${hourStr} - ${hourEnd}</span>
                    ${hourSummary ? '<span class="summary-badge" aria-hidden="true">✨</span>' : ''}
                </div>
                <span class="hour-count">${screenshots.length} screenshot${screenshots.length !== 1 ? 's' : ''}</span>
                <span class="hour-toggle" aria-hidden="true">▼</span>
            `;
            headerDiv.onclick = () => {
                const isCollapsed = groupDiv.classList.toggle('collapsed');
                headerDiv.setAttribute('aria-expanded', !isCollapsed);
            };

            // Summary section
            const summaryDiv = document.createElement('div');
            summaryDiv.className = 'summary-box';
            summaryDiv.style.margin = '15px 15px 0 15px';
            if (hourSummary) {
                summaryDiv.innerHTML = `
                    <span class="summary-icon">✨</span>
                    <span class="summary-text">${hourSummary}</span>
                `;
            } else {
                summaryDiv.innerHTML = `
                    <span class="summary-icon" style="opacity: 0.5;">💬</span>
                    <span class="summary-text summary-placeholder">No summary</span>
                    <button class="generate-btn" data-hour="${hour}">Generate</button>
                `;
            }

            const contentDiv = document.createElement('div');
            contentDiv.className = 'hour-content';
            contentDiv.id = `hour-content-${hour}`;

            screenshots.forEach(screenshot => {
                const time = new Date(screenshot.timestamp * 1000).toLocaleTimeString();
                const appName = screenshot.app_name || 'Unknown';

                const card = document.createElement('div');
                card.className = 'screenshot-card';
                card.innerHTML = `
                    <img
                        src="/thumbnail/${screenshot.id}"
                        alt="Screenshot at ${time}"
                        class="screenshot-img"
                        loading="lazy"
                        data-id="${screenshot.id}"
                    >
                    <div class="screenshot-meta">
                        <div class="screenshot-time">${time}</div>
                        <div class="screenshot-app">
                            <span class="app-badge">${appName}</span>
                        </div>
                    </div>
                `;
                contentDiv.appendChild(card);
            });

            groupDiv.appendChild(headerDiv);
            groupDiv.appendChild(summaryDiv);
            groupDiv.appendChild(contentDiv);
            hourGroupsContainer.appendChild(groupDiv);
        });

        // Update default screenshots for modal
        window._timelineDefaultScreenshots = data.screenshots;

    } catch (error) {
        console.error('Failed to load screenshots:', error);
        hourGroupsContainer.innerHTML = '<div class="empty-state"><p>Failed to load screenshots</p></div>';
    }
}

// Timeline screenshot modal helper - wraps ScreenshotModal with viewAllUrl
// This function is used for timeline-specific modal display with "View all from this day" link
function showTimelineScreenshotModal(screenshots, screenshotIdOrIndex, options = {}) {
    if (!screenshots || screenshots.length === 0) return;

    // Compute the viewAllUrl from the first screenshot's date
    const firstScreenshot = screenshots[0];
    let viewAllUrl = null;
    if (firstScreenshot && firstScreenshot.timestamp) {
        const date = new Date(firstScreenshot.timestamp * 1000);
        const dateStr = date.toISOString().split('T')[0];
        viewAllUrl = `/day/${dateStr}`;
    }

    // Use ScreenshotModal with computed options
    ScreenshotModal.show(screenshots, screenshotIdOrIndex, {
        viewAllUrl: viewAllUrl,
        findById: options.findById || false,
        ...options
    });
}

// Global alias for backward compatibility and session clicks
window.showModalWithScreenshots = function(screenshotId, screenshots) {
    showTimelineScreenshotModal(screenshots, screenshotId, { findById: true });
};

// Set up click handlers for screenshot images (only once)
let timelineModalClickHandlerInitialized = false;
function initializeTimelineModalClickHandler(defaultScreenshots) {
    if (timelineModalClickHandlerInitialized) return;
    timelineModalClickHandlerInitialized = true;

    // Store default screenshots for hourly view
    window._timelineDefaultScreenshots = defaultScreenshots || [];

    document.addEventListener('click', (e) => {
        if (e.target.classList.contains('screenshot-img')) {
            const screenshotId = parseInt(e.target.dataset.id);

            // Check if this is a session screenshot
            if (e.target.classList.contains('session-screenshot')) {
                const sessionId = e.target.dataset.sessionId;
                const container = document.getElementById(`session-screenshots-${sessionId}`);
                if (container && container.dataset.screenshots) {
                    const sessionScreenshots = JSON.parse(container.dataset.screenshots);
                    showTimelineScreenshotModal(sessionScreenshots, screenshotId, { findById: true });
                    return;
                }
            }

            // Default to hourly screenshots
            showTimelineScreenshotModal(window._timelineDefaultScreenshots, screenshotId, { findById: true });
        }
    });
}

// Build daily summary from hourly summaries
function buildDailySummary() {
    const hours = Object.keys(state.summaries).sort((a, b) => a - b);
    if (hours.length === 0) return '';

    return hours.map(hour => {
        const hourStr = `${hour}:00`;
        return `<strong>${hourStr}</strong>: ${state.summaries[hour]}`;
    }).join('<br><br>');
}

// Generate summaries for specified hours (or all unsummarized)
async function generateSummaries(hours = null) {
    if (!state.selectedDate) return;

    const btn = document.getElementById('generateAllBtn');
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-small"></span> Generating...';
    }

    try {
        const body = { date: state.selectedDate };
        if (hours) {
            body.hours = hours;
        }

        const response = await fetch('/api/summaries/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });

        const data = await response.json();

        if (data.status === 'started') {
            // Start polling for generation status
            startGenerationPolling();
        } else if (data.status === 'nothing_to_do') {
            if (btn) {
                btn.style.display = 'none';
            }
        } else if (data.status === 'already_running') {
            // Already running, start polling
            startGenerationPolling();
        }
    } catch (error) {
        console.error('Failed to start summarization:', error);
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = 'Generate All';
        }
    }
}

// Poll for generation status (distinct from summarization status polling)
function startGenerationPolling() {
    if (state.pollInterval) {
        clearInterval(state.pollInterval);
    }

    state.pollInterval = setInterval(async () => {
        try {
            const response = await fetch('/api/summaries/generate/status');
            const status = await response.json();

            updateGenerationProgress(status);

            if (!status.running) {
                clearInterval(state.pollInterval);
                state.pollInterval = null;

                // Refresh data to show new summaries
                if (state.selectedDate) {
                    await loadDayData(state.selectedDate);
                }
            }
        } catch (error) {
            console.error('Failed to poll status:', error);
            clearInterval(state.pollInterval);
            state.pollInterval = null;
        }
    }, 2000);
}

// Update UI with generation progress
function updateGenerationProgress(status) {
    const btn = document.getElementById('generateAllBtn');
    const coverage = document.getElementById('summaryCoverage');

    if (status.running) {
        if (btn) {
            btn.disabled = true;
            const hourStr = status.current_hour !== null ? `${status.current_hour}:00` : '';
            btn.innerHTML = `<span class="spinner-small"></span> ${status.completed}/${status.total} ${hourStr}`;
        }
    } else {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = 'Generate All';

            // Hide if all done
            if (status.error === null && status.completed === status.total) {
                btn.style.display = 'none';
            }
        }

        if (status.error) {
            console.error('Summarization error:', status.error);
        }
    }
}

// Refresh summaries after generation
async function refreshSummaries() {
    if (!state.selectedDate) return;

    try {
        const response = await fetch(`/api/summaries/${state.selectedDate}`);
        const data = await response.json();

        if (data.summaries) {
            state.summaries = {};
            data.summaries.forEach(s => {
                if (s.summary) {
                    state.summaries[s.hour] = s.summary;
                }
            });

            // Update daily summary content
            const dailySummaryContent = document.getElementById('dailySummaryContent');
            if (dailySummaryContent) {
                const text = buildDailySummary();
                dailySummaryContent.innerHTML = text || '<span class="summary-placeholder">No summaries generated yet.</span>';
            }

            // Update coverage
            const coverage = document.getElementById('summaryCoverage');
            if (coverage) {
                const total = data.summaries.length;
                const summarized = Object.keys(state.summaries).length;
                coverage.textContent = `${summarized} of ${total} hours summarized`;
            }
        }
    } catch (error) {
        console.error('Failed to refresh summaries:', error);
    }
}

// =====================================================================
// Session-based display functions
// =====================================================================

// Toggle summary details visibility
function toggleSummaryDetails(sessionId) {
    const details = document.getElementById(`summary-details-${sessionId}`);
    if (details) {
        details.classList.toggle('visible');
    }
}

// Toggle prompt details visibility
function togglePromptDetails(sessionId) {
    const details = document.getElementById(`prompt-details-${sessionId}`);
    if (details) {
        details.classList.toggle('visible');
    }
}

// Toggle hour group collapse/expand
function toggleHourGroup(hour) {
    const group = document.querySelector(`.hour-group[data-hour="${hour}"]`);
    if (group) {
        group.classList.toggle('collapsed');
    }
}

// ==================== Table Selection ====================

// Handle row click - navigate to detail page
function handleRowClick(event, summaryId) {
    // Navigate to detail page
    window.location.href = `/summary/${summaryId}`;
}

// Handle checkbox click with shift+click support
function handleCheckboxClick(event, summaryId) {
    event.stopPropagation();
    const checkbox = event.target;
    const row = checkbox.closest('tr');
    const allCheckboxes = Array.from(document.querySelectorAll('.summaries-table tbody .summary-checkbox'));
    const currentIndex = allCheckboxes.indexOf(checkbox);

    // Shift+click for range selection
    if (event.shiftKey && state.lastClickedCheckboxIndex !== null && state.lastClickedCheckboxIndex !== currentIndex) {
        const start = Math.min(state.lastClickedCheckboxIndex, currentIndex);
        const end = Math.max(state.lastClickedCheckboxIndex, currentIndex);
        const shouldSelect = checkbox.checked;

        for (let i = start; i <= end; i++) {
            const cb = allCheckboxes[i];
            const cbRow = cb.closest('tr');
            const cbId = parseInt(cb.dataset.id);

            cb.checked = shouldSelect;
            if (shouldSelect) {
                state.selectedSummaries.add(cbId);
                cbRow.classList.add('selected');
            } else {
                state.selectedSummaries.delete(cbId);
                cbRow.classList.remove('selected');
            }
        }
    } else {
        // Normal click
        if (checkbox.checked) {
            state.selectedSummaries.add(summaryId);
            row.classList.add('selected');
        } else {
            state.selectedSummaries.delete(summaryId);
            row.classList.remove('selected');
        }
    }

    state.lastClickedCheckboxIndex = currentIndex;
    updateBulkActionBar();
}

// Toggle select all
function toggleSelectAll(event) {
    event.stopPropagation();
    const selectAll = event.target.checked;
    const checkboxes = document.querySelectorAll('.summaries-table tbody .summary-checkbox');

    checkboxes.forEach(checkbox => {
        const summaryId = parseInt(checkbox.dataset.id);
        const row = checkbox.closest('tr');

        checkbox.checked = selectAll;
        if (selectAll) {
            state.selectedSummaries.add(summaryId);
            row.classList.add('selected');
        } else {
            state.selectedSummaries.delete(summaryId);
            row.classList.remove('selected');
        }
    });

    updateBulkActionBar();
}

// Clear selection
function clearSelection() {
    state.selectedSummaries.clear();
    document.querySelectorAll('.summaries-table tbody tr').forEach(row => {
        row.classList.remove('selected');
    });
    document.querySelectorAll('.summaries-table .summary-checkbox').forEach(cb => {
        cb.checked = false;
    });
    updateBulkActionBar();
}

// Update bulk action bar visibility
function updateBulkActionBar() {
    const bar = document.getElementById('bulkActionBar');
    const countEl = document.getElementById('selectedCount');
    const count = state.selectedSummaries.size;

    if (count > 0) {
        bar.classList.add('visible');
        countEl.textContent = count;
    } else {
        bar.classList.remove('visible');
    }

    // Update select all checkbox
    const selectAllCheckbox = document.getElementById('selectAllCheckbox');
    const totalRows = document.querySelectorAll('.summaries-table tbody tr').length;
    if (selectAllCheckbox) {
        selectAllCheckbox.checked = count === totalRows && totalRows > 0;
        selectAllCheckbox.indeterminate = count > 0 && count < totalRows;
    }
}

// Bulk regenerate selected summaries
async function bulkRegenerate() {
    const ids = Array.from(state.selectedSummaries);
    if (ids.length === 0) return;

    if (!confirm(`Regenerate ${ids.length} selected ${ids.length === 1 ? 'summary' : 'summaries'}?`)) {
        return;
    }

    // Queue each for regeneration
    for (const id of ids) {
        try {
            await fetch(`/api/threshold-summaries/${id}/regenerate`, { method: 'POST' });
        } catch (error) {
            console.error(`Failed to queue regeneration for ${id}:`, error);
        }
    }

    showToast(`Queued ${ids.length} ${ids.length === 1 ? 'summary' : 'summaries'} for regeneration.`, 'success');
    clearSelection();

    // Reload after a delay
    setTimeout(() => {
        if (state.selectedDate) {
            loadDayData(state.selectedDate);
        }
    }, 3000);
}

// Bulk delete selected summaries
async function bulkDelete() {
    const ids = Array.from(state.selectedSummaries);
    if (ids.length === 0) return;

    if (!confirm(`Delete ${ids.length} selected ${ids.length === 1 ? 'summary' : 'summaries'}? This cannot be undone.`)) {
        return;
    }

    let deleted = 0;
    for (const id of ids) {
        try {
            const response = await fetch(`/api/threshold-summaries/${id}`, { method: 'DELETE' });
            if (response.ok) deleted++;
        } catch (error) {
            console.error(`Failed to delete ${id}:`, error);
        }
    }

    showToast(`Deleted ${deleted} of ${ids.length} ${ids.length === 1 ? 'summary' : 'summaries'}.`, 'success');
    clearSelection();

    // Reload
    if (state.selectedDate) {
        loadDayData(state.selectedDate);
    }
}

// ==================== Threshold Summaries ====================

// Format hour as AM/PM
function formatHourAMPM(hour) {
    const ampm = hour >= 12 ? 'PM' : 'AM';
    const h = hour % 12 || 12;
    return `${h}:00 ${ampm}`;
}

// Render the threshold summaries panel with hourly sections
// Calculate active time within a time range from sessions
function getActiveTimeInRange(startTime, endTime) {
    const sessions = state.timeline?.sessions || [];
    const rangeStart = new Date(startTime).getTime();
    const rangeEnd = new Date(endTime).getTime();

    let activeMs = 0;
    sessions.forEach(session => {
        const sessStart = new Date(session.start_time).getTime();
        const sessEnd = session.end_time
            ? new Date(session.end_time).getTime()
            : sessStart + (session.duration_minutes * 60 * 1000);

        // Check for overlap
        if (sessEnd > rangeStart && sessStart < rangeEnd) {
            const overlapStart = Math.max(sessStart, rangeStart);
            const overlapEnd = Math.min(sessEnd, rangeEnd);
            activeMs += (overlapEnd - overlapStart);
        }
    });

    return activeMs;
}

function renderThresholdSummaries() {
    const summaries = state.thresholdSummaries || [];
    const hourlyData = state.hourlyData || [];

    // Find or create the threshold summaries container
    let container = document.getElementById('thresholdSummariesPanel');
    if (!container) {
        const detailContent = document.getElementById('detailContent');
        container = document.createElement('div');
        container.id = 'thresholdSummariesPanel';
        container.className = 'threshold-summaries-panel';
        detailContent.appendChild(container);
    }

    // Group summaries by hour
    const summaryGroups = {};
    summaries.forEach(summary => {
        const startDate = new Date(summary.start_time);
        const hour = startDate.getHours();
        if (!summaryGroups[hour]) {
            summaryGroups[hour] = [];
        }
        summaryGroups[hour].push(summary);
    });

    // Get all hours with activity from hourlyData
    const hoursWithActivity = hourlyData
        .filter(h => h.screenshot_count > 0)
        .map(h => ({ hour: h.hour, count: h.screenshot_count }))
        .sort((a, b) => a.hour - b.hour);

    if (hoursWithActivity.length === 0) {
        container.innerHTML = `
            <div class="no-summaries">
                No activity recorded for this day.
            </div>
        `;
        return;
    }

    // Build the HTML
    let html = `
        <div class="bulk-action-bar" id="bulkActionBar">
            <span class="selected-count"><span id="selectedCount">0</span> selected</span>
            <div class="bulk-actions">
                <button class="btn-bulk" id="bulkRegenerateBtn">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 4v6h-6M1 20v-6h6"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>
                    Regenerate
                </button>
                <button class="btn-bulk danger" id="bulkDeleteBtn">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                    Delete
                </button>
                <button class="btn-bulk btn-cancel" id="bulkCancelBtn">Cancel</button>
            </div>
        </div>
    `;

    // Table with hourly sections
    html += `
        <div class="summaries-table-wrapper">
            <table class="summaries-table">
                <thead>
                    <tr>
                        <th style="width: 32px;"><input type="checkbox" class="summary-checkbox" id="selectAllCheckbox"></th>
                        <th>Time</th>
                        <th>Duration</th>
                        <th style="width: 100%;">Summary</th>
                        <th>Conf</th>
                    </tr>
                </thead>
                <tbody>
    `;

    // Only show hours that have summaries (skip empty hours)
    hoursWithActivity.forEach(({ hour, count }) => {
        const hourSummaries = summaryGroups[hour] || [];
        if (hourSummaries.length === 0) return; // Skip hours with no summaries

        // Summaries for this hour
        hourSummaries.forEach(summary => {
            const startDate = new Date(summary.start_time);
            const endDate = new Date(summary.end_time);
            const startTime = startDate.toLocaleTimeString([], {hour: 'numeric', minute:'2-digit', hour12: true});

            // Calculate active time from sessions overlapping this summary's time range
            const activeMs = getActiveTimeInRange(summary.start_time, summary.end_time);
            const activeMins = Math.round(activeMs / 60000);
            let durationStr;
            if (activeMins < 1) {
                durationStr = '<1m';
            } else if (activeMins < 60) {
                durationStr = `${activeMins}m`;
            } else {
                const hours = Math.floor(activeMins / 60);
                const mins = activeMins % 60;
                durationStr = mins > 0 ? `${hours}h${mins}m` : `${hours}h`;
            }

            // Confidence indicator
            const conf = summary.confidence;
            let confClass = 'conf-medium';
            let confTitle = 'Moderate confidence';
            if (conf >= 0.8) {
                confClass = 'conf-high';
                confTitle = 'High confidence';
            } else if (conf < 0.5) {
                confClass = 'conf-low';
                confTitle = 'Low confidence';
            }
            const confDisplay = conf !== null && conf !== undefined ? `<span class="confidence-badge ${confClass}" title="${confTitle}: ${(conf * 100).toFixed(0)}%">${(conf * 100).toFixed(0)}%</span>` : '';

            // Explanation tooltip (escape quotes for HTML attribute)
            const explanationEscaped = (summary.explanation || '').replace(/"/g, '&quot;').replace(/'/g, '&#39;');

            // Tags for data attribute (used by tags section for filtering)
            const tags = summary.tags || [];

            // Preview indicator for active session summaries
            const isPreview = summary.is_preview;
            const previewBadge = isPreview ? '<span class="preview-badge" title="Live preview of current session - updates periodically">Current Session</span>' : '';
            const previewClass = isPreview ? ' preview-row' : '';

            html += `
                <tr data-summary-id="${summary.id}" data-hour="${hour}" data-tags="${tags.join(',')}"${isPreview ? ' data-preview="true"' : ''} class="${previewClass}">
                    <td class="checkbox-cell">
                        <input type="checkbox" class="summary-checkbox" data-id="${summary.id}"${isPreview ? ' disabled title="Cannot select preview summaries"' : ''}>
                    </td>
                    <td class="summary-time">${startTime}${previewBadge}</td>
                    <td class="summary-duration">
                        <span class="duration-display" title="${activeMins} minutes active">${durationStr}</span>
                    </td>
                    <td class="summary-text-cell">
                        <span title="${summary.summary}">${summary.summary}</span>
                        ${summary.explanation ? `<span class="explanation-icon" data-explanation="${explanationEscaped}" title="Show explanation">ℹ️</span>` : ''}
                    </td>
                    <td class="summary-confidence">${confDisplay}</td>
                </tr>
            `;
        });
    });

    html += `
                </tbody>
            </table>
        </div>
    `;

    container.innerHTML = html;
    state.selectedSummaries = new Set();

    // Set up scroll observer for chart highlighting
    setupScrollObserver();

    // Track current highlighted hour
    state.highlightedHour = null;

    // Render tags section
    renderTagsSection(summaries);
}

// Render the tags section below the hourly chart
function renderTagsSection(summaries) {
    const tagsSection = document.getElementById('tagsSection');
    const tagsList = document.getElementById('tagsList');

    // Aggregate all tags with counts
    const tagCounts = {};
    summaries.forEach(s => {
        (s.tags || []).forEach(tag => {
            tagCounts[tag] = (tagCounts[tag] || 0) + 1;
        });
    });

    // Sort by count (descending), then alphabetically
    const sortedTags = Object.entries(tagCounts)
        .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));

    if (sortedTags.length === 0) {
        tagsSection.style.display = 'none';
        return;
    }

    tagsSection.style.display = 'block';
    tagsList.innerHTML = sortedTags.map(([tag, count]) =>
        `<span class="tag-badge" data-tag="${escapeHtml(tag)}">
            ${escapeHtml(tag)}<span class="tag-count">${count}</span>
        </span>`
    ).join('');

    // Store for filtering
    state.activeTag = null;
}

// Toggle tag filter on click
function toggleTagFilter(tag) {
    const badges = document.querySelectorAll('.tag-badge');
    const rows = document.querySelectorAll('.summaries-table tbody tr[data-summary-id]');

    if (state.activeTag === tag) {
        // Deselect - show all rows
        state.activeTag = null;
        badges.forEach(b => b.classList.remove('active'));
        rows.forEach(r => r.classList.remove('tag-dimmed', 'tag-highlight'));
    } else {
        // Select this tag - filter rows
        state.activeTag = tag;
        badges.forEach(b => b.classList.toggle('active', b.dataset.tag === tag));
        rows.forEach(r => {
            const rowTags = (r.dataset.tags || '').split(',').filter(t => t);
            if (rowTags.includes(tag)) {
                r.classList.add('tag-highlight');
                r.classList.remove('tag-dimmed');
            } else {
                r.classList.add('tag-dimmed');
                r.classList.remove('tag-highlight');
            }
        });
    }
}

// Highlight rows on hover (only if no active filter)
function highlightTagRows(tag) {
    if (state.activeTag) return; // Don't interfere with active filter
    const rows = document.querySelectorAll('.summaries-table tbody tr[data-summary-id]');
    rows.forEach(r => {
        const rowTags = (r.dataset.tags || '').split(',').filter(t => t);
        if (rowTags.includes(tag)) {
            r.classList.add('tag-highlight');
        }
    });
}

// Clear hover highlight
function clearTagHighlight() {
    if (state.activeTag) return; // Don't interfere with active filter
    const rows = document.querySelectorAll('.summaries-table tbody tr[data-summary-id]');
    rows.forEach(r => r.classList.remove('tag-highlight'));
}

// View screenshots for an hour without summaries
async function viewHourScreenshots(hour) {
    if (!state.selectedDate) return;

    try {
        const response = await fetch(`/api/screenshots/${state.selectedDate}/${hour}`);
        const data = await response.json();

        if (data.error) {
            showToast('Failed to load screenshots: ' + data.error, 'error');
            return;
        }

        if (data.screenshots && data.screenshots.length > 0) {
            // Use the existing modal system with navigation
            window.showModalWithScreenshots(data.screenshots[0].id, data.screenshots);
        } else {
            showToast('No screenshots for this hour', 'warning');
        }
    } catch (error) {
        console.error('Failed to load screenshots:', error);
    }
}

// Show explanation modal
function showExplanation(explanation) {
    // Unescape HTML entities
    const text = explanation.replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, '&');

    const modal = document.createElement('div');
    modal.className = 'explanation-modal';
    modal.innerHTML = `
        <div class="explanation-modal-content">
            <h3>Model Explanation</h3>
            <p>${text}</p>
            <button class="explanation-close-btn">Close</button>
        </div>
    `;
    modal.addEventListener('click', (e) => {
        if (e.target === modal || e.target.classList.contains('explanation-close-btn')) {
            modal.remove();
        }
    });
    document.body.appendChild(modal);
}

// View screenshots for a specific summary
async function viewSummaryScreenshots(summaryId) {
    try {
        const response = await fetch(`/api/threshold-summaries/${summaryId}`);
        const data = await response.json();

        if (data.error) {
            showToast('Failed to load summary: ' + data.error, 'error');
            return;
        }

        const screenshotIds = data.screenshot_ids || [];
        if (screenshotIds.length === 0) {
            showToast('No screenshots associated with this summary', 'warning');
            return;
        }

        // Fetch screenshot details
        const screenshotsResponse = await fetch(`/api/screenshots/batch?ids=${screenshotIds.join(',')}`);
        const screenshotsData = await screenshotsResponse.json();

        if (screenshotsData.screenshots && screenshotsData.screenshots.length > 0) {
            // Use the existing modal system with navigation
            window.showModalWithScreenshots(screenshotsData.screenshots[0].id, screenshotsData.screenshots);
        } else {
            showToast('No screenshots available', 'warning');
        }
    } catch (error) {
        console.error('Failed to load screenshots:', error);
    }
}

// Set up IntersectionObserver to highlight chart bar on scroll
function setupScrollObserver() {
    const tableWrapper = document.querySelector('.summaries-table-wrapper');
    if (!tableWrapper) return;

    const summaryRows = document.querySelectorAll('.summaries-table tbody tr[data-summary-id]');
    if (summaryRows.length === 0) return;

    // Clean up previous observer
    if (state.scrollObserver) {
        state.scrollObserver.disconnect();
    }

    // Create observer for summary rows
    const observer = new IntersectionObserver((entries) => {
        // Find the topmost visible row
        let topmostEntry = null;
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                if (!topmostEntry || entry.boundingClientRect.top < topmostEntry.boundingClientRect.top) {
                    topmostEntry = entry;
                }
            }
        });

        if (topmostEntry) {
            const hour = parseInt(topmostEntry.target.dataset.hour);
            if (state.highlightedHour !== hour) {
                state.highlightedHour = hour;
                highlightChartBar(hour);
            }
        }
    }, {
        root: tableWrapper,
        rootMargin: '0px 0px -80% 0px',
        threshold: 0.1
    });

    summaryRows.forEach(row => observer.observe(row));

    // Store observer for cleanup
    state.scrollObserver = observer;
}

// Highlight a bar in the hourly chart
function highlightChartBar(hour) {
    if (!state.chart) return;

    // Find the index for this hour
    const hourIndex = state.chart.data.labels.findIndex(
        label => parseInt(label) === hour
    );

    if (hourIndex === -1) return;

    // Create color arrays - highlighted bar is brighter, others are dimmed
    const colors = state.chart.data.labels.map((_, i) => {
        if (i === hourIndex) {
            return 'rgba(88, 166, 255, 1)';  // Bright blue for highlighted
        }
        return 'rgba(88, 166, 255, 0.4)';  // Dimmed for others
    });

    const borderColors = state.chart.data.labels.map((_, i) => {
        if (i === hourIndex) {
            return 'rgba(31, 111, 235, 1)';  // Strong border for highlighted
        }
        return 'rgba(31, 111, 235, 0.3)';  // Dimmed border for others
    });

    state.chart.data.datasets[0].backgroundColor = colors;
    state.chart.data.datasets[0].borderColor = borderColors;
    state.chart.update('none'); // Update without animation
}

// Reset chart bar highlighting
function resetChartHighlight() {
    if (!state.chart) return;

    const theme = getThemeColors();
    state.chart.data.datasets[0].backgroundColor = theme.accent;
    state.chart.data.datasets[0].borderColor = theme.accentStrong;
    state.chart.update('none');
    state.highlightedHour = null;
}

// Trigger summarization of all pending screenshots
async function triggerSummarization() {
    // Disable the generate button while processing
    const generateBtn = document.getElementById('btnGenerateMissing');
    const originalBtnHtml = generateBtn ? generateBtn.innerHTML : '';

    if (generateBtn) {
        generateBtn.disabled = true;
        generateBtn.innerHTML = `
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="spin"><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/></svg>
            Generating...
        `;
    }

    const resetButton = () => {
        if (generateBtn) {
            generateBtn.disabled = false;
            generateBtn.innerHTML = `
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/></svg>
                Generate Missing
            `;
        }
    };

    try {
        // Pass the current timeline date to only generate for that day
        const response = await fetch('/api/threshold-summaries/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ date: state.selectedDate })
        });

        const data = await response.json();

        if (data.error) {
            showToast('Failed to trigger summarization: ' + data.error, 'error');
            resetButton();
            return;
        }

        if (data.status === 'no_pending') {
            showToast(`No unsummarized sessions for ${state.selectedDate}`, 'warning');
            resetButton();
            return;
        }

        // Poll for completion then refresh
        pollForSummarization();
    } catch (error) {
        console.error('Failed to trigger summarization:', error);
        showToast('Failed to trigger summarization', 'error');
        resetButton();
    }
}

// Poll worker status until summarization is complete
async function pollForSummarization() {
    const pollInterval = setInterval(async () => {
        try {
            const response = await fetch('/api/threshold-summaries/worker-status');
            const data = await response.json();

            if (data.queue_size === 0 && !data.current_task) {
                clearInterval(pollInterval);
                // Refresh the view
                if (state.selectedDate) {
                    loadDayData(state.selectedDate);
                }
            }
        } catch (error) {
            console.error('Error polling worker status:', error);
            clearInterval(pollInterval);
        }
    }, 2000);
}

// Scroll to hour section when chart bar is clicked
function scrollToHourSection(hour) {
    // Find first summary row for this hour
    const row = document.querySelector(`.summaries-table tbody tr[data-hour="${hour}"]`);
    if (row) {
        row.scrollIntoView({ behavior: 'smooth', block: 'start' });
        // Immediately highlight the bar
        state.highlightedHour = hour;
        highlightChartBar(hour);
    }
}

// Regenerate a summary with current settings
async function regenerateSummary(summaryId) {
    const btn = document.querySelector(`[data-summary-id="${summaryId}"] .btn-icon:first-child`);
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-small"></span>';
    }

    try {
        const response = await fetch(`/api/threshold-summaries/${summaryId}/regenerate`, {
            method: 'POST'
        });
        const data = await response.json();

        if (data.status === 'queued') {
            // Poll for completion
            pollForSummaryUpdate(summaryId);
        }
    } catch (error) {
        console.error('Failed to regenerate summary:', error);
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 4v6h-6M1 20v-6h6"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>';
        }
    }
}

// Poll for summary update after regeneration
function pollForSummaryUpdate(summaryId) {
    const interval = setInterval(async () => {
        try {
            const response = await fetch('/api/threshold-summaries/worker-status');
            const status = await response.json();

            if (!status.running || status.current_task !== 'regenerate') {
                clearInterval(interval);
                // Reload the day data to show updated summary
                if (state.selectedDate) {
                    await loadDayData(state.selectedDate);
                }
            }
        } catch (error) {
            clearInterval(interval);
        }
    }, 2000);
}

// Show summary version history
async function showSummaryHistory(summaryId) {
    try {
        const response = await fetch(`/api/threshold-summaries/${summaryId}/history`);
        const data = await response.json();

        if (data.error) {
            showToast('Failed to load history: ' + data.error, 'error');
            return;
        }

        // Create modal
        let modal = document.getElementById('summaryHistoryModal');
        if (!modal) {
            modal = document.createElement('div');
            modal.id = 'summaryHistoryModal';
            modal.className = 'modal-overlay';
            document.body.appendChild(modal);
        }

        const versions = data.versions || [];
        let versionsHtml = versions.map((v, idx) => {
            const isLatest = idx === versions.length - 1;
            const date = new Date(v.created_at).toLocaleString();
            return `
                <div class="version-item ${isLatest ? 'current' : ''}">
                    <div class="version-header">
                        <span class="version-date">${date}</span>
                        <span class="version-model">${v.model_used}</span>
                        ${isLatest ? '<span class="current-badge">Latest</span>' : ''}
                    </div>
                    <div class="version-text">${v.summary}</div>
                </div>
            `;
        }).join('');

        modal.innerHTML = `
            <div class="modal-content">
                <div class="modal-header">
                    <h3>Summary History</h3>
                    <button class="modal-close" id="historyModalClose">&times;</button>
                </div>
                <div class="modal-body">
                    ${versions.length > 0 ? versionsHtml : '<p>No version history available.</p>'}
                </div>
                <div class="modal-footer">
                    <button id="historyRegenBtn" data-original-id="${data.original_id}">
                        Regenerate with Current Settings
                    </button>
                    <button class="secondary" id="historyCloseBtn">Close</button>
                </div>
            </div>
        `;

        document.getElementById('historyModalClose').addEventListener('click', closeSummaryHistory);
        document.getElementById('historyCloseBtn').addEventListener('click', closeSummaryHistory);
        document.getElementById('historyRegenBtn').addEventListener('click', function() {
            regenerateSummary(parseInt(this.dataset.originalId));
            closeSummaryHistory();
        });

        modal.style.display = 'flex';
    } catch (error) {
        console.error('Failed to load summary history:', error);
    }
}

// Close summary history modal
function closeSummaryHistory() {
    const modal = document.getElementById('summaryHistoryModal');
    if (modal) {
        modal.style.display = 'none';
    }
}

// Delete a summary
async function deleteSummary(summaryId) {
    if (!confirm('Delete this summary? The screenshots will become unsummarized.')) {
        return;
    }

    try {
        const response = await fetch(`/api/threshold-summaries/${summaryId}`, {
            method: 'DELETE'
        });
        const data = await response.json();

        if (data.status === 'deleted') {
            // Reload the day data
            if (state.selectedDate) {
                await loadDayData(state.selectedDate);
            }
        }
    } catch (error) {
        console.error('Failed to delete summary:', error);
    }
}
