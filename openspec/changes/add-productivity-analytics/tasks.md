# Implementation Tasks: Productivity Analytics Suite

## Phase 1: Core Analytics Foundation

### 1.1 Database Schema
- [ ] 1.1.1 Add `flow_sessions` table to storage.py
- [ ] 1.1.2 Add `context_switches` table to storage.py
- [ ] 1.1.3 Add `distraction_events` table to storage.py
- [ ] 1.1.4 Add `daily_analytics` cache table to storage.py
- [ ] 1.1.5 Add indexes for efficient querying
- [ ] 1.1.6 Test database migrations on existing DB

### 1.2 Flow Detection Module
- [ ] 1.2.1 Create `tracker/flow_detector.py`
- [ ] 1.2.2 Implement `FlowSession` dataclass
- [ ] 1.2.3 Implement `ContextSwitch` dataclass
- [ ] 1.2.4 Implement `FlowDetector` class with:
  - [ ] on_focus_change hook integration
  - [ ] Flow state tracking (start, end, building)
  - [ ] Flow score calculation algorithm
  - [ ] Related-app detection for same-family switching
  - [ ] Context switch classification (productive/distraction/neutral)
- [ ] 1.2.5 Implement flow session persistence
- [ ] 1.2.6 Implement context switch persistence
- [ ] 1.2.7 Add flow status methods (is_in_flow, get_flow_status)
- [ ] 1.2.8 Add AFK handling (flush flow on session end)

### 1.3 Flow Detector Integration
- [ ] 1.3.1 Modify `tracker/daemon.py` to instantiate FlowDetector
- [ ] 1.3.2 Hook FlowDetector into WindowWatcher callbacks
- [ ] 1.3.3 Handle daemon restart (resume tracking state)
- [ ] 1.3.4 Add configuration options to config.py:
  - [ ] flow_detection.enabled
  - [ ] flow_detection.min_duration_minutes
  - [ ] flow_detection.max_switches_per_30min
  - [ ] distraction_apps list

### 1.4 Analytics Module
- [ ] 1.4.1 Create `tracker/analytics.py`
- [ ] 1.4.2 Implement flow session query functions
- [ ] 1.4.3 Implement context switch query functions
- [ ] 1.4.4 Implement daily flow statistics aggregation
- [ ] 1.4.5 Implement daily context switch aggregation
- [ ] 1.4.6 Implement daily analytics cache computation
- [ ] 1.4.7 Implement period comparison (this week vs last week)
- [ ] 1.4.8 Implement baseline calculation (4-week rolling average)
- [ ] 1.4.9 Implement trend detection (improving/stable/declining)
- [ ] 1.4.10 Implement anomaly detection (deviation from baseline)

### 1.5 Phase 1 API Endpoints
- [ ] 1.5.1 `GET /api/analytics/flow` - Query flow sessions
- [ ] 1.5.2 `GET /api/analytics/flow/current` - Current flow status
- [ ] 1.5.3 `GET /api/analytics/flow/daily` - Daily flow stats
- [ ] 1.5.4 `GET /api/analytics/switches` - Query context switches
- [ ] 1.5.5 `GET /api/analytics/switches/interruptors` - Top interruptor apps
- [ ] 1.5.6 `GET /api/analytics/compare` - Period comparison
- [ ] 1.5.7 `GET /api/analytics/baseline` - Baseline values
- [ ] 1.5.8 `POST /api/analytics/refresh` - Refresh daily cache

### 1.6 Phase 1 Tests
- [ ] 1.6.1 Unit tests for FlowDetector
- [ ] 1.6.2 Unit tests for flow score calculation
- [ ] 1.6.3 Unit tests for context switch classification
- [ ] 1.6.4 Unit tests for analytics aggregation
- [ ] 1.6.5 Integration tests for API endpoints
- [ ] 1.6.6 Test with existing focus event data

## Phase 2: Real-Time & Distraction Awareness

### 2.1 Distraction Detection Module
- [ ] 2.1.1 Create `tracker/distractions.py`
- [ ] 2.1.2 Implement distraction app matching
- [ ] 2.1.3 Implement distraction event recording
- [ ] 2.1.4 Track `during_flow` for flow interruptions
- [ ] 2.1.5 Integrate with FlowDetector
- [ ] 2.1.6 Implement distraction aggregation functions
- [ ] 2.1.7 Implement distraction threshold warnings

### 2.2 Distraction Configuration
- [ ] 2.2.1 Add distraction_detection section to config.py
- [ ] 2.2.2 Add distraction apps list to settings UI
- [ ] 2.2.3 Add warning threshold configuration
- [ ] 2.2.4 Persist distraction preferences

### 2.3 Real-Time SSE Endpoint
- [ ] 2.3.1 Create SSE generator function in app.py
- [ ] 2.3.2 Implement `/api/stream/activity` endpoint
- [ ] 2.3.3 Add change detection to reduce bandwidth
- [ ] 2.3.4 Add heartbeat for connection keepalive
- [ ] 2.3.5 Handle client disconnection gracefully
- [ ] 2.3.6 Add `/api/current` snapshot endpoint

