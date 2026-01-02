# Change: Add Timeline Keyboard Navigation

## Why
The horizontal activity timeline supports mouse-based panning and zooming, but lacks keyboard navigation for:
- Accessibility (users who prefer or require keyboard-only navigation)
- Precision (fine-grained seeking is difficult with mouse drag)
- Power users (faster navigation with keyboard shortcuts)

Currently, keyboard shortcuts only work for day/month navigation, not for timeline seeking within a day.

## What Changes
- Add keyboard shortcuts for horizontal timeline pan left/right
- Add keyboard shortcuts for zoom in/out on timeline
- Add keyboard shortcut to center on current time (if viewing today)
- Add visual indicator when timeline receives keyboard focus
- Update keyboard hints section to include new shortcuts

## Impact
- Affected specs: timeline-ui
- Affected code: web/templates/timeline.html (JavaScript event handlers, CSS for focus indicator)
- No breaking changes
- No backend changes required
