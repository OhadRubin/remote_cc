interface ToolbarProps {
  onNewTab: () => void;
  onShowSessions: () => void;
  onCloseAll: () => void;
  panelCount: number;
}

export function Toolbar({ onNewTab, onShowSessions, onCloseAll, panelCount }: ToolbarProps) {
  return (
    <div className="toolbar">
      <button onClick={onNewTab}>+ New Tab</button>
      <button onClick={onShowSessions}>Sessions</button>
      {panelCount > 0 && (
        <button onClick={onCloseAll}>Close All</button>
      )}
      <span className="status">{panelCount} tab(s) open</span>
    </div>
  );
}
