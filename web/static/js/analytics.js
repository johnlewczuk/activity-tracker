// Analytics Page JavaScript

(function() {
    const state = {
        dateRange: 7,
        charts: {},
        data: {}
    };

    document.addEventListener('DOMContentLoaded', () => {
        setupEventListeners();
        loadAllData();
    });

    function setupEventListeners() {
        document.querySelectorAll('.range-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                document.querySelectorAll('.range-btn').forEach(b => b.classList.remove('active'));
                e.target.classList.add('active');
                state.dateRange = parseInt(e.target.dataset.range);
                loadAllData();
            });
        });

        document.getElementById('refreshBtn').addEventListener('click', loadAllData);
    }

    async function loadAllData() {
        try {
            const [aiData, focusData] = await Promise.all([
                fetch(`/api/analytics/ai?days=${state.dateRange}`).then(r => r.json()),
                fetch(`/api/analytics/focus/${getLocalDateString(new Date())}`).then(r => r.json())
            ]);

            state.data.ai = aiData;
            state.data.focus = focusData;

            renderAIInsights(aiData);
            renderFocusSection(focusData);
            renderAppUsageFromFocus(focusData);
            renderTrendChart(aiData);
        } catch (error) {
            console.error('Failed to load data:', error);
        }
    }

    function renderAIInsights(data) {
        document.getElementById('totalSummaries').textContent = data.total_summaries || 0;
        document.getElementById('avgConfidence').textContent = data.avg_confidence ? `${Math.round(data.avg_confidence * 100)}%` : '-';
        document.getElementById('highConfCount').textContent = data.confidence_distribution?.high || 0;
        document.getElementById('uniqueTags').textContent = Object.keys(data.tag_counts || {}).length;

        renderTagCloud(data.tag_counts);
        renderConfidenceChart(data.confidence_distribution);
        renderRecentSummaries(data.recent_summaries);
    }

    function renderTagCloud(tagCounts) {
        const container = document.getElementById('tagCloud');
        const entries = Object.entries(tagCounts || {});

        if (entries.length === 0) {
            container.innerHTML = '<div class="no-data">No tags generated yet.</div>';
            return;
        }

        const maxCount = Math.max(...entries.map(e => e[1]));

        container.innerHTML = entries.map(([tag, count]) => {
            const ratio = count / maxCount;
            let sizeClass = 'size-1';
            if (ratio > 0.8) sizeClass = 'size-5';
            else if (ratio > 0.6) sizeClass = 'size-4';
            else if (ratio > 0.4) sizeClass = 'size-3';
            else if (ratio > 0.2) sizeClass = 'size-2';

            return `<span class="tag-item ${sizeClass}">${escapeHtml(tag)}<span class="tag-count">${count}</span></span>`;
        }).join('');
    }

    function renderConfidenceChart(dist) {
        const ctx = document.getElementById('confidenceChart').getContext('2d');
        const theme = getThemeColors();

        state.charts.confidence = destroyChart(state.charts.confidence);

        const total = (dist?.high || 0) + (dist?.medium || 0) + (dist?.low || 0);
        if (total === 0) {
            ctx.font = '14px sans-serif';
            ctx.fillStyle = theme.muted;
            ctx.textAlign = 'center';
            ctx.fillText('No confidence data yet', ctx.canvas.width / 2, ctx.canvas.height / 2);
            return;
        }

        state.charts.confidence = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: ['High (≥80%)', 'Medium (50-80%)', 'Low (<50%)'],
                datasets: [{
                    data: [dist.high, dist.medium, dist.low],
                    backgroundColor: [theme.success, theme.warning, theme.danger],
                    borderColor: theme.bg,
                    borderWidth: 3
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        position: 'bottom',
                        labels: { color: theme.text, padding: 15 }
                    }
                }
            }
        });
    }

    function renderRecentSummaries(summaries) {
        const container = document.getElementById('recentSummaries');

        if (!summaries || summaries.length === 0) {
            container.innerHTML = '<li class="no-data">No summaries generated yet.</li>';
            return;
        }

        container.innerHTML = summaries.map(s => {
            const time = s.start_time ? new Date(s.start_time).toLocaleString([], {
                month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
            }) : '-';

            let confClass = 'conf-medium';
            let confLabel = 'Medium';
            if (s.confidence >= 0.8) { confClass = 'conf-high'; confLabel = 'High'; }
            else if (s.confidence < 0.5) { confClass = 'conf-low'; confLabel = 'Low'; }

            const tagsHtml = (s.tags || []).slice(0, 3).map(t =>
                `<span class="summary-tag">${escapeHtml(t)}</span>`
            ).join('');

            return `
                <li class="summary-item">
                    <span class="summary-time">${time}</span>
                    <div class="summary-content">
                        <div class="summary-text">${escapeHtml(s.summary)}</div>
                        <div class="summary-meta">
                            ${tagsHtml}
                            ${s.confidence !== null ? `<span class="confidence-badge ${confClass}">${confLabel}</span>` : ''}
                            <a href="/summary/${s.id}" class="summary-link">View details</a>
                        </div>
                    </div>
                </li>
            `;
        }).join('');
    }

    function renderFocusSection(data) {
        if (!data || data.error) {
            document.getElementById('trackedTime').textContent = '0m';
            document.getElementById('contextSwitches').textContent = '0';
            document.getElementById('longestFocus').textContent = '0m';
            document.getElementById('appsUsed').textContent = '0';
            document.getElementById('focusTimeline').innerHTML = '<div class="no-data" style="padding: 10px;">No focus data for today</div>';
            return;
        }

        const metrics = data.metrics || {};
        document.getElementById('trackedTime').textContent = formatDuration(metrics.total_tracked_seconds);
        document.getElementById('contextSwitches').textContent = metrics.context_switches || 0;
        document.getElementById('appsUsed').textContent = metrics.unique_apps || 0;

        const longest = metrics.longest_focus_sessions?.[0];
        document.getElementById('longestFocus').textContent = longest ? formatDuration(longest.duration_seconds) : '0m';

        renderFocusTimeline(data.apps);
    }

    function renderFocusTimeline(apps) {
        const container = document.getElementById('focusTimeline');
        const legend = document.getElementById('focusLegend');

        if (!apps || apps.length === 0) {
            container.innerHTML = '<div class="no-data" style="padding: 10px;">No focus data</div>';
            legend.innerHTML = '';
            return;
        }

        const colors = ['#58a6ff', '#f85149', '#3fb950', '#d29922', '#a371f7', '#ff7b72', '#79c0ff'];
        const total = apps.reduce((sum, a) => sum + (a.total_seconds || 0), 0);

        let html = '';
        let offset = 0;
        apps.forEach((app, i) => {
            const width = ((app.total_seconds || 0) / total) * 100;
            if (width > 0.5) {
                html += `<div class="timeline-block" style="left: ${offset}%; width: ${width}%; background: ${colors[i % colors.length]};" title="${app.app_name}: ${formatDuration(app.total_seconds)}"></div>`;
                offset += width;
            }
        });

        container.innerHTML = html || '<div class="no-data" style="padding: 10px;">No focus data</div>';

        legend.innerHTML = apps.slice(0, 5).map((app, i) => `
            <div class="legend-item">
                <div class="legend-color" style="background: ${colors[i % colors.length]}"></div>
                <span>${app.app_name} (${formatDuration(app.total_seconds)})</span>
            </div>
        `).join('');
    }

    function renderAppUsageFromFocus(data) {
        const apps = data?.apps || [];
        const tbody = document.getElementById('appTableBody');
        const theme = getThemeColors();

        if (apps.length === 0) {
            tbody.innerHTML = '<tr><td colspan="3" class="no-data">No app usage data</td></tr>';
            return;
        }

        const total = apps.reduce((sum, a) => sum + (a.total_seconds || 0), 0);

        tbody.innerHTML = apps.slice(0, 15).map((app, i) => {
            const pct = total > 0 ? ((app.total_seconds / total) * 100).toFixed(1) : 0;
            return `
                <tr>
                    <td><span style="color: var(--muted); margin-right: 8px;">${i + 1}.</span>${escapeHtml(app.app_name)}</td>
                    <td style="text-align: right;">${formatDuration(app.total_seconds)}</td>
                    <td>
                        <div class="progress-bar-container">
                            <div class="progress-bar" style="width: ${pct}%"></div>
                        </div>
                    </td>
                </tr>
            `;
        }).join('');

        renderAppPieChart(apps.slice(0, 8));
    }

    function renderAppPieChart(apps) {
        const ctx = document.getElementById('appUsageChart').getContext('2d');
        const theme = getThemeColors();

        state.charts.apps = destroyChart(state.charts.apps);

        if (apps.length === 0) {
            ctx.font = '14px sans-serif';
            ctx.fillStyle = theme.muted;
            ctx.textAlign = 'center';
            ctx.fillText('No app usage data', ctx.canvas.width / 2, ctx.canvas.height / 2);
            return;
        }

        const colors = ['#58a6ff', '#f85149', '#3fb950', '#d29922', '#a371f7', '#ff7b72', '#79c0ff', '#ffa657'];

        state.charts.apps = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: apps.map(a => a.app_name),
                datasets: [{
                    data: apps.map(a => a.total_seconds),
                    backgroundColor: colors,
                    borderColor: theme.bg,
                    borderWidth: 2
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        position: 'right',
                        labels: { color: theme.text, font: { size: 11 } }
                    },
                    tooltip: {
                        callbacks: {
                            label: (ctx) => `${ctx.label}: ${formatDuration(ctx.raw)}`
                        }
                    }
                }
            }
        });
    }

    function renderTrendChart(data) {
        const ctx = document.getElementById('trendChart').getContext('2d');
        const theme = getThemeColors();

        state.charts.trend = destroyChart(state.charts.trend);

        const byDay = data.summaries_by_day || {};
        const days = [];
        const counts = [];

        for (let i = state.dateRange - 1; i >= 0; i--) {
            const d = new Date();
            d.setDate(d.getDate() - i);
            const dateStr = getLocalDateString(d);
            days.push(d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }));
            counts.push(byDay[dateStr] || 0);
        }

        state.charts.trend = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: days,
                datasets: [{
                    label: 'Summaries',
                    data: counts,
                    backgroundColor: theme.accent,
                    borderColor: theme.accentStrong,
                    borderWidth: 1,
                    borderRadius: 4
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    y: {
                        beginAtZero: true,
                        ticks: { color: theme.muted, stepSize: 1 },
                        grid: { color: theme.border }
                    },
                    x: {
                        ticks: { color: theme.muted },
                        grid: { display: false }
                    }
                },
                plugins: {
                    legend: { display: false }
                }
            }
        });
    }
})();
