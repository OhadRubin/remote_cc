import { useEffect, useRef, useState } from 'react';
import * as Y from 'yjs';
import { WebsocketProvider } from 'y-websocket';
import type { DockviewApi } from 'dockview';

export function useLayoutSync(dockviewApi: DockviewApi | null) {
  const ydocRef = useRef<Y.Doc | null>(null);
  const providerRef = useRef<WebsocketProvider | null>(null);
  const [synced, setSynced] = useState(false);
  const lastSavedRef = useRef<string | null>(null);

  useEffect(() => {
    const ydoc = new Y.Doc();
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const provider = new WebsocketProvider(
      `${wsProtocol}//${window.location.host}/yjs`,
      'layout',
      ydoc
    );

    ydocRef.current = ydoc;
    providerRef.current = provider;
    provider.on('sync', (isSynced: boolean) => setSynced(isSynced));

    return () => {
      provider.destroy();
      ydoc.destroy();
    };
  }, []);

  useEffect(() => {
    if (!synced || !dockviewApi || !ydocRef.current) return;

    const layoutMap = ydocRef.current.getMap<string>('layout');

    const applyRemote = () => {
      const data = layoutMap.get('data');
      if (!data || data === lastSavedRef.current) return;

      lastSavedRef.current = data;
      try {
        dockviewApi.fromJSON(JSON.parse(data), { reuseExistingPanels: true });
      } catch (e) {
        console.error('Failed to apply layout:', e);
      }
    };

    applyRemote();

    layoutMap.observe(applyRemote);
    return () => layoutMap.unobserve(applyRemote);
  }, [synced, dockviewApi]);

  useEffect(() => {
    if (!dockviewApi || !ydocRef.current) return;

    const layoutMap = ydocRef.current.getMap<string>('layout');

    const disposable = dockviewApi.onDidLayoutChange(() => {
      const serialized = JSON.stringify(dockviewApi.toJSON());
      if (serialized === lastSavedRef.current) return;
      lastSavedRef.current = serialized;
      layoutMap.set('data', serialized);
    });

    return () => disposable.dispose();
  }, [dockviewApi]);

  return { synced };
}
