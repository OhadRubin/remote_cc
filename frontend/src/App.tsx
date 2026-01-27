import { useRef, useState, useEffect, useCallback } from 'react';
import { DockviewReact, type DockviewReadyEvent, type IDockviewPanelProps } from 'dockview';
import { TerminalPanel, type TerminalPanelParams } from './components/TerminalPanel';
import { SessionsModal } from './components/SessionsModal';
import { Toolbar } from './components/Toolbar';
import 'dockview/dist/styles/dockview.css';

let panelCounter = 0;

export function App() {
  const dockviewRef = useRef<DockviewReadyEvent['api'] | null>(null);
  const [panelCount, setPanelCount] = useState(0);
  const [showSessions, setShowSessions] = useState(false);

  const updatePanelCount = useCallback(() => {
    if (dockviewRef.current) {
      setPanelCount(dockviewRef.current.panels.length);
    }
  }, []);

  const addNewTab = useCallback(() => {
    if (!dockviewRef.current) return;
    panelCounter++;
    const id = `terminal-${panelCounter}`;
    dockviewRef.current.addPanel({
      id,
      component: 'terminal',
      title: `Tab ${panelCounter}`,
    });
    updatePanelCount();
  }, [updatePanelCount]);

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
    updatePanelCount();
  }, [updatePanelCount]);

  const onReady = useCallback((event: DockviewReadyEvent) => {
    dockviewRef.current = event.api;

    event.api.onDidLayoutChange(() => {
      updatePanelCount();
      localStorage.setItem('terminal-layout', JSON.stringify(event.api.toJSON()));
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

  const components = {
    terminal: (props: IDockviewPanelProps<TerminalPanelParams>) => <TerminalPanel {...props} />,
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh' }}>
      <Toolbar
        onNewTab={addNewTab}
        onShowSessions={() => setShowSessions(true)}
        onCloseAll={closeAllPanels}
        panelCount={panelCount}
      />
      <div style={{ flex: 1 }}>
        <DockviewReact
          className="dockview-theme-dark"
          onReady={onReady}
          components={components}
        />
      </div>
      <SessionsModal
        isOpen={showSessions}
        onClose={() => setShowSessions(false)}
        onSelectSession={attachToSession}
      />
    </div>
  );
}
