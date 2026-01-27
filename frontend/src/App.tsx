import { useRef, useState, useEffect, useCallback, useMemo } from 'react';
import { DockviewReact, type DockviewReadyEvent, type IDockviewPanelProps, type IDockviewHeaderActionsProps } from 'dockview';
import { TerminalPanel, type TerminalPanelParams } from './components/TerminalPanel';
import { SessionsSidebar } from './components/SessionsSidebar';
import { StatusBar } from './components/StatusBar';
import { ContextMenu, type ContextMenuOption } from './components/ContextMenu';
import 'dockview/dist/styles/dockview.css';

let panelCounter = 0;

export const panelSessionMap = new Map<string, number>();

interface ContextMenuState {
  x: number;
  y: number;
  panelId: string;
}

export function App() {
  const dockviewRef = useRef<DockviewReadyEvent['api'] | null>(null);
  const [panelCount, setPanelCount] = useState(0);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null);

  const updatePanelCount = useCallback(() => {
    if (dockviewRef.current) {
      setPanelCount(dockviewRef.current.panels.length);
    }
  }, []);

  const attachToSession = useCallback((sessionId: number) => {
    if (!dockviewRef.current) return;
    panelCounter++;
    const id = `terminal-${panelCounter}`;
    dockviewRef.current.addPanel({
      id,
      component: 'terminal',
      title: `Tab ${panelCounter} (reconnect)`,
      params: { sessionId },
    });
    updatePanelCount();
  }, [updatePanelCount]);

  const closeAllPanels = useCallback(() => {
    if (!dockviewRef.current) return;
    const panels = [...dockviewRef.current.panels];
    panels.forEach((panel) => panel.api.close());
    localStorage.removeItem('terminal-layout');
    updatePanelCount();
  }, [updatePanelCount]);

  const onReady = useCallback((event: DockviewReadyEvent) => {
    dockviewRef.current = event.api;

    event.api.onDidLayoutChange(() => {
      updatePanelCount();
      if (event.api.panels.length > 0) {
        localStorage.setItem('terminal-layout', JSON.stringify(event.api.toJSON()));
      }
    });

    const saved = localStorage.getItem('terminal-layout');
    if (saved) {
      try {
        event.api.fromJSON(JSON.parse(saved));
        updatePanelCount();
        return;
      } catch (e) {
        console.warn('Failed to restore layout:', e);
      }
    }

    autoReconnectLastSession(event.api);
  }, [updatePanelCount]);

  const autoReconnectLastSession = async (api: DockviewReadyEvent['api']) => {
    try {
      const response = await fetch('/api/last-session');
      const data = await response.json();
      if (data.session_id !== null) {
        const sessionsResponse = await fetch('/api/sessions');
        const sessionsData = await sessionsResponse.json();
        const sessionExists = sessionsData.sessions.some(
          (s: { id: number }) => s.id === data.session_id
        );
        if (sessionExists) {
          panelCounter++;
          api.addPanel({
            id: `terminal-${panelCounter}`,
            component: 'terminal',
            title: `Tab ${panelCounter} (reconnect)`,
            params: { sessionId: data.session_id },
          });
          updatePanelCount();
        }
      }
    } catch (e) {
      console.error('Failed to auto-reconnect:', e);
    }
  };

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (!e.altKey || !dockviewRef.current) return;

      const panels = dockviewRef.current.panels;
      if (panels.length === 0) return;

      const activePanel = dockviewRef.current.activePanel;
      if (!activePanel) return;

      const currentIndex = panels.findIndex((p) => p.id === activePanel.id);
      let newIndex = currentIndex;

      if (e.key === 'ArrowLeft' || e.key === 'h') {
        newIndex = (currentIndex - 1 + panels.length) % panels.length;
      } else if (e.key === 'ArrowRight' || e.key === 'l') {
        newIndex = (currentIndex + 1) % panels.length;
      } else if (e.key === 'ArrowUp' || e.key === 'k') {
        newIndex = (currentIndex - 1 + panels.length) % panels.length;
      } else if (e.key === 'ArrowDown' || e.key === 'j') {
        newIndex = (currentIndex + 1) % panels.length;
      } else {
        return;
      }

      e.preventDefault();
      panels[newIndex].api.setActive();
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  useEffect(() => {
    const handleContextMenu = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      const tab = target.closest('.dv-default-tab');
      if (!tab || !dockviewRef.current) return;

      e.preventDefault();

      const panels = dockviewRef.current.panels;
      const tabContainer = tab.closest('.dv-tabs-container');
      if (!tabContainer) return;

      const tabs = tabContainer.querySelectorAll('.dv-default-tab');
      const tabIndex = Array.from(tabs).indexOf(tab);
      if (tabIndex === -1 || tabIndex >= panels.length) return;

      const panel = panels[tabIndex];
      setContextMenu({ x: e.clientX, y: e.clientY, panelId: panel.id });
    };

    document.addEventListener('contextmenu', handleContextMenu);
    return () => document.removeEventListener('contextmenu', handleContextMenu);
  }, []);

  const handleRenameTab = useCallback(async (panelId: string) => {
    const sessionId = panelSessionMap.get(panelId);
    if (sessionId === undefined) {
      alert('Session not yet initialized');
      return;
    }

    const newName = prompt('Enter new session name:');
    if (!newName) return;

    try {
      const response = await fetch(`/api/sessions/${sessionId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newName }),
      });
      if (!response.ok) {
        const data = await response.json();
        alert(data.error || 'Failed to rename session');
        return;
      }
      const panel = dockviewRef.current?.getPanel(panelId);
      if (panel) {
        const currentTitle = panel.title || '';
        const indicator = currentTitle.match(/ [\u25cf\u25cb]$/)?.[0] || '';
        panel.api.setTitle(`${newName}${indicator}`);
      }
    } catch (err) {
      console.error('Failed to rename:', err);
      alert('Failed to rename session');
    }
  }, []);

  const handleKillSession = useCallback(async (panelId: string) => {
    const sessionId = panelSessionMap.get(panelId);
    if (sessionId === undefined) {
      alert('Session not yet initialized');
      return;
    }

    if (!confirm('Kill this session? The shell process will be terminated.')) {
      return;
    }

    try {
      const response = await fetch(`/api/sessions/${sessionId}`, {
        method: 'DELETE',
      });
      if (!response.ok) {
        const data = await response.json();
        alert(data.error || 'Failed to kill session');
      }
    } catch (err) {
      console.error('Failed to kill session:', err);
      alert('Failed to kill session');
    }
  }, []);

  const handleCloseTab = useCallback((panelId: string) => {
    const panel = dockviewRef.current?.getPanel(panelId);
    if (panel) {
      panel.api.close();
      panelSessionMap.delete(panelId);
      updatePanelCount();
    }
  }, [updatePanelCount]);

  const getContextMenuOptions = useCallback((panelId: string): ContextMenuOption[] => {
    return [
      { label: 'Rename', onClick: () => handleRenameTab(panelId) },
      { label: 'Kill', onClick: () => handleKillSession(panelId), danger: true },
      { label: 'Close Tab', onClick: () => handleCloseTab(panelId) },
    ];
  }, [handleRenameTab, handleKillSession, handleCloseTab]);

  const components = {
    terminal: (props: IDockviewPanelProps<TerminalPanelParams>) => <TerminalPanel {...props} />,
  };

  const LeftHeaderActions = useMemo(() => {
    return function LeftHeaderActionsComponent(props: IDockviewHeaderActionsProps) {
      const onClick = () => {
        panelCounter++;
        props.containerApi.addPanel({
          id: `terminal-${panelCounter}`,
          component: 'terminal',
          title: `Tab ${panelCounter}`,
          position: { referenceGroup: props.group },
        });
        updatePanelCount();
      };
      return (
        <div className="group-control">
          <div className="action" onClick={onClick} title="New Tab">
            <span className="material-symbols-outlined">add</span>
          </div>
        </div>
      );
    };
  }, [updatePanelCount]);

  const PrefixHeaderActions = useMemo(() => {
    return function PrefixHeaderActionsComponent(_props: IDockviewHeaderActionsProps) {
      return (
        <div className="group-control">
          <div className="action" onClick={() => setSidebarOpen(true)} title="Sessions">
            <span className="material-symbols-outlined">menu</span>
          </div>
        </div>
      );
    };
  }, []);

  const RightHeaderActions = useMemo(() => {
    return function RightHeaderActionsComponent(props: IDockviewHeaderActionsProps) {
      const onMaximize = () => {
        if (props.containerApi.hasMaximizedGroup()) {
          props.containerApi.exitMaximizedGroup();
        } else {
          props.activePanel?.api.maximize();
        }
      };
      const isMaximized = props.containerApi.hasMaximizedGroup();
      return (
        <div className="group-control">
          {props.isGroupActive && props.panels.length > 0 && (
            <>
              <div className="action" onClick={onMaximize} title={isMaximized ? 'Restore' : 'Maximize'}>
                <span className="material-symbols-outlined">
                  {isMaximized ? 'collapse_content' : 'expand_content'}
                </span>
              </div>
              <div className="action action-danger" onClick={closeAllPanels} title="Close All">
                <span className="material-symbols-outlined">close</span>
              </div>
            </>
          )}
        </div>
      );
    };
  }, [closeAllPanels]);

  const createFirstTab = useCallback(() => {
    if (!dockviewRef.current) return;
    panelCounter++;
    dockviewRef.current.addPanel({
      id: `terminal-${panelCounter}`,
      component: 'terminal',
      title: `Tab ${panelCounter}`,
    });
    updatePanelCount();
  }, [updatePanelCount]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh' }}>
      <div style={{ flex: 1, position: 'relative' }}>
        <DockviewReact
          className="dockview-theme-dark"
          onReady={onReady}
          components={components}
          prefixHeaderActionsComponent={PrefixHeaderActions}
          leftHeaderActionsComponent={LeftHeaderActions}
          rightHeaderActionsComponent={RightHeaderActions}
        />
        {panelCount === 0 && (
          <div className="empty-state">
            <button onClick={createFirstTab} className="empty-state-btn">
              + New Tab
            </button>
            <button onClick={() => setSidebarOpen(true)} className="empty-state-btn">
              Sessions
            </button>
          </div>
        )}
      </div>
      <StatusBar panelCount={panelCount} wsConnected={panelCount > 0} />
      <SessionsSidebar
        isOpen={sidebarOpen}
        onToggle={() => setSidebarOpen(!sidebarOpen)}
        onSelectSession={attachToSession}
      />
      {contextMenu && (
        <ContextMenu
          x={contextMenu.x}
          y={contextMenu.y}
          options={getContextMenuOptions(contextMenu.panelId)}
          onClose={() => setContextMenu(null)}
        />
      )}
    </div>
  );
}
