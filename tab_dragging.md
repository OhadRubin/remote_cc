# Tab Dragging

Tab dragging enables fluid tab manipulation:
- Drag a tab out of the window to spawn a new window
- Drag tabs between windows to merge them

---

## Mechanism Overview

Tab dragging operates on a **detach-attach** model. A tab is not simply moved visually—it is detached from its parent tab strip, exists temporarily as a floating entity, and then attaches to a target (either a new window, an existing window's tab strip, or back to its origin).

## User Actions

| Action | Trigger | Result |
|--------|---------|--------|
| Reorder within window | Drag tab horizontally within tab strip | Tab changes position in the same window |
| Tear off to new window | Drag tab beyond vertical threshold | New window spawns containing the tab |
| Merge into existing window | Drag tab onto another window's tab strip | Tab joins the target window |
| Cancel drag | return to origin | Tab snaps back to original position |

## Workflow / State Machine

```
[IDLE]
   │
   ▼ (mousedown on tab)
[DRAG_PENDING]
   │
   ▼ (mouse moves beyond threshold)
[DRAGGING_WITHIN_STRIP]
   │
   ├──▶ (horizontal movement) ──▶ Reorder tabs, update indices
   │
   ▼ (vertical movement exceeds detach threshold)
[DETACHED]
   │
   ├──▶ (dragged over another window's tab strip) ──▶ [ATTACHING_TO_TARGET]
   │
   ├──▶ (dragged to empty space) ──▶ [CREATING_NEW_WINDOW]
   │
   ▼ (mouseup)
[COMMIT]
   │
   ▼
[IDLE]
```

## Implementation Details

### 1. Drag Threshold Detection

Dragging doesn't start immediately on mousedown. A small movement threshold (typically 4-8 pixels) prevents accidental drags when clicking.

```
if (distance(mousedown_pos, current_pos) > DRAG_THRESHOLD) {
    enter_drag_mode()
}
```

### 2. Hit Testing

During a drag, the system continuously checks:
- Is the cursor over a tab strip? (which window? what index?)
- Is the cursor in the "detach zone" (below/above the tab strip)?
- Is the cursor over empty desktop space?

### 3. Visual Feedback

- **Dragging within strip**: Other tabs slide left/right to show insertion point
- **Detached state**: A semi-transparent "ghost window" follows the cursor showing preview
- **Over target strip**: Target strip highlights the insertion gap

### 4. Tab Data Transfer

When a tab detaches, its associated data must transfer:
- The underlying process/content (terminal session, document, etc.)
- Session history or state
- Tab metadata (pinned, muted, title, etc.)

The underlying process is **re-parented**, not destroyed and recreated. This preserves state and avoids reloading content.

### 5. Window Creation (Tear-off)

When dropping a detached tab into empty space:
1. Create new window at cursor position
2. Attach the detached tab's content/process to the new window
3. Size the new window to match the original (or use default dimensions)

### 6. Z-Order Management

During drag, windows may need to be raised/lowered:
- The source window stays in place
- Target windows highlight when hovered
- The ghost preview renders above all windows

## Edge Cases

- **Pinned tabs**: Often restricted from being dragged out, or dragged only to the pinned section
- **Single tab window**: Dragging the only tab out may close the original window
- **Cross-context drag**: May be disallowed between isolated contexts (e.g., different user profiles or security domains)
- **Mid-drag window close**: If target window closes while dragging over it, fall back to new window creation     