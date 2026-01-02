// Reports Page JavaScript

// Current report data for export
let currentReport = null;
let currentTimeRange = '';
let currentReportType = 'summary';
let pdfAvailable = true;  // Will be updated on load

// Generate report
async function generateReport(timeRange, reportType, includeScreenshots = true) {
    document.getElementById('loading').style.display = 'flex';
    document.getElementById('report-container').style.display = 'none';

    currentTimeRange = timeRange;
    currentReportType = reportType;

    try {
        const response = await fetch('/api/reports/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                time_range: timeRange,
                report_type: reportType,
                include_screenshots: includeScreenshots
            })
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || 'Report generation failed');
        }

        const report = await response.json();
        currentReport = report;
        displayReport(report);
    } catch (error) {
        showToast('Error: ' + error.message, 'error');
    } finally {
        document.getElementById('loading').style.display = 'none';
    }
}

// Display report
function displayReport(report) {
    document.getElementById('report-title').textContent = report.title;
    document.getElementById('report-meta').textContent =
        `Generated: ${new Date(report.generated_at).toLocaleString()}`;

    // Analytics
    const hours = Math.floor(report.analytics.total_active_minutes / 60);
    const mins = report.analytics.total_active_minutes % 60;
    const timeStr = hours > 0 ? `${hours}h ${mins}m` : `${mins}m`;

    document.getElementById('analytics-grid').innerHTML = `
        <div class="stat-card">
            <div class="stat-value">${timeStr}</div>
            <div class="stat-label">Active Time</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">${report.analytics.total_sessions}</div>
            <div class="stat-label">Sessions</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">${report.analytics.top_apps.length}</div>
            <div class="stat-label">Apps Used</div>
        </div>
        <div class="stat-card">
            <div class="stat-value" style="font-size: 1rem;">${report.analytics.busiest_period}</div>
            <div class="stat-label">Busiest Period</div>
        </div>
    `;

    // Top Apps
    if (report.analytics.top_apps.length > 0) {
        const appsHtml = report.analytics.top_apps.slice(0, 8).map(app => `
            <span class="app-badge">
                ${app.name}
                <span class="app-time">${app.minutes}m</span>
            </span>
        `).join('');

        document.getElementById('top-apps').innerHTML = `
            <h3>Top Applications</h3>
            <div class="app-list">${appsHtml}</div>
        `;
        document.getElementById('top-apps').style.display = 'block';
    } else {
        document.getElementById('top-apps').style.display = 'none';
    }

    // Executive Summary
    document.getElementById('executive-summary').innerHTML = `
        <h3>Executive Summary</h3>
        <p>${report.executive_summary}</p>
    `;

    // Sections
    if (report.sections && report.sections.length > 0) {
        const sectionsHtml = report.sections.map(s => `
            <div class="section-card">
                <h3>${s.title}</h3>
                <p>${s.content}</p>
            </div>
        `).join('');
        document.getElementById('report-sections').innerHTML = sectionsHtml;
    } else {
        document.getElementById('report-sections').innerHTML = '';
    }

    // Screenshots
    if (report.key_screenshots && report.key_screenshots.length > 0) {
        const screenshotsHtml = report.key_screenshots.map(s => `
            <div class="screenshot-card">
                <img src="${s.url}" alt="Screenshot" loading="lazy">
                <div class="screenshot-meta">
                    <span class="time">${new Date(s.timestamp).toLocaleTimeString()}</span>
                    <span class="title">${s.window_title || 'Unknown'}</span>
                </div>
            </div>
        `).join('');
        document.getElementById('screenshot-grid').innerHTML = screenshotsHtml;
        document.getElementById('screenshots-section').style.display = 'block';
    } else {
        document.getElementById('screenshots-section').style.display = 'none';
    }

    document.getElementById('report-container').style.display = 'block';

    // Scroll to report with smooth animation
    setTimeout(() => {
        document.getElementById('report-container').scrollIntoView({
            behavior: 'smooth',
            block: 'start'
        });
    }, 100);
}

// Export report (uses already-generated data - instant!)
async function exportReport(format) {
    if (!currentReport) {
        showToast('Please generate a report first', 'warning');
        return;
    }

    if (format === 'pdf' && !pdfAvailable) {
        showToast('PDF export unavailable. Install weasyprint for PDF support.', 'warning');
        return;
    }

    // Show brief loading indicator for HTML/PDF (needs to embed images)
    const btn = document.querySelector(`.export-btn[data-format="${format}"]`);
    const originalText = btn.textContent;
    btn.textContent = 'Exporting...';
    btn.disabled = true;

    try {
        const response = await fetch('/api/reports/export', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                report: currentReport,  // Pass the report data directly!
                format: format
            })
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || 'Export failed');
        }

        const result = await response.json();

        // Refresh history list to show new export
        loadHistory();

        window.location.href = result.download_url;
    } catch (error) {
        showToast('Export error: ' + error.message, 'error');
    } finally {
        btn.textContent = originalText;
        btn.disabled = false;
    }
}

