import { useEffect, useRef, useState, useCallback } from 'react';
import * as Y from 'yjs';
import { WebsocketProvider } from 'y-websocket';
import type { DockviewApi } from 'dockview';

interface PanelState {
  id: string;
  component: string;
  title: string;
  params?: Record<string, unknown>;
}

export function useLayoutSync(dockviewApi: DockviewApi | null) {
  const ydocRef = useRef<Y.Doc | null>(null);
  const providerRef = useRef<WebsocketProvider | null>(null);
  const [connected, setConnected] = useState(false);
  const [synced, setSynced] = useState(false);
  const isLocalChangeRef = useRef(false);

  useEffect(() => {
    const ydoc = new Y.Doc();
    ydocRef.current = ydoc;

    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const provider = new WebsocketProvider(
      `${wsProtocol}//${window.location.host}/yjs`,
      'layout',
      ydoc
    );
    providerRef.current = provider;

    provider.on('status', (event: { status: string }) => {
      setConnected(event.status === 'connected');
    });

    provider.on('sync', (isSynced: boolean) => {
      setSynced(isSynced);
    });

    return () => {
      provider.destroy();
      ydoc.destroy();
    };
  }, []);

  useEffect(() => {
    if (!ydocRef.current || !dockviewApi) return;

    const panelsArray = ydocRef.current.getArray<PanelState>('panels');

    const observer = () => {
      if (isLocalChangeRef.current) {
        isLocalChangeRef.current = false;
        return;
      }

      const remotePanels = panelsArray.toArray();
      const localPanelIds = new Set(dockviewApi.panels.map(p => p.id));
      const remotePanelIds = new Set(remotePanels.map(p => p.id));

      for (const panel of remotePanels) {
        if (!localPanelIds.has(panel.id)) {
          dockviewApi.addPanel({
            id: panel.id,
            component: panel.component,
            title: panel.title,
            params: panel.params,
          });
        }
      }

      for (const panel of dockviewApi.panels) {
        if (!remotePanelIds.has(panel.id)) {
          panel.api.close();
        }
      }

      for (const panel of remotePanels) {
        const localPanel = dockviewApi.getPanel(panel.id);
        if (localPanel && localPanel.title !== panel.title) {
          localPanel.api.setTitle(panel.title);
        }
      }
    };

    panelsArray.observe(observer);
    return () => panelsArray.unobserve(observer);
  }, [dockviewApi]);

  const addPanel = useCallback((panel: PanelState) => {
    if (!ydocRef.current) return;
    isLocalChangeRef.current = true;
    const panelsArray = ydocRef.current.getArray<PanelState>('panels');
    panelsArray.push([panel]);
  }, []);

  const removePanel = useCallback((panelId: string) => {
    if (!ydocRef.current) return;
    isLocalChangeRef.current = true;
    const panelsArray = ydocRef.current.getArray<PanelState>('panels');
    const index = panelsArray.toArray().findIndex(p => p.id === panelId);
    if (index !== -1) {
      panelsArray.delete(index, 1);
    }
  }, []);

  const updatePanelTitle = useCallback((panelId: string, title: string) => {
    if (!ydocRef.current) return;
    isLocalChangeRef.current = true;
    const panelsArray = ydocRef.current.getArray<PanelState>('panels');
    const arr = panelsArray.toArray();
    const index = arr.findIndex(p => p.id === panelId);
    if (index !== -1) {
      const panel = arr[index];
      panelsArray.delete(index, 1);
      panelsArray.insert(index, [{ ...panel, title }]);
    }
  }, []);

  const clearAllPanels = useCallback(() => {
    if (!ydocRef.current) return;
    isLocalChangeRef.current = true;
    const panelsArray = ydocRef.current.getArray<PanelState>('panels');
    panelsArray.delete(0, panelsArray.length);
  }, []);

  const getPanels = useCallback((): PanelState[] => {
    if (!ydocRef.current) return [];
    const panelsArray = ydocRef.current.getArray<PanelState>('panels');
    return panelsArray.toArray();
  }, []);

  return {
    connected,
    synced,
    addPanel,
    removePanel,
    updatePanelTitle,
    clearAllPanels,
    getPanels,
  };
}
