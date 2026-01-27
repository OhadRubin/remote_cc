
# Remote CC Features

## Sessions Sidebar (Collapsible)

- **Session list** - Named sessions (e.g., "api-gateway", "frontend-watch", "frontend-mods") 
- **Orphaned session indicator** - Warning icon (⚠) on sessions that not connected, clicking on them creates a new tab and attaches to them.
- **Delete button** - Kill orphaned/stale sessions - upon clicking we get a confirmation pop up
- **Settings icon** - Access preferences at bottom of sidebar

## Tab Bar

- **Multiple tabs** - Open multiple terminal sessions simultaneously
- **Status indicators** - Green dot for connected, visual feedback for tab state
- **Close button (X)** - Close individual tabs
- **"+" button** - Add new tabs (opens a new session)
- **Right-click context menu**:
  - Rename - Change tab name
  - Kill - Kill the session in the remote
  - Close Tab - Close the tab

## Status Bar

- **Connection status** - "Connected" / "Disconnected" indicators with colored dots
- **WebSocket status** - Shows "WebSocket: OK" when healthy
- **Tab count** - Displays number of open tabs (e.g., "3 tab(s) open")


# Feature Critique: Why These Features Are Needed

Based on hands-on exploration of the current Terminal Mux interface at cc.ohadrubin.com, this document explains the rationale behind each proposed feature.

---

## Sessions Sidebar (Collapsible)

### Session List with Named Sessions
**Current Problem:** Sessions are identified by cryptic auto-generated names like `session-mkw9ikm3`. When the Sessions modal is opened, users see a list of meaningless identifiers.

**Why Needed:**
- Users managing multiple remote processes (API server, frontend watcher, log tails) cannot quickly identify which session is which
- Forces users to click through tabs to find the right one
- Named sessions like "api-gateway" or "frontend-watch" provide instant context

### Orphaned Session Indicator (⚠)
**Current Problem:** The Sessions modal shows "0 attached" for some sessions, but there's no visual distinction between healthy and orphaned sessions. Users must parse the "X attached" text to understand session state.

**Why Needed:**
- Orphaned sessions consume server resources
- Without clear indication, users may reconnect to dead shells expecting them to work
- A warning icon provides instant visual triage of session health
- Clicking to auto-attach is a sensible recovery action

### Delete Button for Sessions
**Current Problem:** No way to clean up stale sessions from the UI. The Sessions modal is read-only.

**Why Needed:**
- Sessions accumulate over time (I observed 6 sessions, some with 0 attachments)
- Server-side processes may continue running indefinitely
- Users need control to kill processes without SSH access
- Confirmation popup prevents accidental termination of active work

### Settings Icon
**Current Problem:** No visible preferences or configuration options.

**Why Needed:**
- Terminal customization (font size, color scheme) is expected
- Keyboard shortcut configuration
- Default session naming patterns
- Connection timeout settings

---

## Tab Bar

### Multiple Tabs
**Current State:** Already implemented. Users can have multiple session tabs open.

**Why Still Listed:** Core functionality that must be preserved.

### Status Indicators
**Current Problem:** Inconsistent visual feedback. Some tabs show "(reconnected)" text suffix, others show a colored dot. The first tab I viewed showed error messages but no clear status indicator on the tab itself.

**Why Needed:**
- Green dot for connected provides at-a-glance health check
- Users shouldn't need to click each tab to discover which sessions have problems
- Consistent visual language reduces cognitive load

### Close Button (X)
**Current State:** Exists but is small and hard to target.

**Why Needed:**
- Standard UI pattern users expect
- Current implementation could benefit from larger click target

### "+" Button Behavior
**Current Problem:** Clicking "+ New Tab" produced no visible feedback during testing. No loading indicator, no modal, no new tab appeared immediately.

**Why Needed:**
- Clear expectation: click + and get a new session
- Current behavior is confusing and feels broken
- Should create new session immediately or show clear feedback

### Right-Click Context Menu
**Current Problem:** No context menu exists. Tab management is limited.

**Why Needed:**
- **Rename:** Essential for making tabs identifiable without sidebar
- **Kill:** Terminate remote process without closing local tab (allows viewing final output)
- **Close Tab:** Alternative to X button, follows desktop app conventions

---

## Status Bar

### Connection Status Indicator
**Current Problem:** No visible indication of connection health to the server. When I first loaded the page, tabs showed "Reconnecting to session..." but there was no global connection indicator.

**Why Needed:**
- Users need to know if disconnects are session-specific or global
- Prevents confusion when multiple tabs fail simultaneously
- "Disconnected" indicator prompts users to check network

### WebSocket Status
**Current Problem:** No visibility into WebSocket health. Users can't distinguish between:
- Network issues
- Server issues
- Individual session issues

**Why Needed:**
- "WebSocket: OK" confirms the transport layer is healthy
- Helps debug connection issues
- Professional terminal multiplexers (tmux status line) show this info

### Tab Count
**Current State:** Already implemented ("3 tab(s) open" visible in top-right).

**Why Still Listed:** Confirms this should be preserved. Minor polish: grammar fix for "1 tab(s)" → "1 tab".

---

## Summary

The proposed features address three core gaps in the current implementation:

1. **Discoverability** - Named sessions, status indicators, and connection health make the app state immediately visible
2. **Control** - Delete sessions, context menus, and settings give users power over their environment
3. **Polish** - Consistent visual feedback, proper button states, and standard UI patterns create a professional experience

These aren't feature creep—they're baseline expectations for a terminal multiplexer UI that's meant to replace direct tmux access.