// Check export capabilities on load
async function checkCapabilities() {
    try {
        const response = await fetch('/api/reports/capabilities');
        const caps = await response.json();
        pdfAvailable = caps.pdf_available;

        // Update PDF button state
        const pdfBtn = document.querySelector('.export-btn[data-format="pdf"]');
        if (pdfBtn && !pdfAvailable) {
            pdfBtn.title = caps.pdf_message || 'PDF export unavailable';
            pdfBtn.style.opacity = '0.5';
            pdfBtn.style.cursor = 'not-allowed';
        }
    } catch (error) {
        console.warn('Failed to check export capabilities:', error);
    }
}

// Load saved reports (cached daily reports)
async function loadSavedReports() {
    try {
        const response = await fetch('/api/reports/saved?days_back=30');
        const data = await response.json();

        const list = document.getElementById('saved-reports-list');
        const emptyState = document.getElementById('saved-empty');

        if (!data.reports || data.reports.length === 0) {
            emptyState.style.display = 'block';
            return;
        }

        emptyState.style.display = 'none';

        // Format duration helper
        const formatDuration = (mins) => {
            if (mins < 60) return `${mins}m`;
            return `${Math.floor(mins / 60)}h ${mins % 60}m`;
        };

        // Format date for display
        const formatDate = (dateStr) => {
            const date = new Date(dateStr + 'T12:00:00');
            return date.toLocaleDateString('en-US', {
                weekday: 'short',
                month: 'short',
                day: 'numeric',
                year: 'numeric'
            });
        };

        const itemsHtml = data.reports.map(r => `
            <div class="saved-report-item" onclick="loadSavedReport('${r.period_date}')">
                <div class="saved-report-info">
                    <div class="saved-report-date">${formatDate(r.period_date)}</div>
                    <div class="saved-report-summary">${r.executive_summary.replace(/</g, '&lt;')}</div>
                </div>
                <div class="saved-report-stats">
                    <span>${formatDuration(r.total_minutes)}</span>
                </div>
            </div>
        `).join('');

        list.innerHTML = itemsHtml;
    } catch (error) {
        console.warn('Failed to load saved reports:', error);
    }
}

// Load a specific saved report for viewing/export
async function loadSavedReport(periodDate) {
    document.getElementById('loading').style.display = 'flex';
    document.getElementById('report-container').style.display = 'none';

    try {
        const response = await fetch(`/api/reports/saved/${periodDate}`);
        if (!response.ok) {
            throw new Error('Failed to load saved report');
        }

        const report = await response.json();
        currentReport = report;
        currentTimeRange = periodDate;
        currentReportType = 'summary';
        displayReport(report);
    } catch (error) {
        showToast('Error loading report: ' + error.message, 'error');
    } finally {
        document.getElementById('loading').style.display = 'none';
    }
}

async function loadHistory() {
    try {
        const response = await fetch('/api/reports/history?limit=10');
        const data = await response.json();

        const historyList = document.getElementById('history-list');
        const emptyState = document.getElementById('history-empty');

        if (!data.reports || data.reports.length === 0) {
            emptyState.style.display = 'block';
            return;
        }

        emptyState.style.display = 'none';

        // Build history items HTML
        const itemsHtml = data.reports.map(r => {
            const createdAt = new Date(r.created_at).toLocaleString();
            const sizeDisplay = r.file_size_display || '';

            return `
                <div class="history-item" data-id="${r.id}">
                    <div class="history-info">
                        <div class="history-title">
                            <span class="format-badge ${r.format}">${r.format}</span>
                            ${r.title}
                        </div>
                        <div class="history-meta">
                            <span>${createdAt}</span>
                            ${sizeDisplay ? `<span>${sizeDisplay}</span>` : ''}
                        </div>
                    </div>
                    <div class="history-actions">
                        <button class="download" onclick="window.location.href='${r.download_url}'">Download</button>
                        <button onclick="deleteHistoryItem(${r.id})">Delete</button>
                    </div>
                </div>
            `;
        }).join('');

        historyList.innerHTML = itemsHtml;
    } catch (error) {
        console.warn('Failed to load report history:', error);
    }
}

// Delete history item
async function deleteHistoryItem(id) {
    if (!confirm('Remove this report from history?')) return;

    try {
        const response = await fetch(`/api/reports/history/${id}`, {
            method: 'DELETE'
        });

        if (response.ok) {
            loadHistory();  // Refresh the list
        } else {
            showToast('Failed to delete report', 'error');
        }
    } catch (error) {
        showToast('Error deleting report: ' + error.message, 'error');
    }
}

// Event listeners
document.addEventListener('DOMContentLoaded', () => {
    // Check export capabilities (PDF availability)
    checkCapabilities();

    // Load saved reports and export history
    loadSavedReports();
    loadHistory();

    // Preset buttons
    document.querySelectorAll('.preset-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            generateReport(btn.dataset.range, btn.dataset.type);
        });
    });

    // Generate button
    document.getElementById('generate-btn').addEventListener('click', () => {
        const timeRange = document.getElementById('time-range').value;
        const reportType = document.getElementById('report-type').value;
        const includeScreenshots = document.getElementById('include-screenshots').checked;

        if (!timeRange) {
            showToast('Please enter a time range', 'warning');
            return;
        }

        generateReport(timeRange, reportType, includeScreenshots);
    });

    // Export buttons
    document.querySelectorAll('.export-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            exportReport(btn.dataset.format);
        });
    });

    // Enter key on time range input
    document.getElementById('time-range').addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            document.getElementById('generate-btn').click();
        }
    });
});
