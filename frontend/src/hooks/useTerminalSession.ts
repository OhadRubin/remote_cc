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
}

export function useTerminalSession(options: UseTerminalSessionOptions): UseTerminalSessionResult {
  const { sessionId, onSessionInfo, onStatusMessage } = options;

  const termRef = useRef<HTMLDivElement | null>(null);
  const termInstanceRef = useRef<Terminal | null>(null);
  const fitAddonRef = useRef<FitAddon | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const recvBufferRef = useRef<ArrayBuffer>(new ArrayBuffer(0));

  const [sessionActive, setSessionActive] = useState(false);
  const [sessionInfo, setSessionInfo] = useState<SessionInfo | null>(null);

  const disconnect = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
  }, []);

  const sendResize = useCallback(() => {
    const term = termInstanceRef.current;
    const ws = wsRef.current;
    const fitAddon = fitAddonRef.current;

    if (fitAddon && term) {
      fitAddon.fit();
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(encodeResize(term.cols, term.rows));
      }
    }
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
      if (sessionId !== undefined) {
        onStatusMessage?.('Reconnecting to session...', 'warning');
      } else {
        onStatusMessage?.('Connecting...', 'info');
      }
      ws.send(encodeIdentify(term.cols, term.rows));

      if (sessionId !== undefined) {
        ws.send(encodeAttach(sessionId));
      } else {
        const sessionName = `session-${Date.now().toString(36)}`;
        ws.send(encodeNewSession(sessionName));
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
          setSessionInfo(info);
          setSessionActive(true);
          onSessionInfo?.(info);
          if (sessionId !== undefined) {
            onStatusMessage?.('Reconnected!', 'success');
          } else {
            onStatusMessage?.('Session ready.', 'success');
          }
        } else if (msg.type === MessageType.ERROR) {
          const view = new DataView(msg.payload.buffer, msg.payload.byteOffset);
          const errLen = view.getUint32(0, false);
          const errText = new TextDecoder().decode(msg.payload.slice(4, 4 + errLen));
          onStatusMessage?.(`Error: ${errText}`, 'error');
        } else if (msg.type === MessageType.SHELL_EXITED) {
          onStatusMessage?.('Shell exited', 'warning');
          setSessionActive(false);
        }
      }
    };

    ws.onclose = () => {
      onStatusMessage?.('Disconnected', 'error');
      setSessionActive(false);
    };

    ws.onerror = () => {
      onStatusMessage?.('Connection error', 'error');
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
  }, [sessionId, onSessionInfo, onStatusMessage]);

  return {
    termRef,
    term: termInstanceRef.current,
    fitAddon: fitAddonRef.current,
    sessionActive,
    sessionInfo,
    disconnect,
    sendResize,
  };
}
