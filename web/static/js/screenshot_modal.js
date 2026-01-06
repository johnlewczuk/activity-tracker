/**
 * Shared Screenshot Modal Component
 *
 * Usage:
 *   // Show modal with screenshots array starting at index
 *   ScreenshotModal.show(screenshots, startIndex, options);
 *
 *   // Close modal programmatically
 *   ScreenshotModal.close();
 *
 * Options:
 *   - viewAllUrl: string - URL for "View all screenshots" link (hidden if not provided)
 *   - onClose: function - Callback when modal closes
 */

window.ScreenshotModal = (function() {
    let state = {
        screenshots: [],
        currentIndex: -1,
        triggerElement: null,
        keyHandler: null,
        options: {}
    };

    // DOM element references (cached on first use)
    let elements = null;

    function getElements() {
        if (elements) return elements;

        elements = {
            modal: document.getElementById('screenshotModal'),
            image: document.getElementById('screenshotModalImage'),
            closeBtn: document.getElementById('screenshotModalClose'),
            prevBtn: document.getElementById('screenshotModalPrev'),
            nextBtn: document.getElementById('screenshotModalNext'),
            counter: document.getElementById('screenshotModalCounter'),
            time: document.getElementById('screenshotModalTime'),
            app: document.getElementById('screenshotModalApp'),
            windowTitleContainer: document.getElementById('screenshotModalWindowTitleContainer'),
            windowTitle: document.getElementById('screenshotModalWindowTitle'),
            viewAllLink: document.getElementById('screenshotModalViewAllLink'),
            preloadPrev: document.getElementById('screenshotModalPreloadPrev'),
            preloadNext: document.getElementById('screenshotModalPreloadNext')
        };

        return elements;
    }

    function init() {
        const el = getElements();
        if (!el.modal) return;

        // Click handlers
        el.closeBtn.addEventListener('click', close);
        el.modal.addEventListener('click', (e) => {
            if (e.target === el.modal) close();
        });

        el.prevBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            if (!el.prevBtn.classList.contains('disabled')) {
                navigatePrev();
            }
        });

        el.nextBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            if (!el.nextBtn.classList.contains('disabled')) {
                navigateNext();
            }
        });
    }

    function updateContent(index) {
        const el = getElements();
        if (index < 0 || index >= state.screenshots.length) return;

        state.currentIndex = index;
        const screenshot = state.screenshots[index];

        // Update image
        el.image.src = `/screenshot/${screenshot.id}`;

        // Update metadata
        const time = new Date(screenshot.timestamp * 1000);
        el.counter.textContent = `${index + 1} / ${state.screenshots.length}`;
        el.time.textContent = time.toLocaleTimeString();
        el.app.textContent = screenshot.app_name || 'Unknown';

        // Show window title if available
        if (screenshot.window_title && screenshot.window_title.trim()) {
            el.windowTitle.textContent = screenshot.window_title;
            el.windowTitleContainer.style.display = 'flex';
        } else {
            el.windowTitleContainer.style.display = 'none';
        }

        // Update navigation buttons
        el.prevBtn.classList.toggle('disabled', index === 0);
        el.nextBtn.classList.toggle('disabled', index === state.screenshots.length - 1);

        // Update view all link
        if (state.options.viewAllUrl) {
            el.viewAllLink.href = state.options.viewAllUrl;
            el.viewAllLink.style.display = 'block';
        } else {
            el.viewAllLink.style.display = 'none';
        }

        // Preload adjacent images
        preloadAdjacentImages();
    }

    function preloadAdjacentImages() {
        const el = getElements();
        const total = state.screenshots.length;
        if (total <= 1) return;

        if (state.currentIndex > 0) {
            el.preloadPrev.src = `/screenshot/${state.screenshots[state.currentIndex - 1].id}`;
        }
        if (state.currentIndex < total - 1) {
            el.preloadNext.src = `/screenshot/${state.screenshots[state.currentIndex + 1].id}`;
        }
    }

    function navigatePrev() {
        if (state.currentIndex > 0) {
            updateContent(state.currentIndex - 1);
        }
    }

    function navigateNext() {
        if (state.currentIndex < state.screenshots.length - 1) {
            updateContent(state.currentIndex + 1);
        }
    }

    function handleKeyDown(e) {
        const el = getElements();
        if (!el.modal.classList.contains('active')) return;

        switch (e.key) {
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
                close();
                break;
        }
    }

    /**
     * Show the screenshot modal
     * @param {Array} screenshots - Array of screenshot objects with id, timestamp, app_name, window_title
     * @param {number} startIndex - Index to start at (or screenshot ID if findById option is true)
     * @param {Object} options - Optional settings
     * @param {string} options.viewAllUrl - URL for "View all" link
     * @param {boolean} options.findById - If true, startIndex is treated as a screenshot ID to find
     * @param {function} options.onClose - Callback when modal closes
     */
    function show(screenshots, startIndex, options = {}) {
        const el = getElements();
        if (!el.modal || !screenshots || screenshots.length === 0) return;

        state.screenshots = screenshots;
        state.options = options;
        state.triggerElement = document.activeElement;

        // Find index by screenshot ID if requested
        let index = startIndex;
        if (options.findById) {
            index = screenshots.findIndex(s => s.id === startIndex);
            if (index === -1) return;
        }

        updateContent(index);
        el.modal.classList.add('active');
        el.modal.setAttribute('aria-hidden', 'false');

        // Add keyboard handler
        if (state.keyHandler) {
            document.removeEventListener('keydown', state.keyHandler);
        }
        state.keyHandler = handleKeyDown;
        document.addEventListener('keydown', state.keyHandler);
    }

    /**
     * Close the screenshot modal
     */
    function close() {
        const el = getElements();
        if (!el.modal) return;

        el.modal.classList.remove('active');
        el.modal.setAttribute('aria-hidden', 'true');
        state.currentIndex = -1;

        // Return focus to trigger element
        if (state.triggerElement) {
            state.triggerElement.focus();
            state.triggerElement = null;
        }

        // Remove keyboard handler
        if (state.keyHandler) {
            document.removeEventListener('keydown', state.keyHandler);
            state.keyHandler = null;
        }

        // Call onClose callback if provided
        if (state.options.onClose) {
            state.options.onClose();
        }
    }

    /**
     * Check if modal is currently active
     */
    function isActive() {
        const el = getElements();
        return el.modal && el.modal.classList.contains('active');
    }

    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // Public API
    return {
        show,
        close,
        isActive
    };
})();
