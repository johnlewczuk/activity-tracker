// Day Page JavaScript

(function() {
    // Get screenshots data from script tag
    const dataScript = document.getElementById('screenshots-data');
    if (!dataScript) return;

    const screenshots = JSON.parse(dataScript.textContent);
    if (!screenshots || screenshots.length === 0) return;

    const hourlyGroups = {};
    screenshots.forEach(screenshot => {
        const hour = new Date(screenshot.timestamp * 1000).getHours();
        if (!hourlyGroups[hour]) {
            hourlyGroups[hour] = [];
        }
        hourlyGroups[hour].push(screenshot);
    });

    // Render hourly groups
    const container = document.getElementById('hourlyGroups');
    const sortedHours = Object.keys(hourlyGroups).sort((a, b) => a - b);

    sortedHours.forEach(hour => {
        const hourScreenshots = hourlyGroups[hour];
        // Format hour string with AM/PM
        const hourStr = new Date(0, 0, 0, hour).toLocaleTimeString([], { hour: '2-digit', hour12: true });

        const groupDiv = document.createElement('div');
        groupDiv.className = 'hour-group';

        const headerDiv = document.createElement('div');
        headerDiv.className = 'hour-header';
        headerDiv.innerHTML = `
            <span class="hour-toggle">▼</span>
            <span class="hour-title">${hourStr}</span>
            <span class="hour-count">${hourScreenshots.length} screenshot${hourScreenshots.length !== 1 ? 's' : ''}</span>
        `;
        headerDiv.onclick = () => {
            groupDiv.classList.toggle('collapsed');
        };

        const contentDiv = document.createElement('div');
        contentDiv.className = 'hour-content';

        hourScreenshots.forEach(screenshot => {
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
        groupDiv.appendChild(contentDiv);
        container.appendChild(groupDiv);
    });

    // Collapse/Expand all functionality
    let allCollapsed = false;
    const collapseBtn = document.getElementById('collapseAllBtn');

    collapseBtn.addEventListener('click', () => {
        const groups = document.querySelectorAll('.hour-group');
        allCollapsed = !allCollapsed;

        groups.forEach(group => {
            if (allCollapsed) {
                group.classList.add('collapsed');
            } else {
                group.classList.remove('collapsed');
            }
        });

        collapseBtn.textContent = allCollapsed ? 'Expand All' : 'Collapse All';
    });

    // Build flat array of all screenshots in chronological order
    let allScreenshots = [];
    sortedHours.forEach(hour => {
        allScreenshots = allScreenshots.concat(hourlyGroups[hour]);
    });

    // Click handlers - use the shared screenshot modal
    document.addEventListener('click', (e) => {
        if (e.target.classList.contains('screenshot-img')) {
            const screenshotId = parseInt(e.target.dataset.id);
            ScreenshotModal.show(allScreenshots, screenshotId, { findById: true });
        }
    });
})();
