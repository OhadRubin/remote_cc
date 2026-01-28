import { useEffect, useCallback } from 'react';
import type { IDockviewPanelProps } from 'dockview';
import { useTerminalSession } from '../hooks/useTerminalSession';
import { useToast } from '../context/ToastContext';
import { panelSessionMap } from '../App';
import '@xterm/xterm/css/xterm.css';

export interface TerminalPanelParams {
  sessionId?: number;
  sessionName?: string;
}

export function TerminalPanel(props: IDockviewPanelProps<TerminalPanelParams>) {
  const { api, params } = props;
  const panelId = api.id;
  const { showToast } = useToast();

  const handleSessionInfo = useCallback(
    (info: { name: string; sessionId: number }) => {
      const suffix = params.sessionId !== undefined ? ' (reconnected)' : '';
      api.setTitle(`${info.name}${suffix}`);
      panelSessionMap.set(panelId, info.sessionId);
      api.updateParameters({ sessionId: info.sessionId });
    },
    [api, params.sessionId, panelId]
  );

  useEffect(() => {
    return () => {
      panelSessionMap.delete(panelId);
    };
  }, [panelId]);

  const sessionName = params.sessionName ?? `panel-${panelId}`;

  const { termRef, sendResize, sessionActive } = useTerminalSession({
    sessionId: params.sessionId,
    sessionName,
    onSessionInfo: handleSessionInfo,
    onStatusMessage: showToast,
  });

  useEffect(() => {
    const container = termRef.current;
    if (!container) return;

    const observer = new ResizeObserver(() => {
      sendResize();
    });
    observer.observe(container);
    return () => observer.disconnect();
  }, [sendResize]);

  useEffect(() => {
    const currentTitle = api.title || 'Terminal';
    const indicator = sessionActive ? ' \u25cf' : ' \u25cb';
    const baseTitle = currentTitle.replace(/ [\u25cf\u25cb]$/, '');
    api.setTitle(`${baseTitle}${indicator}`);
  }, [api, sessionActive]);

  return (
    <div
      ref={termRef}
      style={{
        width: '100%',
        height: '100%',
        backgroundColor: '#1a1a2e',
      }}
    />
  );
}
