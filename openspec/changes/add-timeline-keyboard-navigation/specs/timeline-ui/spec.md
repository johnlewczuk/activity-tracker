## ADDED Requirements

### Requirement: Timeline Keyboard Navigation
The horizontal activity timeline SHALL support keyboard-based navigation for panning and zooming.

The system SHALL respond to the following keyboard shortcuts when the timeline has focus:
- `j` or `a` SHALL pan the timeline view left (earlier in time)
- `k` or `d` SHALL pan the timeline view right (later in time)
- `-` or `[` SHALL zoom out (show more time)
- `+` or `=` or `]` SHALL zoom in (show less time, more detail)
- `0` SHALL reset zoom to show the full day (00:00-24:00)
- `n` SHALL center the view on the current time (only when viewing today's date)

#### Scenario: User pans timeline with keyboard
- **WHEN** the horizontal timeline has keyboard focus
- **AND** user presses `j` or `a`
- **THEN** the timeline view shifts left (earlier in time) by 10% of current view duration

#### Scenario: User zooms timeline with keyboard
- **WHEN** the horizontal timeline has keyboard focus
- **AND** user presses `+` or `=` or `]`
- **THEN** the timeline zooms in by 50%, centered on the current view center

#### Scenario: User resets timeline zoom
- **WHEN** the horizontal timeline has keyboard focus
- **AND** user presses `0`
- **THEN** the timeline resets to show the full day (00:00-24:00)

#### Scenario: User jumps to current time
- **WHEN** viewing today's date in the timeline
- **AND** user presses `n`
- **THEN** the timeline centers on the current time
- **AND** if zoomed out, the view zooms to 2-hour window around current time


### Requirement: Timeline Focus Indicator
The timeline SHALL display a visible focus indicator when it has keyboard focus.

#### Scenario: Timeline receives focus
- **WHEN** user tabs to or clicks on the timeline
- **THEN** a visible focus ring appears around the timeline container
- **AND** the timeline becomes ready to receive keyboard input

#### Scenario: Keyboard hints update
- **WHEN** the timeline page loads
- **THEN** the keyboard hints section at the bottom displays timeline navigation shortcuts
