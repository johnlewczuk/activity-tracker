/**
 * Analytics Summary Page JavaScript
 * Handles Day/Week/Month views with navigation and data fetching
 */

(function() {
    'use strict';

    // State management
    const state = {
        view: 'day',
        // Day view state
        date: getLocalDateString(new Date()),
        // Week view state
        weekYear: null,
        weekNum: null,
        // Month view state
        monthYear: null,
        month: null,
        // Cache
        loading: false
    };

    // Day labels for week view
    const DAY_LABELS = ['M', 'T', 'W', 'T', 'F', 'S', 'S'];

    // Initialize current week/month
    const now = new Date();
    state.weekYear = getISOWeekYear(now);
    state.weekNum = getISOWeek(now);
    state.monthYear = now.getFullYear();
    state.month = now.getMonth() + 1;

    // ==================== Initialization ====================

    document.addEventListener('DOMContentLoaded', () => {
        setupEventListeners();
        // Load initial view (day)
        fetchDayData();
    });

    function setupEventListeners() {
        // Tab switching
        document.querySelectorAll('.period-tab').forEach(tab => {
            tab.addEventListener('click', () => switchView(tab.dataset.view));
        });

        // Day view controls
        document.getElementById('day-prev')?.addEventListener('click', () => navigateDay(-1));
        document.getElementById('day-next')?.addEventListener('click', () => navigateDay(1));
        document.getElementById('day-today')?.addEventListener('click', goToToday);
        document.getElementById('day-picker')?.addEventListener('change', onDatePickerChange);

        // Week view controls
        document.getElementById('week-prev')?.addEventListener('click', () => navigateWeek(-1));
        document.getElementById('week-next')?.addEventListener('click', () => navigateWeek(1));
        document.getElementById('week-today')?.addEventListener('click', goToThisWeek);

        // Month view controls
        document.getElementById('month-prev')?.addEventListener('click', () => navigateMonth(-1));
        document.getElementById('month-next')?.addEventListener('click', () => navigateMonth(1));
        document.getElementById('month-today')?.addEventListener('click', goToThisMonth);
    }

    // ==================== View Switching ====================

    function switchView(view) {
        state.view = view;

        // Update tabs
        document.querySelectorAll('.period-tab').forEach(tab => {
            tab.classList.toggle('active', tab.dataset.view === view);
        });

        // Update containers
        document.querySelectorAll('.view-container').forEach(container => {
            container.classList.toggle('active', container.id === `${view}-view`);
        });

        // Fetch data for new view
        if (view === 'day') fetchDayData();
        else if (view === 'week') fetchWeekData();
        else if (view === 'month') fetchMonthData();
    }

    // ==================== Day View ====================

    function navigateDay(delta) {
        // Parse YYYY-MM-DD as local date (not UTC) to avoid timezone issues
        const [year, month, day] = state.date.split('-').map(Number);
        const current = new Date(year, month - 1, day);
        current.setDate(current.getDate() + delta);
        state.date = getLocalDateString(current);
        updateDatePicker();
        fetchDayData();
    }

    function goToToday() {
        state.date = getLocalDateString(new Date());
        updateDatePicker();
        fetchDayData();
    }

    function onDatePickerChange(e) {
        state.date = e.target.value;
        fetchDayData();
    }

    function updateDatePicker() {
        const picker = document.getElementById('day-picker');
        if (picker) picker.value = state.date;
    }

    async function fetchDayData() {
        if (state.loading) return;
        state.loading = true;

        try {
            const response = await fetch(`/api/analytics/summary/day/${state.date}`);
            const data = await response.json();

            if (data.error) {
                console.error('Day data error:', data.error);
                return;
            }

            renderDayView(data);
        } catch (error) {
            console.error('Failed to fetch day data:', error);
        } finally {
            state.loading = false;
        }
    }

    function renderDayView(data) {
        // Update header
        document.getElementById('day-label').textContent = data.label;
        document.getElementById('day-active-time').textContent =
            data.has_data ? `${formatDurationHM(data.active_time_seconds)} active` : 'No activity';

        // Update date picker
        const picker = document.getElementById('day-picker');
        if (picker) picker.value = data.date;

        // Disable next button if today
        const nextBtn = document.getElementById('day-next');
        if (nextBtn) {
            nextBtn.disabled = data.date === getLocalDateString(new Date());
        }

        // Render activity chart (24 bars)
        renderActivityChart('day-activity-chart', data.hourly_activity || [], 24, data.current_hour);

        // Render stats
        const stats = data.stats || {};
        document.getElementById('day-stat-active').textContent = formatDurationHM(stats.active_seconds);
        document.getElementById('day-stat-break').textContent = formatDurationHM(stats.break_seconds);
        document.getElementById('day-stat-start').textContent = stats.start_time || '--';
        document.getElementById('day-stat-end').textContent = stats.end_time || '--';
        document.getElementById('day-stat-switches').textContent = stats.context_switches || 0;
        document.getElementById('day-stat-focus').textContent = formatDurationHM(stats.longest_focus_seconds);

        // Goal progress
        const goalPct = Math.min(stats.goal_pct || 0, 100);
        document.getElementById('day-goal-fill').style.width = `${goalPct}%`;
        document.getElementById('day-goal-pct').textContent = `${goalPct}%`;

        // Peak hours
        renderPeakHours('day-peak-hours', data.peak_hours || []);
        renderPeakInsight('day-peak-insight', data.peak_hours || []);

        // Tags
        renderTags('day-tags-list', data.tags || []);

        // App distribution donut
        renderDonut('day-donut-chart', 'day-donut-legend', data.app_distribution || []);

        // Top windows
        renderWindows('day-windows-list', data.top_windows || []);
    }

    // ==================== Week View ====================

    function navigateWeek(delta) {
        let d = new Date();
        d.setFullYear(state.weekYear);
        // Set to Monday of the current ISO week
        d = getDateFromISOWeek(state.weekYear, state.weekNum);
        d.setDate(d.getDate() + (delta * 7));
        state.weekYear = getISOWeekYear(d);
        state.weekNum = getISOWeek(d);
        fetchWeekData();
    }

    function goToThisWeek() {
        const now = new Date();
        state.weekYear = getISOWeekYear(now);
        state.weekNum = getISOWeek(now);
        fetchWeekData();
    }

    async function fetchWeekData() {
        if (state.loading) return;
        state.loading = true;

        try {
            const response = await fetch(`/api/analytics/summary/week/${state.weekYear}/${state.weekNum}`);
            const data = await response.json();

            if (data.error) {
                console.error('Week data error:', data.error);
                return;
            }

            renderWeekView(data);
        } catch (error) {
            console.error('Failed to fetch week data:', error);
        } finally {
            state.loading = false;
        }
    }

    function renderWeekView(data) {
        // Update header
        document.getElementById('week-label').textContent = data.label;
        const activeText = data.has_data ? ` · ${formatDurationHM(data.active_time_seconds)} active` : '';
        document.getElementById('week-dates').textContent = `${data.date_range}${activeText}`;

        // Disable next button if current week
        const now = new Date();
        const isCurrentWeek = state.weekYear === getISOWeekYear(now) && state.weekNum === getISOWeek(now);
        const nextBtn = document.getElementById('week-next');
        if (nextBtn) nextBtn.disabled = isCurrentWeek;

        // Render daily activity chart (7 bars)
        renderWeekActivityChart('week-activity-chart', data.daily_activity || [], data.today_index);

        // Render stats
        const stats = data.stats || {};
        document.getElementById('week-stat-active').textContent = formatDurationHM(stats.active_seconds);
        document.getElementById('week-stat-avg').textContent = formatDurationHM(stats.avg_daily_seconds);
        document.getElementById('week-stat-break').textContent = formatDurationHM(stats.break_seconds);
        document.getElementById('week-stat-start').textContent = stats.typical_start || '--';
        document.getElementById('week-stat-end').textContent = stats.typical_end || '--';
        document.getElementById('week-stat-switches').textContent = stats.avg_context_switches || 0;
        document.getElementById('week-stat-focus').textContent = formatDurationHM(stats.longest_focus_seconds);
        document.getElementById('week-stat-days').textContent = `${stats.active_days || 0}/7`;

        // Goal progress
        const goalPct = Math.min(stats.goal_pct || 0, 100);
        document.getElementById('week-goal-fill').style.width = `${goalPct}%`;
        document.getElementById('week-goal-pct').textContent = `${goalPct}%`;

        // Peak hours
        renderPeakHours('week-peak-hours', data.peak_hours_avg || []);
        renderPeakInsight('week-peak-insight', data.peak_hours_avg || []);

        // Tags
        renderTags('week-tags-list', data.tags || []);

        // App distribution donut
        renderDonut('week-donut-chart', 'week-donut-legend', data.app_distribution || []);

        // Top windows
        renderWindows('week-windows-list', data.top_windows || []);

        // Daily breakdown
        renderDailyBreakdown('week-daily-hours', data.daily_breakdown?.hours || [], DAY_LABELS, data.today_index);
        renderDailyBreakdown('week-daily-breaks', data.daily_breakdown?.breaks || [], DAY_LABELS, data.today_index, true);
    }

    // ==================== Month View ====================

    function navigateMonth(delta) {
        let month = state.month + delta;
        let year = state.monthYear;

        if (month < 1) {
            month = 12;
            year--;
        } else if (month > 12) {
            month = 1;
            year++;
        }

        state.month = month;
        state.monthYear = year;
        fetchMonthData();
    }

    function goToThisMonth() {
        const now = new Date();
        state.monthYear = now.getFullYear();
        state.month = now.getMonth() + 1;
        fetchMonthData();
    }

    async function fetchMonthData() {
        if (state.loading) return;
        state.loading = true;

        try {
            const response = await fetch(`/api/analytics/summary/month/${state.monthYear}/${state.month}`);
            const data = await response.json();

            if (data.error) {
                console.error('Month data error:', data.error);
                return;
            }

            renderMonthView(data);
        } catch (error) {
            console.error('Failed to fetch month data:', error);
        } finally {
            state.loading = false;
        }
    }

    function renderMonthView(data) {
        // Update header
        document.getElementById('month-label').textContent = data.label;
        const activeText = data.has_data ? ` · ${formatDurationHM(data.active_time_seconds)} active` : '';
        document.getElementById('month-dates').textContent = `${data.date_range}${activeText}`;

        // Disable next button if current month
        const now = new Date();
        const isCurrentMonth = state.monthYear === now.getFullYear() && state.month === (now.getMonth() + 1);
        const nextBtn = document.getElementById('month-next');
        if (nextBtn) nextBtn.disabled = isCurrentMonth;

        // Render weekly activity chart
        renderMonthActivityChart(
            'month-activity-chart',
            'month-activity-labels',
            data.weekly_activity || [],
            data.weekly_breakdown?.labels || [],
            data.current_week_index
        );

        // Render stats
        const stats = data.stats || {};
        document.getElementById('month-stat-active').textContent = formatDurationHM(stats.active_seconds);
        document.getElementById('month-stat-avg').textContent = formatDurationHM(stats.avg_daily_seconds);
        document.getElementById('month-stat-break').textContent = formatDurationHM(stats.break_seconds);
        document.getElementById('month-stat-start').textContent = stats.typical_start || '--';
        document.getElementById('month-stat-end').textContent = stats.typical_end || '--';
        document.getElementById('month-stat-switches').textContent = stats.avg_context_switches || 0;
        document.getElementById('month-stat-focus').textContent = formatDurationHM(stats.longest_focus_seconds);
        document.getElementById('month-stat-days').textContent = stats.active_days || 0;

        // Goal progress
        const goalPct = Math.min(stats.goal_pct || 0, 100);
        document.getElementById('month-goal-fill').style.width = `${goalPct}%`;
        document.getElementById('month-goal-pct').textContent = `${goalPct}%`;

        // Peak hours
        renderPeakHours('month-peak-hours', data.peak_hours_avg || []);
        renderPeakInsight('month-peak-insight', data.peak_hours_avg || []);

        // Tags
        renderTags('month-tags-list', data.tags || []);

        // App distribution donut
        renderDonut('month-donut-chart', 'month-donut-legend', data.app_distribution || []);

        // Top windows
        renderWindows('month-windows-list', data.top_windows || []);

        // Weekly breakdown
        const breakdown = data.weekly_breakdown || {};
        renderWeeklyBreakdown('month-weekly-hours', breakdown.hours || [], breakdown.labels || [], data.current_week_index);
        renderWeeklyBreakdown('month-weekly-breaks', breakdown.breaks || [], breakdown.labels || [], data.current_week_index, true);
    }

    // ==================== Render Helpers ====================

    function renderActivityChart(containerId, data, count, currentIndex) {
        const container = document.getElementById(containerId);
        if (!container) return;

        const maxVal = Math.max(...data.filter(v => v != null), 1);

        let html = '';
        for (let i = 0; i < count; i++) {
            const val = data[i] || 0;
            const heightPct = maxVal > 0 ? Math.max((val / maxVal) * 100, val > 0 ? 5 : 0) : 0;
            const barClass = getBarClass(val, maxVal);
            const isCurrent = currentIndex !== null && i === currentIndex;

            html += `<div class="activity-bar ${barClass}${isCurrent ? ' current' : ''}"
                         style="height: ${heightPct}%"
                         title="${i}:00 - ${val}min"></div>`;
        }

        container.innerHTML = html;
    }

    function renderWeekActivityChart(containerId, data, todayIndex) {
        const container = document.getElementById(containerId);
        if (!container) return;

        const validData = data.filter(v => v !== null);
        const maxVal = validData.length > 0 ? Math.max(...validData, 1) : 1;

        let html = '';
        for (let i = 0; i < 7; i++) {
            const val = data[i];
            const isFuture = val === null;
            const isCurrent = todayIndex !== null && i === todayIndex;

            if (isFuture) {
                html += `<div class="activity-bar future" title="${DAY_LABELS[i]}: Future"></div>`;
            } else {
                const heightPct = maxVal > 0 ? Math.max((val / maxVal) * 100, val > 0 ? 5 : 0) : 0;
                const barClass = getBarClass(val, maxVal);
                html += `<div class="activity-bar ${barClass}${isCurrent ? ' current' : ''}"
                             style="height: ${heightPct}%"
                             title="${DAY_LABELS[i]}: ${formatDurationHM(val)}"></div>`;
            }
        }

        container.innerHTML = html;
    }

    function renderMonthActivityChart(containerId, labelsId, data, labels, currentIndex) {
        const container = document.getElementById(containerId);
        const labelsContainer = document.getElementById(labelsId);
        if (!container) return;

        const maxVal = Math.max(...data.filter(v => v != null), 1);

        let html = '';
        for (let i = 0; i < data.length; i++) {
            const val = data[i] || 0;
            const heightPct = maxVal > 0 ? Math.max((val / maxVal) * 100, val > 0 ? 5 : 0) : 0;
            const barClass = getBarClass(val, maxVal);
            const isCurrent = currentIndex !== null && i === currentIndex;
            const label = labels[i] || `W${i + 1}`;

            html += `<div class="activity-bar ${barClass}${isCurrent ? ' current' : ''}"
                         style="height: ${heightPct}%"
                         title="${label}: ${formatDurationHM(val)}"></div>`;
        }

        container.innerHTML = html;

        // Render labels
        if (labelsContainer) {
            labelsContainer.innerHTML = labels.map(l => `<span>${l}</span>`).join('');
        }
    }

    function renderPeakHours(containerId, data) {
        const container = document.getElementById(containerId);
        if (!container) return;

        let html = '';
        for (let i = 0; i < 24; i++) {
            const val = data[i] || 0;
            const heightPct = Math.max(val, val > 0 ? 5 : 2);
            const barClass = getPeakClass(val);

            html += `<div class="peak-bar ${barClass}" style="height: ${heightPct}%" title="${i}:00"></div>`;
        }

        container.innerHTML = html;
    }

    function renderPeakInsight(containerId, data) {
        const container = document.getElementById(containerId);
        if (!container) return;

        // Find peak hours (top 3)
        const indexed = data.map((val, i) => ({ hour: i, val }));
        const sorted = indexed.sort((a, b) => b.val - a.val);
        const peaks = sorted.slice(0, 3).filter(p => p.val > 0);

        if (peaks.length === 0) {
            container.textContent = 'No activity data available.';
            return;
        }

        const peakHours = peaks.map(p => formatHour(p.hour)).join(', ');
        container.textContent = `Peak focus hours: ${peakHours}`;
    }

    function renderTags(containerId, tags) {
        const container = document.getElementById(containerId);
        if (!container) return;

        if (!tags || tags.length === 0) {
            container.innerHTML = '<span class="no-tags">No tags generated</span>';
            return;
        }

        container.innerHTML = tags.map(tag =>
            `<span class="tag-item">${escapeHtml(tag.name)}<span class="tag-count">${tag.count}</span></span>`
        ).join('');
    }

    function renderDonut(chartId, legendId, apps) {
        const chart = document.getElementById(chartId);
        const legend = document.getElementById(legendId);
        if (!chart || !legend) return;

        if (!apps || apps.length === 0) {
            chart.style.background = 'var(--border)';
            legend.innerHTML = '<span class="no-windows">No app data</span>';
            return;
        }

        // Build conic gradient
        const segments = [];
        let cumulative = 0;

        apps.forEach(app => {
            const start = cumulative;
            cumulative += app.pct;
            segments.push(`${app.color} ${start}% ${cumulative}%`);
        });

        chart.style.background = `conic-gradient(${segments.join(', ')})`;

        // Build legend
        legend.innerHTML = apps.slice(0, 6).map(app =>
            `<div class="legend-item">
                <div class="legend-color" style="background: ${app.color}"></div>
                <span class="legend-name">${escapeHtml(app.app)}</span>
                <span class="legend-time">${formatDurationHM(app.seconds)}</span>
            </div>`
        ).join('');
    }

    function renderWindows(containerId, windows) {
        const container = document.getElementById(containerId);
        if (!container) return;

        if (!windows || windows.length === 0) {
            container.innerHTML = '<span class="no-windows">No window data</span>';
            return;
        }

        container.innerHTML = windows.slice(0, 6).map(w =>
            `<div class="window-item">
                <div class="window-info">
                    <div class="window-app">${escapeHtml(w.app)}</div>
                    <div class="window-title">${escapeHtml(w.title || 'Untitled')}</div>
                </div>
                <div class="window-time">${formatDurationHM(w.seconds)}</div>
            </div>`
        ).join('');
    }

    function renderDailyBreakdown(containerId, data, labels, todayIndex, isBreak = false) {
        const container = document.getElementById(containerId);
        if (!container) return;

        const validData = data.filter(v => v !== null);
        const maxVal = validData.length > 0 ? Math.max(...validData, 1) : 1;

        let html = '';
        for (let i = 0; i < 7; i++) {
            const val = data[i];
            const isFuture = val === null;
            const widthPct = !isFuture && maxVal > 0 ? Math.min((val / maxVal) * 100, 100) : 0;

            html += `<div class="breakdown-row${isFuture ? ' future' : ''}">
                <span class="breakdown-label">${labels[i]}</span>
                <div class="breakdown-bar-container">
                    <div class="breakdown-bar${isBreak ? ' break' : ''}" style="width: ${widthPct}%"></div>
                </div>
                <span class="breakdown-value">${isFuture ? '--' : formatDurationHM(val)}</span>
            </div>`;
        }

        container.innerHTML = html;
    }

    function renderWeeklyBreakdown(containerId, data, labels, currentIndex, isBreak = false) {
        const container = document.getElementById(containerId);
        if (!container) return;

        const maxVal = data.length > 0 ? Math.max(...data, 1) : 1;

        let html = '';
        for (let i = 0; i < data.length; i++) {
            const val = data[i] || 0;
            const widthPct = maxVal > 0 ? Math.min((val / maxVal) * 100, 100) : 0;
            const isCurrent = currentIndex !== null && i === currentIndex;

            html += `<div class="breakdown-row${isCurrent ? ' current' : ''}">
                <span class="breakdown-label">${labels[i] || `W${i + 1}`}</span>
                <div class="breakdown-bar-container">
                    <div class="breakdown-bar${isBreak ? ' break' : ''}" style="width: ${widthPct}%"></div>
                </div>
                <span class="breakdown-value">${formatDurationHM(val)}</span>
            </div>`;
        }

        container.innerHTML = html;
    }

    // ==================== Utility Functions ====================

    function formatDurationHM(seconds) {
        if (seconds == null || isNaN(seconds) || seconds === 0) return '0m';
        const hours = Math.floor(seconds / 3600);
        const mins = Math.round((seconds % 3600) / 60);
        if (hours === 0) return `${mins}m`;
        if (mins === 0) return `${hours}h`;
        return `${hours}h ${mins}m`;
    }

    function formatHour(hour) {
        if (hour === 0) return '12am';
        if (hour === 12) return '12pm';
        if (hour < 12) return `${hour}am`;
        return `${hour - 12}pm`;
    }

    function getBarClass(value, maxValue) {
        if (maxValue === 0 || value === 0) return 'low';
        const pct = (value / maxValue) * 100;
        if (pct >= 86) return 'peak';
        if (pct >= 51) return 'high';
        if (pct >= 21) return 'med';
        return 'low';
    }

    function getPeakClass(value) {
        if (value >= 75) return 'peak';
        if (value >= 50) return 'high';
        if (value >= 20) return 'med';
        return 'low';
    }

    // ISO week utilities
    function getISOWeek(date) {
        const d = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
        const dayNum = d.getUTCDay() || 7;
        d.setUTCDate(d.getUTCDate() + 4 - dayNum);
        const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
        return Math.ceil((((d - yearStart) / 86400000) + 1) / 7);
    }

    function getISOWeekYear(date) {
        const d = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
        const dayNum = d.getUTCDay() || 7;
        d.setUTCDate(d.getUTCDate() + 4 - dayNum);
        return d.getUTCFullYear();
    }

    function getDateFromISOWeek(year, week) {
        const simple = new Date(Date.UTC(year, 0, 1 + (week - 1) * 7));
        const dayOfWeek = simple.getUTCDay();
        const isoWeekStart = simple;
        if (dayOfWeek <= 4) {
            isoWeekStart.setUTCDate(simple.getUTCDate() - simple.getUTCDay() + 1);
        } else {
            isoWeekStart.setUTCDate(simple.getUTCDate() + 8 - simple.getUTCDay());
        }
        return isoWeekStart;
    }

})();
