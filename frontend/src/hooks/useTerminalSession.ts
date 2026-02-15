import { useRef, useState, useEffect, useCallback } from 'react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import type { SessionInfo } from '../types';
import type { ToastType } from '../context/ToastContext';
import {
  MessageType,
  encodeIdentify,
  encodeNewSession,
  encodeAttach,
  encodeInput,
  encodeResize,
  decodeMessages,
  decodeSessionInfo,
} from '../protocol';

interface UseTerminalSessionOptions {
  sessionId?: number;
  sessionName?: string;
  onSessionInfo?: (info: SessionInfo) => void;
  onStatusMessage?: (message: string, type: ToastType) => void;
}

interface UseTerminalSessionResult {
  termRef: React.RefObject<HTMLDivElement | null>;
  term: Terminal | null;
  fitAddon: FitAddon | null;
  sessionActive: boolean;
  sessionInfo: SessionInfo | null;
  disconnect: () => void;
  sendResize: () => void;
  sendInput: (data: string) => void;
  focus: () => void;
}

export function useTerminalSession(options: UseTerminalSessionOptions): UseTerminalSessionResult {
  const { sessionId, sessionName, onSessionInfo, onStatusMessage } = options;

  const termRef = useRef<HTMLDivElement | null>(null);
  const termInstanceRef = useRef<Terminal | null>(null);
  const fitAddonRef = useRef<FitAddon | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const recvBufferRef = useRef<ArrayBuffer>(new ArrayBuffer(0));

  const initialSessionIdRef = useRef(sessionId);
  const initialSessionNameRef = useRef(sessionName);
  const onSessionInfoRef = useRef(onSessionInfo);
  const onStatusMessageRef = useRef(onStatusMessage);
  const hasConnectedRef = useRef(false);
  onSessionInfoRef.current = onSessionInfo;
  onStatusMessageRef.current = onStatusMessage;

  const [sessionActive, setSessionActive] = useState(false);
  const [sessionInfo, setSessionInfo] = useState<SessionInfo | null>(null);

  const disconnect = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
  }, []);

  const sendResize = useCallback(() => {
    const fitAddon = fitAddonRef.current;
    if (fitAddon) fitAddon.fit();
  }, []);

  const sendInput = useCallback((data: string) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(encodeInput(data));
    }
  }, []);

  const focus = useCallback(() => {
    termInstanceRef.current?.focus();
  }, []);

  useEffect(() => {
    const container = termRef.current;
    if (!container) return;

    const term = new Terminal({
      cursorBlink: true,
      fontSize: 12,
      fontFamily: 'Menlo, Monaco, "Courier New", monospace',
      theme: {
        background: '#1a1a2e',
        foreground: '#eee',
      },
    });

    const fitAddon = new FitAddon();
    term.loadAddon(fitAddon);
    term.open(container);

    termInstanceRef.current = term;
    fitAddonRef.current = fitAddon;

    setTimeout(() => fitAddon.fit(), 0);

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws`;

    let ws: WebSocket;
    try {
      ws = new WebSocket(wsUrl);
    } catch (e) {
      term.write(`\x1b[31mFailed to connect: ${(e as Error).message}\x1b[0m\r\n`);
      return;
    }

    ws.binaryType = 'arraybuffer';
    wsRef.current = ws;

    ws.onopen = () => {
      const sid = initialSessionIdRef.current;
      if (sid !== undefined) {
        onStatusMessageRef.current?.('Reconnecting to session...', 'warning');
      } else {
        onStatusMessageRef.current?.('Connecting...', 'info');
      }
      ws.send(encodeIdentify(term.cols, term.rows));

      if (sid !== undefined) {
        ws.send(encodeAttach(sid));
      } else {
        const name = initialSessionNameRef.current ?? `session-${Date.now().toString(36)}`;
        ws.send(encodeNewSession(name));
      }
    };

    ws.onmessage = (event) => {
      const combined = new Uint8Array(recvBufferRef.current.byteLength + event.data.byteLength);
      combined.set(new Uint8Array(recvBufferRef.current), 0);
      combined.set(new Uint8Array(event.data), recvBufferRef.current.byteLength);

      const { messages, remaining } = decodeMessages(combined.buffer);
      recvBufferRef.current = remaining;

      for (const msg of messages) {
        if (msg.type === MessageType.OUTPUT) {
          const text = new TextDecoder().decode(msg.payload);
          term.write(text);
        } else if (msg.type === MessageType.SESSION_INFO) {
          const info = decodeSessionInfo(msg.payload);
          const isInitialConnect = !hasConnectedRef.current;

          setSessionInfo(info);
          setSessionActive(true);
          hasConnectedRef.current = true;
          onSessionInfoRef.current?.(info);
          if (isInitialConnect) {
            if (initialSessionIdRef.current !== undefined) {
              onStatusMessageRef.current?.('Reconnected!', 'success');
            } else {
              onStatusMessageRef.current?.('Session ready.', 'success');
            }
          }
        } else if (msg.type === MessageType.ERROR) {
          const view = new DataView(msg.payload.buffer, msg.payload.byteOffset);
          const errLen = view.getUint32(0, false);
          const errText = new TextDecoder().decode(msg.payload.slice(4, 4 + errLen));
          onStatusMessageRef.current?.(`Error: ${errText}`, 'error');
        } else if (msg.type === MessageType.SHELL_EXITED) {
          onStatusMessageRef.current?.('Shell exited', 'warning');
          setSessionActive(false);
        }
      }
    };

    ws.onclose = () => {
      onStatusMessageRef.current?.('Disconnected', 'error');
      setSessionActive(false);
    };

    ws.onerror = () => {
      onStatusMessageRef.current?.('Connection error', 'error');
    };

    const dataDisposable = term.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(encodeInput(data));
      }
    });

    const resizeDisposable = term.onResize(({ cols, rows }) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(encodeResize(cols, rows));
      }
    });

    return () => {
      dataDisposable.dispose();
      resizeDisposable.dispose();
      ws.close();
      term.dispose();
      termInstanceRef.current = null;
      fitAddonRef.current = null;
      wsRef.current = null;
    };
  }, []);

  return {
    termRef,
    term: termInstanceRef.current,
    fitAddon: fitAddonRef.current,
    sessionActive,
    sessionInfo,
    disconnect,
    sendResize,
    sendInput,
    focus,
  };
}
