import { useEffect, useCallback, useRef } from 'react';
import type { IDockviewPanelProps } from 'dockview';
import { useTerminalSession } from '../hooks/useTerminalSession';
import { useToast } from '../context/ToastContext';
import { panelSessionMap } from '../App';
import '@xterm/xterm/css/xterm.css';

const SCROLL_SENSITIVITY = 20;

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

  const { termRef, sendResize, sendInput, sessionActive } = useTerminalSession({
    sessionId: params.sessionId,
    sessionName,
    onSessionInfo: handleSessionInfo,
    onStatusMessage: showToast,
  });

  const touchScrollRef = useRef<{ y: number; accumulated: number } | null>(null);
  const lastSizeRef = useRef<{ width: number; height: number } | null>(null);
  const resizeTimeoutRef = useRef<number | null>(null);

  useEffect(() => {
    const container = termRef.current;
    if (!container) return;

    const observer = new ResizeObserver((entries) => {
      const { width, height } = entries[0].contentRect;
      const w = Math.round(width);
      const h = Math.round(height);

      if (lastSizeRef.current?.width === w && lastSizeRef.current?.height === h) {
        return;
      }
      lastSizeRef.current = { width: w, height: h };

      if (resizeTimeoutRef.current) {
        clearTimeout(resizeTimeoutRef.current);
      }
      resizeTimeoutRef.current = window.setTimeout(() => {
        console.log(`[TerminalPanel ${panelId}] ResizeObserver: ${w}x${h}`);
        sendResize();
      }, 100);
    });
    observer.observe(container);

    return () => {
      observer.disconnect();
      if (resizeTimeoutRef.current) clearTimeout(resizeTimeoutRef.current);
    };
  }, [sendResize, panelId]);


  useEffect(() => {
    const container = termRef.current;
    if (!container) return;

    const handleTouchStart = (e: TouchEvent) => {
      if (e.touches.length !== 1) return;
      touchScrollRef.current = { y: e.touches[0].clientY, accumulated: 0 };
    };

    const handleTouchMove = (e: TouchEvent) => {
      if (!touchScrollRef.current || e.touches.length !== 1) return;

      const deltaY = touchScrollRef.current.y - e.touches[0].clientY;
      touchScrollRef.current.y = e.touches[0].clientY;
      touchScrollRef.current.accumulated += deltaY;

      const scrollLines = Math.trunc(touchScrollRef.current.accumulated / SCROLL_SENSITIVITY);
      if (scrollLines !== 0) {
        touchScrollRef.current.accumulated -= scrollLines * SCROLL_SENSITIVITY;
        const button = scrollLines > 0 ? 65 : 64;
        const count = Math.abs(scrollLines);
        for (let i = 0; i < count; i++) {
          sendInput(`\x1b[<${button};1;1M\x1b[<${button};1;1m`);
        }
      }
    };

    const handleTouchEnd = () => {
      touchScrollRef.current = null;
    };

    container.addEventListener('touchstart', handleTouchStart, { passive: true });
    container.addEventListener('touchmove', handleTouchMove, { passive: false });
    container.addEventListener('touchend', handleTouchEnd);
    container.addEventListener('touchcancel', handleTouchEnd);

    return () => {
      container.removeEventListener('touchstart', handleTouchStart);
      container.removeEventListener('touchmove', handleTouchMove);
      container.removeEventListener('touchend', handleTouchEnd);
      container.removeEventListener('touchcancel', handleTouchEnd);
    };
  }, [sendInput]);

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
