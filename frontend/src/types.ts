export interface SessionInfo {
  sessionId: number;
  name: string;
  paneId: number;
  pid: number;
  width: number;
  height: number;
  createdAt: number;
  attachedCount: number;
}

export interface DecodedMessage {
  type: number;
  payload: Uint8Array;
}

export interface DecodeResult {
  messages: DecodedMessage[];
  remaining: ArrayBuffer;
}