### 2.4 Real-Time JavaScript Client
- [ ] 2.4.1 Create `web/static/js/realtime.js`
- [ ] 2.4.2 Implement SSE connection management
- [ ] 2.4.3 Implement reconnection with exponential backoff
- [ ] 2.4.4 Implement widget rendering functions
- [ ] 2.4.5 Create flow progress visualization
- [ ] 2.4.6 Create in-flow/building-flow indicators
- [ ] 2.4.7 Add disconnect/reconnecting UI states

### 2.5 Real-Time Widget Styling
- [ ] 2.5.1 Add widget styles to shared.css
- [ ] 2.5.2 Create flow progress bar design
- [ ] 2.5.3 Create in-flow badge design
- [ ] 2.5.4 Ensure non-intrusive design (doesn't distract from work)
- [ ] 2.5.5 Add dark/light theme support

### 2.6 Phase 2 API Endpoints
- [ ] 2.6.1 `GET /api/analytics/distractions` - Query distraction events
- [ ] 2.6.2 `GET /api/analytics/distractions/summary` - Daily distraction summary
- [ ] 2.6.3 `GET /api/stream/activity` - SSE activity stream
- [ ] 2.6.4 `GET /api/current` - Current activity snapshot

### 2.7 Phase 2 Tests
- [ ] 2.7.1 Unit tests for distraction detection
- [ ] 2.7.2 Unit tests for SSE event generation
- [ ] 2.7.3 Integration tests for distraction API
- [ ] 2.7.4 Manual testing of real-time widget
- [ ] 2.7.5 Test SSE reconnection behavior

## Phase 3: Insights & Standup

### 3.1 Insight Generation Module
- [ ] 3.1.1 Create `tracker/insights.py`
- [ ] 3.1.2 Implement insight rule engine
- [ ] 3.1.3 Implement time-of-day productivity rule
- [ ] 3.1.4 Implement context switching rules
- [ ] 3.1.5 Implement improvement detection rules
- [ ] 3.1.6 Implement concern detection rules
- [ ] 3.1.7 Implement peak hours detection
- [ ] 3.1.8 Implement top interruptors insight
- [ ] 3.1.9 Add confidence scoring
- [ ] 3.1.10 Add insight deduplication
- [ ] 3.1.11 Add actionable suggestions to insights

### 3.2 Insight API
- [ ] 3.2.1 `GET /api/insights` - Query insights
- [ ] 3.2.2 `POST /api/insights/generate` - Force generation
- [ ] 3.2.3 `GET /api/insights/rules` - List active rules

### 3.3 Insights UI
- [ ] 3.3.1 Create `web/templates/insights.html`
- [ ] 3.3.2 Add insights section to analytics dashboard
- [ ] 3.3.3 Design insight cards with categories
- [ ] 3.3.4 Add priority-based sorting
- [ ] 3.3.5 Add filter by category

### 3.4 Standup Generation
- [ ] 3.4.1 Add standup generation function to reports.py
- [ ] 3.4.2 Implement activity consolidation logic
- [ ] 3.4.3 Implement project/context grouping
- [ ] 3.4.4 Implement meeting detection
- [ ] 3.4.5 Implement time estimation formatting
- [ ] 3.4.6 Add standup templates (default, simple, detailed)

### 3.5 Standup API
- [ ] 3.5.1 `GET /api/standup` - Get standup data
- [ ] 3.5.2 `POST /api/standup/copy` - Copy to clipboard format
- [ ] 3.5.3 `GET /api/standup/history` - Past standups
- [ ] 3.5.4 `GET /api/standup/templates` - Available templates

### 3.6 Standup UI
- [ ] 3.6.1 Create `web/templates/standup.html`
- [ ] 3.6.2 Create `web/static/js/standup.js`
- [ ] 3.6.3 Implement yesterday section (from activity data)
- [ ] 3.6.4 Implement editable today/blockers sections
- [ ] 3.6.5 Implement copy-to-clipboard
- [ ] 3.6.6 Add date selector
- [ ] 3.6.7 Add template selector
- [ ] 3.6.8 Add navigation link from main UI

### 3.7 Phase 3 Tests
- [ ] 3.7.1 Unit tests for insight rules
- [ ] 3.7.2 Unit tests for insight confidence
- [ ] 3.7.3 Unit tests for standup generation
- [ ] 3.7.4 Unit tests for activity consolidation
- [ ] 3.7.5 Integration tests for API endpoints
- [ ] 3.7.6 Manual testing of standup workflow

## Integration & Polish

### 4.1 Configuration Integration
- [ ] 4.1.1 Add all new config options to settings page
- [ ] 4.1.2 Document new configuration options
- [ ] 4.1.3 Add sensible defaults

### 4.2 Navigation & UI Polish
- [ ] 4.2.1 Add Insights to navigation
- [ ] 4.2.2 Add Standup to navigation
- [ ] 4.2.3 Add real-time widget to header/sidebar
- [ ] 4.2.4 Update analytics dashboard with new metrics

### 4.3 Documentation
- [ ] 4.3.1 Update CLAUDE.md with new features
- [ ] 4.3.2 Update API documentation
- [ ] 4.3.3 Add usage examples

### 4.4 Final Testing
- [ ] 4.4.1 Full integration test of all features
- [ ] 4.4.2 Performance testing (ensure no daemon slowdown)
- [ ] 4.4.3 Test with fresh database
- [ ] 4.4.4 Test with existing database migration
- [ ] 4.4.5 Browser compatibility testing
