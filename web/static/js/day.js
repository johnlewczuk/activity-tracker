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
        const hourStr = `${hour}:00`;
        const hourEnd = `${(parseInt(hour) + 1)}:00`;

        const groupDiv = document.createElement('div');
        groupDiv.className = 'hour-group';

        const headerDiv = document.createElement('div');
        headerDiv.className = 'hour-header';
        headerDiv.innerHTML = `
            <span class="hour-title">${hourStr} - ${hourEnd}</span>
            <span class="hour-count">${hourScreenshots.length} screenshot${hourScreenshots.length !== 1 ? 's' : ''}</span>
            <span class="hour-toggle">▼</span>
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

    // Image modal with keyboard navigation
    const modal = document.getElementById('imageModal');
    const modalImg = document.getElementById('modalImage');
    const closeBtn = document.querySelector('.modal-close');
    const prevBtn = document.getElementById('modalPrev');
    const nextBtn = document.getElementById('modalNext');
    const modalCounter = document.getElementById('modalCounter');
    const modalTime = document.getElementById('modalTime');
    const modalApp = document.getElementById('modalApp');
    const modalWindowTitle = document.getElementById('modalWindowTitle');
    const modalWindowTitleContainer = document.getElementById('modalWindowTitleContainer');

    let currentIndex = -1;
    let allScreenshots = [];

    // Build flat array of all screenshots in chronological order
    sortedHours.forEach(hour => {
        allScreenshots = allScreenshots.concat(hourlyGroups[hour]);
    });

    function updateModalContent(index) {
        if (index < 0 || index >= allScreenshots.length) return;

        currentIndex = index;
        const screenshot = allScreenshots[index];

        // Update image
        modalImg.src = `/screenshot/${screenshot.id}`;

        // Update metadata
        const time = new Date(screenshot.timestamp * 1000);
        modalCounter.textContent = `${index + 1} / ${allScreenshots.length}`;
        modalTime.textContent = time.toLocaleTimeString();
        modalApp.textContent = screenshot.app_name || 'Unknown';

        // Show window title if available
        if (screenshot.window_title && screenshot.window_title.trim()) {
            modalWindowTitle.textContent = screenshot.window_title;
            modalWindowTitleContainer.style.display = 'flex';
        } else {
            modalWindowTitleContainer.style.display = 'none';
        }

        // Update navigation buttons
        if (index === 0) {
            prevBtn.classList.add('disabled');
        } else {
            prevBtn.classList.remove('disabled');
        }

        if (index === allScreenshots.length - 1) {
            nextBtn.classList.add('disabled');
        } else {
            nextBtn.classList.remove('disabled');
        }
    }

    function showModal(screenshotId) {
        const index = allScreenshots.findIndex(s => s.id === screenshotId);
        if (index !== -1) {
            updateModalContent(index);
            modal.classList.add('active');
        }
    }

    function closeModal() {
        modal.classList.remove('active');
        currentIndex = -1;
    }

    function navigatePrev() {
        if (currentIndex > 0) {
            updateModalContent(currentIndex - 1);
        }
    }

    function navigateNext() {
        if (currentIndex < allScreenshots.length - 1) {
            updateModalContent(currentIndex + 1);
        }
    }

    // Click handlers
    document.addEventListener('click', (e) => {
        if (e.target.classList.contains('screenshot-img')) {
            const screenshotId = parseInt(e.target.dataset.id);
            showModal(screenshotId);
        }
    });

    closeBtn.onclick = closeModal;

    modal.onclick = (e) => {
        if (e.target === modal) {
            closeModal();
        }
    };

    prevBtn.onclick = (e) => {
        e.stopPropagation();
        if (!prevBtn.classList.contains('disabled')) {
            navigatePrev();
        }
    };

    nextBtn.onclick = (e) => {
        e.stopPropagation();
        if (!nextBtn.classList.contains('disabled')) {
            navigateNext();
        }
    };

    // Keyboard navigation
    document.addEventListener('keydown', (e) => {
        if (!modal.classList.contains('active')) return;

        switch(e.key) {
            case 'ArrowLeft':
                e.preventDefault();
                navigatePrev();
                break;
            case 'ArrowRight':
                e.preventDefault();
                navigateNext();
                break;
            case 'Escape':
                e.preventDefault();
                closeModal();
                break;
        }
    });
})();
