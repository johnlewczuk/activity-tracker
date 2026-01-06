// Summary Detail Page JavaScript

(function() {
    const summaryId = parseInt(document.getElementById('content').dataset.summaryId);
    let data = null;

    document.addEventListener('DOMContentLoaded', () => {
        setupEventListeners();
        loadSummaryDetails();
    });

    function setupEventListeners() {
        document.getElementById('regenerateBtn').addEventListener('click', regenerateSummary);
        document.getElementById('deleteBtn').addEventListener('click', deleteSummary);
    }

    async function loadSummaryDetails() {
        try {
            const response = await fetch(`/api/threshold-summaries/${summaryId}/detail`);
            data = await response.json();

            if (data.error) {
                document.getElementById('content').innerHTML = `
                    <div class="error-state">
                        <h2>Error</h2>
                        <p>${escapeHtml(data.error)}</p>
                        <a href="/timeline" class="back-link">Return to Timeline</a>
                    </div>
                `;
                return;
            }

            renderContent();
        } catch (error) {
            console.error('Failed to load summary:', error);
            document.getElementById('content').innerHTML = `
                <div class="error-state">
                    <h2>Failed to load summary</h2>
                    <p>${escapeHtml(error.message)}</p>
                </div>
            `;
        }
    }

    function renderContent() {
        const summary = data.summary;
        const screenshots = data.screenshots;
        const windowDurations = data.window_durations;
        const activityLog = data.activity_log || [];
        const totalFocusSeconds = data.total_focus_seconds || 0;
        const config = summary.config_snapshot || {};

        const startTime = new Date(summary.start_time);
        const dateStr = startTime.toLocaleDateString('en-US', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' });
        const timeStr = startTime.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});

        const maxDuration = windowDurations.length > 0 ? Math.max(...windowDurations.map(w => w.duration_seconds)) : 1;
        const maxActivityDuration = activityLog.length > 0 ? Math.max(...activityLog.map(e => e.duration_seconds)) : 1;

        let confidenceBadge = '';
        if (summary.confidence != null) {
            const conf = summary.confidence;
            let confClass = 'conf-medium', confLabel = 'Moderate';
            if (conf >= 0.8) { confClass = 'conf-high'; confLabel = 'High'; }
            else if (conf < 0.5) { confClass = 'conf-low'; confLabel = 'Low'; }
            confidenceBadge = `<span class="summary-meta-item"><span class="confidence-badge ${confClass}" title="${confLabel}: ${(conf * 100).toFixed(0)}%">${confLabel} (${(conf * 100).toFixed(0)}%)</span></span>`;
        }

        let explanationSection = '';
        if (summary.explanation) {
            explanationSection = `
                <div class="explanation-section">
                    <div class="explanation-label">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14">
                            <circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>
                        </svg>
                        Model Explanation
                    </div>
                    <div class="explanation-text">${escapeHtml(summary.explanation)}</div>
                </div>
            `;
        }

        let tagsSection = '';
        const tags = summary.tags || [];
        if (tags.length > 0) {
            tagsSection = `
                <div class="tags-section">
                    <span class="tags-label">Tags:</span>
                    ${tags.map(tag => `<span class="tag-badge">${escapeHtml(tag)}</span>`).join('')}
                </div>
            `;
        }

        let html = `
            <div class="summary-card">
                <div class="summary-meta">
                    <span class="summary-meta-item">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
                        ${dateStr}
                    </span>
                    <span class="summary-meta-item">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
                        ${timeStr}
                    </span>
                    <span class="summary-meta-item">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/></svg>
                        ${data.duration_minutes}m duration
                    </span>
                    <span class="summary-meta-item">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>
                        ${screenshots.length} screenshots
                    </span>
                    ${confidenceBadge}
                </div>
                <div class="summary-text">${escapeHtml(summary.summary)}</div>
                ${explanationSection}
                ${tagsSection}
            </div>
        `;

        if (summary.prompt_text) {
            const screenshotIdsMatch = summary.prompt_text.match(/Screenshot IDs used: \[([\d, ]+)\]/);
            let usedScreenshotIds = [];
            if (screenshotIdsMatch?.[1] && screenshotIdsMatch[1] !== 'none') {
                usedScreenshotIds = screenshotIdsMatch[1].split(',').map(id => parseInt(id.trim())).filter(id => !isNaN(id));
            }
            const usedScreenshots = usedScreenshotIds.length > 0 ? screenshots.filter(s => usedScreenshotIds.includes(s.id)) : [];

            html += `
                <div class="section">
                    <div class="section-header">
                        <h2 class="section-title">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
                            API Request
                        </h2>
                    </div>
                    ${usedScreenshots.length > 0 ? `
                        <div style="margin-bottom: 16px;">
                            <div style="font-size: 0.85rem; color: var(--muted); margin-bottom: 8px;">Screenshots sent to LLM (${usedScreenshots.length} of ${screenshots.length})</div>
                            <div class="screenshots-grid" style="grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));">
                                ${usedScreenshots.map(s => {
                                    const idx = screenshots.findIndex(ss => ss.id === s.id);
                                    return `<div class="screenshot-thumb" data-index="${idx}"><img src="/thumbnail/${s.id}" alt="Screenshot" loading="lazy"><span class="time-badge">${s.formatted_time}</span></div>`;
                                }).join('')}
                            </div>
                        </div>
                    ` : ''}
                    <div class="prompt-display">${escapeHtml(summary.prompt_text)}</div>
                </div>
            `;
        }

        if (activityLog.length > 0) {
            html += `
                <div class="section">
                    <div class="section-header">
                        <h2 class="section-title">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>
                            Activity Log
                        </h2>
                        <span class="section-count">${activityLog.length} events</span>
                    </div>
                    <table class="window-table">
                        <thead><tr><th style="width:80px;">Time</th><th style="width:120px;">App</th><th>Window</th><th style="width:100px;">Duration</th></tr></thead>
                        <tbody>
                            ${activityLog.map(e => `
                                <tr>
                                    <td style="font-family: monospace; font-size: 0.85rem;">${e.time}</td>
                                    <td style="font-weight: 500;">${escapeHtml(e.app_name)}</td>
                                    <td class="window-title" title="${escapeHtml(e.title)}">${escapeHtml(e.title)}</td>
                                    <td><div class="duration-bar"><div class="duration-bar-fill" style="width: ${(e.duration_seconds / maxActivityDuration) * 100}px;"></div><span class="duration-text">${formatDuration(e.duration_seconds)}</span></div></td>
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </div>
            `;
        }

        if (windowDurations.length > 0) {
            html += `
                <div class="section">
                    <div class="section-header">
                        <h2 class="section-title">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>
                            Time Breakdown
                        </h2>
                        <span class="section-count">${formatDuration(totalFocusSeconds)} total</span>
                    </div>
                    <table class="window-table">
                        <thead><tr><th>App / Window</th><th style="width:200px;">Duration</th></tr></thead>
                        <tbody>
                            ${windowDurations.map(w => {
                                const pct = totalFocusSeconds > 0 ? Math.round((w.duration_seconds / totalFocusSeconds) * 100) : 0;
                                return `<tr><td class="window-title" title="${escapeHtml(w.title)}">${escapeHtml(w.app_name)} (${escapeHtml(w.title)})</td><td><div class="duration-bar"><div class="duration-bar-fill" style="width: ${(w.duration_seconds / maxDuration) * 100}px;"></div><span class="duration-text">${formatDuration(w.duration_seconds)} [${pct}%]</span></div></td></tr>`;
                            }).join('')}
                        </tbody>
                    </table>
                </div>
            `;
        }

        html += `
            <div class="section">
                <div class="section-header">
                    <h2 class="section-title">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
                        Generation Details
                    </h2>
                </div>
                <div class="details-grid">
                    <div class="detail-item"><div class="detail-label">Model</div><div class="detail-value mono">${summary.model_used || 'Unknown'}</div></div>
                    <div class="detail-item"><div class="detail-label">Inference Time</div><div class="detail-value">${summary.inference_time_ms ? (summary.inference_time_ms / 1000).toFixed(2) + 's' : 'N/A'}</div></div>
                    <div class="detail-item"><div class="detail-label">Generated</div><div class="detail-value">${new Date(summary.created_at).toLocaleString()}</div></div>
                    <div class="detail-item"><div class="detail-label">Summary ID</div><div class="detail-value mono">#${summary.id}</div></div>
                </div>
            </div>
        `;

        if (Object.keys(config).length > 0) {
            html += `
                <div class="section">
                    <div class="section-header">
                        <h2 class="section-title">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                            Config Snapshot
                        </h2>
                    </div>
                    <div class="config-display">
                        ${Object.entries(config).map(([key, value]) => `<div class="config-item"><span class="config-key">${escapeHtml(key)}</span><span class="config-value">${escapeHtml(JSON.stringify(value))}</span></div>`).join('')}
                    </div>
                </div>
            `;
        }

        html += `
            <div class="section">
                <div class="section-header">
                    <h2 class="section-title">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>
                        All Screenshots
                    </h2>
                    <span class="section-count">${screenshots.length}</span>
                </div>
                <div class="screenshots-grid">
                    ${screenshots.map((s, i) => `<div class="screenshot-thumb" data-index="${i}"><img src="/thumbnail/${s.id}" alt="Screenshot" loading="lazy"><span class="time-badge">${s.formatted_time}</span></div>`).join('')}
                </div>
            </div>
        `;

        document.getElementById('content').innerHTML = html;

        // Attach click handlers to screenshot thumbs
        document.querySelectorAll('.screenshot-thumb[data-index]').forEach(thumb => {
            thumb.addEventListener('click', () => openScreenshot(parseInt(thumb.dataset.index)));
        });
    }

    function openScreenshot(index) {
        // Use the shared screenshot modal
        ScreenshotModal.show(data.screenshots, index);
    }

    async function regenerateSummary() {
        if (!confirm('Regenerate this summary with current settings?')) return;

        const btn = document.getElementById('regenerateBtn');
        const originalHtml = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner"></span> Regenerating...';

        try {
            const response = await fetch(`/api/threshold-summaries/${summaryId}/regenerate`, { method: 'POST' });
            const result = await response.json();

            if (result.status === 'queued') {
                showToast('Regeneration started. The page will reload when complete.', 'success');
                const interval = setInterval(async () => {
                    const statusRes = await fetch('/api/threshold-summaries/worker-status');
                    const status = await statusRes.json();
                    if (!status.running || status.current_task !== 'regenerate') {
                        clearInterval(interval);
                        location.reload();
                    }
                }, 2000);
            } else {
                btn.disabled = false;
                btn.innerHTML = originalHtml;
            }
        } catch (error) {
            showToast('Failed to regenerate: ' + error.message, 'error');
            btn.disabled = false;
            btn.innerHTML = originalHtml;
        }
    }

    async function deleteSummary() {
        if (!confirm('Delete this summary? This cannot be undone.')) return;

        try {
            const response = await fetch(`/api/threshold-summaries/${summaryId}`, { method: 'DELETE' });
            const result = await response.json();

            if (result.status === 'deleted') {
                window.location.href = '/timeline';
            } else {
                showToast('Failed to delete: ' + (result.error || 'Unknown error'), 'error');
            }
        } catch (error) {
            showToast('Failed to delete: ' + error.message, 'error');
        }
    }
})();
