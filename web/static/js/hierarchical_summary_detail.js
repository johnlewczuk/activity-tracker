// Hierarchical Summary Detail Page JavaScript

(function() {
    const contentEl = document.getElementById('content');
    const periodType = contentEl.dataset.periodType;
    const periodDate = contentEl.dataset.periodDate;
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
            const response = await fetch(`/api/hierarchical-summaries/${periodType}/${periodDate}`);
            data = await response.json();

            if (data.error) {
                if (response.status === 404) {
                    renderNotFound();
                } else {
                    document.getElementById('content').innerHTML = `
                        <div class="error-state">
                            <h2>Error</h2>
                            <p>${escapeHtml(data.error)}</p>
                            <a href="/reports" class="back-link">Return to Reports</a>
                        </div>
                    `;
                }
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

    function renderNotFound() {
        document.getElementById('content').innerHTML = `
            <div class="not-found-state">
                <h2>No ${periodType} summary found for ${periodDate}</h2>
                <p>This summary hasn't been generated yet.</p>
                <button class="btn" id="generateBtn">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14">
                        <polygon points="5 3 19 12 5 21 5 3"/>
                    </svg>
                    Generate Now
                </button>
            </div>
        `;
        document.getElementById('generateBtn').addEventListener('click', generateSummary);
    }

    function renderContent() {
        const analytics = data.analytics || {};
        const childSummaries = data.child_summaries || [];

        // Format date range
        let dateRange = formatPeriodDate(periodType, periodDate);

        // Format period badge
        const periodBadgeClass = periodType;

        let confidenceBadge = '';
        if (data.confidence != null) {
            const conf = data.confidence;
            let confClass = 'conf-medium', confLabel = 'Moderate';
            if (conf >= 0.8) { confClass = 'conf-high'; confLabel = 'High'; }
            else if (conf < 0.5) { confClass = 'conf-low'; confLabel = 'Low'; }
            confidenceBadge = `<span class="summary-meta-item"><span class="confidence-badge ${confClass}" title="${confLabel}: ${(conf * 100).toFixed(0)}%">${confLabel} (${(conf * 100).toFixed(0)}%)</span></span>`;
        }

        let explanationSection = '';
        if (data.explanation) {
            explanationSection = `
                <div class="explanation-section">
                    <div class="explanation-label">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14">
                            <circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>
                        </svg>
                        Model Explanation
                    </div>
                    <div class="explanation-text">${escapeHtml(data.explanation)}</div>
                </div>
            `;
        }

        let tagsSection = '';
        const tags = data.tags || [];
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
                    <span class="period-badge ${periodBadgeClass}">${periodType}</span>
                    <span class="summary-meta-item">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
                        ${dateRange}
                    </span>
                    ${analytics.total_active_minutes ? `
                    <span class="summary-meta-item">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
                        ${Math.round(analytics.total_active_minutes / 60)}h ${analytics.total_active_minutes % 60}m active
                    </span>
                    ` : ''}
                    ${confidenceBadge}
                </div>
                <div class="summary-text">${escapeHtml(data.executive_summary || 'No summary available')}</div>
                ${explanationSection}
                ${tagsSection}
            </div>
        `;

        // Analytics section
        if (Object.keys(analytics).length > 0) {
            html += `
                <div class="section">
                    <div class="section-header">
                        <h2 class="section-title">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 20V10M12 20V4M6 20v-6"/></svg>
                            Analytics
                        </h2>
                    </div>
                    <div class="analytics-grid">
                        <div class="analytics-card">
                            <div class="analytics-label">Active Time</div>
                            <div class="analytics-value">${formatMinutes(analytics.total_active_minutes || 0)}</div>
                        </div>
                        <div class="analytics-card">
                            <div class="analytics-label">Sessions</div>
                            <div class="analytics-value">${analytics.total_sessions || 0}</div>
                        </div>
                        <div class="analytics-card">
                            <div class="analytics-label">Busiest Period</div>
                            <div class="analytics-value small">${analytics.busiest_period || 'N/A'}</div>
                        </div>
                    </div>
            `;

            // Top apps
            if (analytics.top_apps && analytics.top_apps.length > 0) {
                const maxMinutes = Math.max(...analytics.top_apps.map(a => a.minutes));
                html += `
                    <div style="margin-top: 16px;">
                        <h3 class="section-title" style="font-size: 0.9rem; margin-bottom: 12px;">Top Applications</h3>
                        <div class="top-apps-list">
                            ${analytics.top_apps.slice(0, 8).map(app => `
                                <div class="app-bar">
                                    <span class="app-name" title="${escapeHtml(app.name)}">${escapeHtml(app.name)}</span>
                                    <div class="app-bar-fill">
                                        <div class="app-bar-fill-inner" style="width: ${(app.minutes / maxMinutes) * 100}%"></div>
                                    </div>
                                    <span class="app-time">${formatMinutes(app.minutes)}</span>
                                </div>
                            `).join('')}
                        </div>
                    </div>
                `;
            }

            html += `</div>`;
        }

        // Child summaries section
        if (childSummaries.length > 0) {
            const childType = periodType === 'daily' ? '30-Minute Summaries' :
                              periodType === 'weekly' ? 'Daily Summaries' : 'Weekly Summaries';
            html += `
                <div class="section">
                    <div class="section-header">
                        <h2 class="section-title">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>
                            ${childType}
                        </h2>
                        <span class="section-count">${childSummaries.length}</span>
                    </div>
                    <div class="child-summary-list">
                        ${childSummaries.map(child => {
                            const childLink = getChildLink(child, periodType);
                            const childDate = formatChildDate(child, periodType);
                            return `
                                <div class="child-summary-item">
                                    <a href="${childLink}">
                                        <div class="child-summary-header">
                                            <span class="child-summary-date">${childDate}</span>
                                            <span class="child-summary-time">${child.start_time ? formatTime(child.start_time) : ''}</span>
                                        </div>
                                        <div class="child-summary-text">${escapeHtml(truncate(child.summary || '', 200))}</div>
                                    </a>
                                </div>
                            `;
                        }).join('')}
                    </div>
                </div>
            `;
        }

        // Prompt section
        if (data.prompt_text) {
            html += `
                <div class="section">
                    <div class="section-header">
                        <h2 class="section-title">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
                            LLM Prompt
                        </h2>
                    </div>
                    <div class="prompt-display">${escapeHtml(data.prompt_text)}</div>
                </div>
            `;
        }

        // Generation details section
        html += `
            <div class="section">
                <div class="section-header">
                    <h2 class="section-title">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
                        Generation Details
                    </h2>
                </div>
                <div class="details-grid">
                    <div class="detail-item"><div class="detail-label">Model</div><div class="detail-value mono">${data.model_used || 'Unknown'}</div></div>
                    <div class="detail-item"><div class="detail-label">Inference Time</div><div class="detail-value">${data.inference_time_ms ? (data.inference_time_ms / 1000).toFixed(2) + 's' : 'N/A'}</div></div>
                    <div class="detail-item"><div class="detail-label">Generated</div><div class="detail-value">${data.created_at ? new Date(data.created_at).toLocaleString() : 'N/A'}</div></div>
                    ${data.regenerated_at ? `<div class="detail-item"><div class="detail-label">Last Regenerated</div><div class="detail-value">${new Date(data.regenerated_at).toLocaleString()}</div></div>` : ''}
                    <div class="detail-item"><div class="detail-label">Report ID</div><div class="detail-value mono">#${data.id}</div></div>
                </div>
            </div>
        `;

        document.getElementById('content').innerHTML = html;
    }

    function formatPeriodDate(type, date) {
        if (type === 'daily') {
            const d = new Date(date);
            return d.toLocaleDateString('en-US', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' });
        } else if (type === 'weekly') {
            // Parse ISO week YYYY-Www
            const parts = date.split('-W');
            if (parts.length === 2) {
                return `Week ${parts[1]}, ${parts[0]}`;
            }
            return date;
        } else if (type === 'monthly') {
            // Parse YYYY-MM
            const [year, month] = date.split('-');
            const d = new Date(year, parseInt(month) - 1, 1);
            return d.toLocaleDateString('en-US', { year: 'numeric', month: 'long' });
        }
        return date;
    }

    function formatMinutes(minutes) {
        if (minutes < 60) return `${minutes}m`;
        const h = Math.floor(minutes / 60);
        const m = minutes % 60;
        return m > 0 ? `${h}h ${m}m` : `${h}h`;
    }

    function formatTime(isoTime) {
        const d = new Date(isoTime);
        return d.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
    }

    function getChildLink(child, parentType) {
        if (parentType === 'daily') {
            // Link to threshold summary detail
            return `/summary/${child.id}`;
        } else if (parentType === 'weekly') {
            // Link to daily summary
            return `/summary/daily/${child.period_date}`;
        } else if (parentType === 'monthly') {
            // Link to weekly summary
            return `/summary/weekly/${child.period_date}`;
        }
        return '#';
    }

    function formatChildDate(child, parentType) {
        if (parentType === 'daily') {
            // 30-minute summary: show time range
            if (child.start_time && child.end_time) {
                const start = new Date(child.start_time);
                const end = new Date(child.end_time);
                return `${start.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'})} - ${end.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'})}`;
            }
        } else if (parentType === 'weekly') {
            // Daily summary: show weekday and date
            const d = new Date(child.period_date);
            return d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
        } else if (parentType === 'monthly') {
            // Weekly summary: show week number
            return child.period_date;
        }
        return child.period_date || '';
    }

    function truncate(text, maxLength) {
        if (text.length <= maxLength) return text;
        return text.substring(0, maxLength) + '...';
    }

    async function regenerateSummary() {
        if (!confirm('Regenerate this summary with current settings?')) return;

        const btn = document.getElementById('regenerateBtn');
        const originalHtml = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner"></span> Regenerating...';

        try {
            const response = await fetch(`/api/hierarchical-summaries/${periodType}/${periodDate}/regenerate`, { method: 'POST' });
            const result = await response.json();

            if (result.status === 'queued') {
                showToast('Regeneration started. The page will reload when complete.', 'success');
                // Poll for completion
                const interval = setInterval(async () => {
                    try {
                        const checkRes = await fetch(`/api/hierarchical-summaries/${periodType}/${periodDate}`);
                        const checkData = await checkRes.json();
                        if (checkData.regenerated_at && checkData.regenerated_at !== data.regenerated_at) {
                            clearInterval(interval);
                            location.reload();
                        }
                    } catch (e) {
                        // Continue polling
                    }
                }, 2000);
                // Stop polling after 60 seconds
                setTimeout(() => {
                    clearInterval(interval);
                    btn.disabled = false;
                    btn.innerHTML = originalHtml;
                    location.reload();
                }, 60000);
            } else {
                showToast(result.error || 'Failed to regenerate', 'error');
                btn.disabled = false;
                btn.innerHTML = originalHtml;
            }
        } catch (error) {
            showToast('Failed to regenerate: ' + error.message, 'error');
            btn.disabled = false;
            btn.innerHTML = originalHtml;
        }
    }

    async function generateSummary() {
        const btn = document.getElementById('generateBtn');
        const originalHtml = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner"></span> Generating...';

        try {
            const response = await fetch(`/api/hierarchical-summaries/${periodType}/${periodDate}/generate`, { method: 'POST' });
            const result = await response.json();

            if (result.status === 'generated') {
                showToast('Summary generated!', 'success');
                location.reload();
            } else {
                showToast(result.error || 'Failed to generate', 'error');
                btn.disabled = false;
                btn.innerHTML = originalHtml;
            }
        } catch (error) {
            showToast('Failed to generate: ' + error.message, 'error');
            btn.disabled = false;
            btn.innerHTML = originalHtml;
        }
    }

    async function deleteSummary() {
        if (!confirm('Delete this summary? This cannot be undone.')) return;

        try {
            const response = await fetch(`/api/hierarchical-summaries/${periodType}/${periodDate}`, { method: 'DELETE' });
            const result = await response.json();

            if (result.status === 'deleted') {
                window.location.href = '/reports';
            } else {
                showToast('Failed to delete: ' + (result.error || 'Unknown error'), 'error');
            }
        } catch (error) {
            showToast('Failed to delete: ' + error.message, 'error');
        }
    }
})();
