interface StatusBarProps {
  panelCount: number;
  wsConnected: boolean;
}

export function StatusBar({ panelCount, wsConnected }: StatusBarProps) {
  const tabText = panelCount === 1 ? '1 tab' : `${panelCount} tabs`;

  return (
    <div className="status-bar">
      <div className="status-bar-left">
        <span className={`status-indicator ${wsConnected ? 'connected' : 'disconnected'}`}>
          {wsConnected ? 'Connected' : 'Disconnected'}
        </span>
        <span className="status-separator">|</span>
        <span className={`ws-status ${wsConnected ? 'ok' : 'error'}`}>
          WebSocket: {wsConnected ? 'OK' : 'Error'}
        </span>
      </div>
      <div className="status-bar-right">
        <span className="tab-count">{tabText} open</span>
      </div>
    </div>
  );
}
