import type { SessionInfo, DecodedMessage, DecodeResult } from './types';

export const MessageType = {
  IDENTIFY: 0,
  NEW_SESSION: 1,
  ATTACH: 2,
  DETACH: 3,
  LIST_SESSIONS: 4,
  RESIZE: 5,
  INPUT: 6,
  OUTPUT: 7,
  ERROR: 8,
  SESSION_INFO: 9,
  SHELL_EXITED: 10,
} as const;

export function encodeMessage(type: number, payload: Uint8Array | string): Uint8Array {
  const payloadBytes = payload instanceof Uint8Array ? payload : new TextEncoder().encode(payload);
  const buffer = new ArrayBuffer(8 + payloadBytes.length);
  const view = new DataView(buffer);
  view.setUint32(0, type, false);
  view.setUint32(4, payloadBytes.length, false);
  new Uint8Array(buffer, 8).set(payloadBytes);
  return new Uint8Array(buffer);
}

export function encodeIdentify(width: number, height: number): Uint8Array {
  const payload = new ArrayBuffer(4);
  const view = new DataView(payload);
  view.setUint16(0, width, false);
  view.setUint16(2, height, false);
  return encodeMessage(MessageType.IDENTIFY, new Uint8Array(payload));
}

export function encodeNewSession(name: string): Uint8Array {
  const nameBytes = new TextEncoder().encode(name);
  const payload = new ArrayBuffer(4 + nameBytes.length);
  const view = new DataView(payload);
  view.setUint32(0, nameBytes.length, false);
  new Uint8Array(payload, 4).set(nameBytes);
  return encodeMessage(MessageType.NEW_SESSION, new Uint8Array(payload));
}

export function encodeInput(data: string): Uint8Array {
  return encodeMessage(MessageType.INPUT, data);
}

export function encodeResize(width: number, height: number): Uint8Array {
  const payload = new ArrayBuffer(4);
  const view = new DataView(payload);
  view.setUint16(0, width, false);
  view.setUint16(2, height, false);
  return encodeMessage(MessageType.RESIZE, new Uint8Array(payload));
}

export function encodeAttach(sessionId: number): Uint8Array {
  const payload = new ArrayBuffer(4);
  const view = new DataView(payload);
  view.setUint32(0, sessionId, false);
  return encodeMessage(MessageType.ATTACH, new Uint8Array(payload));
}

export function encodeListSessions(): Uint8Array {
  return encodeMessage(MessageType.LIST_SESSIONS, new Uint8Array(0));
}

export function decodeMessages(buffer: ArrayBuffer): DecodeResult {
  const messages: DecodedMessage[] = [];
  let offset = 0;
  while (offset + 8 <= buffer.byteLength) {
    const view = new DataView(buffer, offset);
    const type = view.getUint32(0, false);
    const length = view.getUint32(4, false);
    if (offset + 8 + length > buffer.byteLength) break;
    const payload = new Uint8Array(buffer, offset + 8, length);
    messages.push({ type, payload });
    offset += 8 + length;
  }
  return { messages, remaining: buffer.slice(offset) };
}

export function decodeSessionInfo(payload: Uint8Array): SessionInfo {
  const view = new DataView(payload.buffer, payload.byteOffset, payload.byteLength);
  let offset = 0;
  const sessionId = view.getUint32(offset, false); offset += 4;
  const nameLen = view.getUint32(offset, false); offset += 4;
  const name = new TextDecoder().decode(payload.slice(offset, offset + nameLen)); offset += nameLen;
  const paneId = view.getUint32(offset, false); offset += 4;
  const pid = view.getUint32(offset, false); offset += 4;
  const width = view.getUint16(offset, false); offset += 2;
  const height = view.getUint16(offset, false); offset += 2;
  const createdAt = view.getFloat64(offset, false); offset += 8;
  const attachedCount = view.getUint32(offset, false);
  return { sessionId, name, paneId, pid, width, height, createdAt, attachedCount };
}
