# Capability: Real-Time Activity Widget

Provides live, streaming updates of current activity, flow status, and productivity metrics via Server-Sent Events (SSE).

## ADDED Requirements

### Requirement: SSE Activity Stream

The system SHALL provide a Server-Sent Events endpoint for real-time activity updates.

#### Scenario: Client connects to SSE stream

- **GIVEN** a client connects to `/api/stream/activity`
- **WHEN** the connection is established
- **THEN** the server SHALL begin sending events every 1 second
- **AND** Content-Type SHALL be `text/event-stream`
- **AND** Cache-Control SHALL be `no-cache`

#### Scenario: Activity update sent

- **GIVEN** an SSE connection is active
- **WHEN** 1 second passes
- **THEN** an event SHALL be sent with current activity data
- **AND** event format SHALL be: `data: {json}\n\n`

#### Scenario: Client disconnects

- **GIVEN** an SSE connection is active
- **WHEN** the client disconnects
- **THEN** the server SHALL stop sending events for that connection
- **AND** resources SHALL be cleaned up

### Requirement: Real-Time Activity Data

The system SHALL include comprehensive activity data in each SSE update.

#### Scenario: Activity data structure

- **GIVEN** an SSE update is sent
- **THEN** it SHALL include:
  ```json
  {
    "timestamp": "2024-01-15T14:32:15",
    "current_app": "code",
    "current_window": "VSCode - main.py",
    "session_active": true,
    "session_duration_minutes": 45,
    "flow_status": {...},
    "today_stats": {...}
  }
  ```

#### Scenario: Flow status in update

- **GIVEN** user is currently in flow
- **THEN** flow_status SHALL include:
  ```json
  {
    "in_flow": true,
    "duration_minutes": 28,
    "flow_score": 65,
    "context_switches": 1,
    "primary_app": "code"
  }
  ```

#### Scenario: Building toward flow

- **GIVEN** user has been focused for 12 minutes (not yet flow)
- **THEN** flow_status SHALL include:
  ```json
  {
    "in_flow": false,
    "building_flow": true,
    "potential_minutes": 12,
    "minutes_to_flow": 8
  }
  ```

### Requirement: Today's Statistics in Stream

The system SHALL include today's aggregated statistics in each update.

#### Scenario: Today stats structure

- **GIVEN** an SSE update is sent
- **THEN** today_stats SHALL include:
  ```json
  {
    "tracked_minutes": 180,
    "flow_minutes": 65,
    "flow_sessions": 2,
    "context_switches": 23,
    "distraction_minutes": 12
  }
  ```

### Requirement: Change Detection

The system SHALL only send updates when data has changed (to reduce bandwidth).

#### Scenario: No change, still send heartbeat

- **GIVEN** user is idle but SSE connection active
- **WHEN** 30 seconds pass with no activity change
- **THEN** a heartbeat event SHALL be sent: `event: heartbeat\ndata: {}\n\n`
- **AND** connection SHALL remain open

#### Scenario: Data changed, send update

- **GIVEN** user switches from VSCode to Firefox
- **WHEN** the next SSE cycle runs
- **THEN** a full data event SHALL be sent
- **AND** current_app and current_window SHALL reflect the change

### Requirement: Current Activity Snapshot API

The system SHALL provide a non-streaming endpoint for one-time activity snapshots.

#### Scenario: Get current snapshot

- **GIVEN** user is active and in flow
- **WHEN** `GET /api/current` is called
- **THEN** the response SHALL match the SSE data structure
- **AND** be a single JSON response (not streaming)

#### Scenario: No active session

- **GIVEN** user is AFK (no active session)
- **WHEN** `GET /api/current` is called
- **THEN** the response SHALL include:
  ```json
  {
    "session_active": false,
    "afk": true,
    "last_activity": "2024-01-15T14:00:00"
  }
  ```

### Requirement: SSE Reconnection Support

The system SHALL support client reconnection with state resumption.

#### Scenario: Client reconnects

- **GIVEN** SSE connection was lost
- **WHEN** client reconnects with `Last-Event-ID` header
- **THEN** server SHALL resume sending events
- **AND** send a full state update immediately

#### Scenario: Stale connection cleanup

- **GIVEN** an SSE connection has been inactive for 60 seconds
- **WHEN** cleanup runs
- **THEN** the connection SHALL be closed
- **AND** resources SHALL be freed

### Requirement: Real-Time Widget UI Component

The system SHALL provide a JavaScript client for consuming the SSE stream.

#### Scenario: Widget initialization

- **GIVEN** `realtime.js` is loaded
- **WHEN** `initRealtimeWidget('#container')` is called
- **THEN** an SSE connection SHALL be established
- **AND** the widget SHALL render in the specified container

#### Scenario: Widget updates on data

- **GIVEN** widget is connected and rendering
- **WHEN** an SSE event is received
- **THEN** the widget SHALL update to show:
  - Current app icon and name
  - Session duration timer
  - Flow status indicator (in-flow, building, not in flow)
  - Today's flow/switch counts

#### Scenario: Widget handles disconnect

- **GIVEN** SSE connection is lost
- **WHEN** the widget detects disconnect
- **THEN** it SHALL show a "Reconnecting..." indicator
- **AND** attempt reconnection with exponential backoff

### Requirement: Flow Progress Visualization

The system SHALL provide visual feedback on flow state progress.

#### Scenario: Flow progress bar

- **GIVEN** user has been focused for 15 minutes (75% to flow)
- **WHEN** the widget renders
- **THEN** a progress bar SHALL show 75% filled
- **AND** label SHALL show "5 min to flow"

#### Scenario: In-flow indicator

- **GIVEN** user is in active flow
- **WHEN** the widget renders
- **THEN** the flow indicator SHALL show:
  - Green "IN FLOW" badge
  - Current flow duration
  - Current flow score
  - Unobtrusive design (doesn't distract)

## API Endpoints

```
GET /api/stream/activity
  SSE stream of real-time activity updates
  Headers: Accept: text/event-stream

GET /api/current
  One-time snapshot of current activity
  Returns: JSON with current state
```

## Files

- `web/app.py` - SSE endpoint implementation
- `web/static/js/realtime.js` - SSE client and widget
- `web/templates/includes/realtime-widget.html` - Widget markup template
- `web/static/css/realtime.css` - Widget styling
