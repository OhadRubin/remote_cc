import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { DockviewReact, type DockviewReadyEvent, type IDockviewPanelProps, type IDockviewHeaderActionsProps } from 'dockview';
import { TerminalPanel, type TerminalPanelParams } from './components/TerminalPanel';
import { SessionsSidebar } from './components/SessionsSidebar';
import { StatusBar } from './components/StatusBar';
import { ContextMenu, type ContextMenuOption } from './components/ContextMenu';
import { InputModal, ConfirmModal } from './components/InputModal';
import { ToastContainer } from './components/Toast';
import { ToastProvider } from './context/ToastContext';
import { useLayoutSync } from './hooks/useLayoutSync';
import 'dockview/dist/styles/dockview.css';

const LONG_PRESS_DURATION = 500;
const SWIPE_THRESHOLD = 80;

function generatePanelId(): string {
  return `terminal-${crypto.randomUUID().slice(0, 8)}`;
}

export const panelSessionMap = new Map<string, number>();

interface ContextMenuState {
  x: number;
  y: number;
  panelId: string;
}

interface RenameModalState {
  panelId: string;
  currentName: string;
}

interface ConfirmModalState {
  panelId: string;
}

export function App() {
  const [dockviewApi, setDockviewApi] = useState<DockviewReadyEvent['api'] | null>(null);
  const [panelCount, setPanelCount] = useState(0);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null);
  const [renameModal, setRenameModal] = useState<RenameModalState | null>(null);
  const [confirmModal, setConfirmModal] = useState<ConfirmModalState | null>(null);
  const [viewportHeight, setViewportHeight] = useState<number | null>(null);
  const layoutSync = useLayoutSync(dockviewApi);

  const longPressTimeoutRef = useRef<number | null>(null);
  const touchStartRef = useRef<{ x: number; y: number } | null>(null);

  // Use visualViewport to track actual visible height (accounts for keyboard)
  useEffect(() => {
    const viewport = window.visualViewport;
    if (!viewport) {
      console.log('[VisualViewport] API not available');
      return;
    }

    const handleResize = () => {
      const height = Math.round(viewport.height);
      console.log(`[VisualViewport] resize: height=${height}`);
      setViewportHeight(prev => {
        if (prev === height) return prev;
        requestAnimationFrame(() => {
          window.dispatchEvent(new Event('resize'));
        });
        return height;
      });
    };

    // Set initial value
    handleResize();

    viewport.addEventListener('resize', handleResize);
    return () => viewport.removeEventListener('resize', handleResize);
  }, []);

  const updatePanelCount = useCallback(() => {
    if (dockviewApi) {
      setPanelCount(dockviewApi.panels.length);
    }
  }, [dockviewApi]);

  const attachToSession = useCallback((sessionId: number) => {
    if (!dockviewApi) return;
    const id = generatePanelId();
    const title = `Session ${sessionId}`;
    dockviewApi.addPanel({
      id,
      component: 'terminal',
      title,
      params: { sessionId },
    });
    updatePanelCount();
  }, [dockviewApi, updatePanelCount]);

  const onReady = useCallback((event: DockviewReadyEvent) => {
    setDockviewApi(event.api);

    event.api.onDidLayoutChange(() => {
      setPanelCount(event.api.panels.length);
    });
  }, []);

  const autoReconnectOrCreateTab = useCallback(async () => {
    if (!dockviewApi) return;
    let reconnected = false;
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
          const id = generatePanelId();
          const title = `Session ${data.session_id}`;
          dockviewApi.addPanel({
            id,
            component: 'terminal',
            title,
            params: { sessionId: data.session_id },
          });
          updatePanelCount();
          reconnected = true;
        }
      }
    } catch (e) {
      console.error('Failed to auto-reconnect:', e);
    }
    if (!reconnected) {
      const id = generatePanelId();
      dockviewApi.addPanel({
        id,
        component: 'terminal',
        title: 'New Tab',
      });
      updatePanelCount();
    }
  }, [dockviewApi, updatePanelCount]);

  useEffect(() => {
    if (!layoutSync.synced || !dockviewApi) return;
    if (dockviewApi.panels.length === 0) {
      autoReconnectOrCreateTab();
    }
  }, [layoutSync.synced, dockviewApi, autoReconnectOrCreateTab]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (!e.altKey || !dockviewApi) return;

      const panels = dockviewApi.panels;
      if (panels.length === 0) return;

      const activePanel = dockviewApi.activePanel;
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
  }, [dockviewApi]);

  useEffect(() => {
    let swipeStartX = 0;
    let swipeStartY = 0;

    const handleSwipeStart = (e: TouchEvent) => {
      const target = e.target as HTMLElement;
      if (target.closest('.dv-default-tab') || target.closest('.sidebar')) return;
      swipeStartX = e.touches[0].clientX;
      swipeStartY = e.touches[0].clientY;
    };

    const handleSwipeEnd = (e: TouchEvent) => {
      if (!dockviewApi || swipeStartX === 0) return;

      const dx = e.changedTouches[0].clientX - swipeStartX;
      const dy = e.changedTouches[0].clientY - swipeStartY;

      if (Math.abs(dy) > Math.abs(dx)) return;
      if (Math.abs(dx) < SWIPE_THRESHOLD) return;

      const panels = dockviewApi.panels;
      if (panels.length <= 1) return;

      const activePanel = dockviewApi.activePanel;
      if (!activePanel) return;

      const currentIndex = panels.findIndex(p => p.id === activePanel.id);
      const newIndex = dx > 0
        ? (currentIndex - 1 + panels.length) % panels.length
        : (currentIndex + 1) % panels.length;

      panels[newIndex].api.setActive();
      swipeStartX = 0;
    };

    document.addEventListener('touchstart', handleSwipeStart, { passive: true });
    document.addEventListener('touchend', handleSwipeEnd, { passive: true });

    return () => {
      document.removeEventListener('touchstart', handleSwipeStart);
      document.removeEventListener('touchend', handleSwipeEnd);
    };
  }, [dockviewApi]);

  useEffect(() => {
    const getPanelFromTab = (tab: Element) => {
      const panels = dockviewApi?.panels;
      if (!panels) return null;
      const tabContainer = tab.closest('.dv-tabs-container');
      if (!tabContainer) return null;
      const tabs = tabContainer.querySelectorAll('.dv-default-tab');
      const tabIndex = Array.from(tabs).indexOf(tab);
      if (tabIndex === -1 || tabIndex >= panels.length) return null;
      return panels[tabIndex];
    };

    const handleContextMenu = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      const tab = target.closest('.dv-default-tab');
      if (!tab || !dockviewApi) return;

      e.preventDefault();
      const panel = getPanelFromTab(tab);
      if (panel) {
        setContextMenu({ x: e.clientX, y: e.clientY, panelId: panel.id });
      }
    };

    const handleTouchStart = (e: TouchEvent) => {
      const target = e.target as HTMLElement;
      const tab = target.closest('.dv-default-tab');
      if (!tab || !dockviewApi) return;

      const touch = e.touches[0];
      touchStartRef.current = { x: touch.clientX, y: touch.clientY };

      longPressTimeoutRef.current = window.setTimeout(() => {
        const panel = getPanelFromTab(tab);
        if (panel && touchStartRef.current) {
          setContextMenu({ x: touchStartRef.current.x, y: touchStartRef.current.y, panelId: panel.id });
        }
        touchStartRef.current = null;
      }, LONG_PRESS_DURATION);
    };

    const handleTouchMove = (e: TouchEvent) => {
      if (!touchStartRef.current || !longPressTimeoutRef.current) return;
      const touch = e.touches[0];
      const dx = Math.abs(touch.clientX - touchStartRef.current.x);
      const dy = Math.abs(touch.clientY - touchStartRef.current.y);
      if (dx > 10 || dy > 10) {
        clearTimeout(longPressTimeoutRef.current);
        longPressTimeoutRef.current = null;
      }
    };

    const handleTouchEnd = () => {
      if (longPressTimeoutRef.current) {
        clearTimeout(longPressTimeoutRef.current);
        longPressTimeoutRef.current = null;
      }
      touchStartRef.current = null;
    };

    document.addEventListener('contextmenu', handleContextMenu);
    document.addEventListener('touchstart', handleTouchStart, { passive: true });
    document.addEventListener('touchmove', handleTouchMove, { passive: true });
    document.addEventListener('touchend', handleTouchEnd);
    document.addEventListener('touchcancel', handleTouchEnd);

    return () => {
      document.removeEventListener('contextmenu', handleContextMenu);
      document.removeEventListener('touchstart', handleTouchStart);
      document.removeEventListener('touchmove', handleTouchMove);
      document.removeEventListener('touchend', handleTouchEnd);
      document.removeEventListener('touchcancel', handleTouchEnd);
    };
  }, [dockviewApi]);

  const handleRenameTab = useCallback((panelId: string) => {
    const sessionId = panelSessionMap.get(panelId);
    if (sessionId === undefined) return;

    const panel = dockviewApi?.getPanel(panelId);
    const currentTitle = panel?.title?.replace(/ [\u25cf\u25cb]$/, '') ?? '';
    setRenameModal({ panelId, currentName: currentTitle });
  }, [dockviewApi]);

  const handleRenameSubmit = useCallback(async (newName: string) => {
    if (!renameModal) return;
    const { panelId } = renameModal;
    const sessionId = panelSessionMap.get(panelId);
    setRenameModal(null);

    if (sessionId === undefined) return;

    try {
      const response = await fetch(`/api/sessions/${sessionId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newName }),
      });
      if (!response.ok) {
        return;
      }
      const panel = dockviewApi?.getPanel(panelId);
      if (panel) {
        const currentTitle = panel.title || '';
        const indicator = currentTitle.match(/ [\u25cf\u25cb]$/)?.[0] || '';
        panel.api.setTitle(`${newName}${indicator}`);
      }
    } catch (err) {
      console.error('Failed to rename:', err);
    }
  }, [renameModal, dockviewApi]);

  const handleKillSession = useCallback((panelId: string) => {
    const sessionId = panelSessionMap.get(panelId);
    if (sessionId === undefined) return;
    setConfirmModal({ panelId });
  }, []);

  const handleKillConfirm = useCallback(async () => {
    if (!confirmModal) return;
    const { panelId } = confirmModal;
    const sessionId = panelSessionMap.get(panelId);
    setConfirmModal(null);

    if (sessionId === undefined) return;

    try {
      await fetch(`/api/sessions/${sessionId}`, {
        method: 'DELETE',
      });
    } catch (err) {
      console.error('Failed to kill session:', err);
    }
  }, [confirmModal]);

  const handleCloseTab = useCallback((panelId: string) => {
    const panel = dockviewApi?.getPanel(panelId);
    if (panel) {
      panel.api.close();
      panelSessionMap.delete(panelId);
      updatePanelCount();
    }
  }, [dockviewApi, updatePanelCount]);

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
        const id = generatePanelId();
        const title = 'New Tab';
        props.containerApi.addPanel({
          id,
          component: 'terminal',
          title,
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
            </>
          )}
        </div>
      );
    };
  }, []);

  const containerHeight = viewportHeight !== null ? `${viewportHeight}px` : '100dvh';

  return (
    <ToastProvider>
      <div style={{ display: 'flex', flexDirection: 'column', height: containerHeight }}>
        <div style={{ flex: 1, position: 'relative' }}>
          <DockviewReact
            className="dockview-theme-dark"
            onReady={onReady}
            components={components}
            prefixHeaderActionsComponent={PrefixHeaderActions}
            leftHeaderActionsComponent={LeftHeaderActions}
            rightHeaderActionsComponent={RightHeaderActions}
          />
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
        {renameModal && (
          <InputModal
            title="Rename Session"
            placeholder="Session name"
            defaultValue={renameModal.currentName}
            onSubmit={handleRenameSubmit}
            onCancel={() => setRenameModal(null)}
          />
        )}
        {confirmModal && (
          <ConfirmModal
            title="Kill Session"
            message="Kill this session? The shell process will be terminated."
            confirmLabel="Kill"
            danger
            onConfirm={handleKillConfirm}
            onCancel={() => setConfirmModal(null)}
          />
        )}
        <ToastContainer />
      </div>
    </ToastProvider>
  );
}
