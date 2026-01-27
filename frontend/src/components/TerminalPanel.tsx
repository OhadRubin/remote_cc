import { useEffect, useCallback } from 'react';
import type { IDockviewPanelProps } from 'dockview';
import { useTerminalSession } from '../hooks/useTerminalSession';
import '@xterm/xterm/css/xterm.css';

export interface TerminalPanelParams {
  sessionId?: number;
}

export function TerminalPanel(props: IDockviewPanelProps<TerminalPanelParams>) {
  const { api, params } = props;

  const handleSessionInfo = useCallback(
    (info: { name: string }) => {
      const suffix = params.sessionId !== undefined ? ' (reconnected)' : '';
      api.setTitle(`${info.name}${suffix}`);
    },
    [api, params.sessionId]
  );

  const { termRef, sendResize, sessionActive } = useTerminalSession({
    sessionId: params.sessionId,
    onSessionInfo: handleSessionInfo,
  });

  useEffect(() => {
    const disposable = api.onDidDimensionsChange(() => {
      sendResize();
    });
    return () => disposable.dispose();
  }, [api, sendResize]);

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
