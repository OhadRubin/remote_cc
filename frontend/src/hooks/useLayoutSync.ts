import { useEffect, useRef, useState } from 'react';
import * as Y from 'yjs';
import { WebsocketProvider } from 'y-websocket';
import type { DockviewApi } from 'dockview';

const scaleSizes = (node: any, scale: number, altScale: number): any => {
  const result = { ...node };
  if (typeof result.size === 'number') result.size = Math.round(result.size * scale);
  if (Array.isArray(result.data)) result.data = result.data.map((n: any) => scaleSizes(n, altScale, scale));
  return result;
};

let debugSeq = 0;
const dbg = (msg: string, ...args: unknown[]) => console.log(`[LayoutSync ${++debugSeq}] ${msg}`, ...args);

export function useLayoutSync(dockviewApi: DockviewApi | null) {
  const ydocRef = useRef<Y.Doc | null>(null);
  const providerRef = useRef<WebsocketProvider | null>(null);
  const [synced, setSynced] = useState(false);
  const lastSavedRef = useRef<string | null>(null);
  const applyingRemoteRef = useRef(false);
  const applyingUntilRef = useRef<number>(0);

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
      dbg('applyRemote called', { hasData: !!data, sameAsLast: data === lastSavedRef.current });
      if (!data || data === lastSavedRef.current) return;

      lastSavedRef.current = data;
      try {
        const parsed = JSON.parse(data);
        const scaleW = dockviewApi.width / parsed.grid.width;
        const scaleH = dockviewApi.height / parsed.grid.height;
        dbg('applyRemote scaling', {
          localSize: { w: dockviewApi.width, h: dockviewApi.height },
          remoteSize: { w: parsed.grid.width, h: parsed.grid.height },
          scales: { scaleW, scaleH }
        });
        const isHoriz = parsed.grid.orientation === 'HORIZONTAL';
        const grid = { ...parsed.grid, width: dockviewApi.width, height: dockviewApi.height, root: scaleSizes(parsed.grid.root, isHoriz ? scaleH : scaleW, isHoriz ? scaleW : scaleH) };
        applyingRemoteRef.current = true;
        applyingUntilRef.current = Date.now() + 500; // Suppress saves for 500ms after apply
        dbg('applyRemote: calling fromJSON, flag=true, suppressUntil=+500ms');
        dockviewApi.fromJSON({ ...parsed, grid }, { reuseExistingPanels: true });
        dbg('applyRemote: fromJSON returned, flag still=true');
        applyingRemoteRef.current = false;
        dbg('applyRemote: flag=false (timestamp suppression still active)');
      } catch (e) {
        applyingRemoteRef.current = false;
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
      const now = Date.now();
      const suppressedByFlag = applyingRemoteRef.current;
      const suppressedByTime = now < applyingUntilRef.current;
      dbg('onDidLayoutChange fired', { suppressedByFlag, suppressedByTime, msRemaining: applyingUntilRef.current - now });

      if (suppressedByFlag || suppressedByTime) {
        dbg('onDidLayoutChange: SUPPRESSED');
        return;
      }

      const serialized = JSON.stringify(dockviewApi.toJSON());
      const sameAsLast = serialized === lastSavedRef.current;
      dbg('onDidLayoutChange: checking serialized', { sameAsLast, len: serialized.length });

      if (sameAsLast) return;

      lastSavedRef.current = serialized;
      dbg('onDidLayoutChange: SAVING to Yjs');
      layoutMap.set('data', serialized);
    });

    return () => disposable.dispose();
  }, [dockviewApi]);

  return { synced };
}
