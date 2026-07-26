import type { ServerEvent } from "./types";

const BACKEND = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

export type MeetSocket = {
  sendAudio: (chunk: Int16Array) => void;
  close: () => void;
};

/** Opens the room WebSocket at /api/meet/ws/{code}?name=..&lang=.. and wires
 * incoming JSON frames to `onEvent`. Binary audio chunks queue behind
 * `readyState === OPEN` rather than throwing - a chunk arriving mid-connect
 * is simply dropped, never crashes the capture loop. */
export function connectMeet(
  code: string,
  name: string,
  lang: string,
  onEvent: (e: ServerEvent) => void,
  onClose: () => void
): MeetSocket {
  const wsBase = BACKEND.replace(/^http/, "ws");
  const url = `${wsBase}/api/meet/ws/${encodeURIComponent(code)}?name=${encodeURIComponent(
    name
  )}&lang=${encodeURIComponent(lang)}`;

  const ws = new WebSocket(url);
  ws.binaryType = "arraybuffer";

  ws.onmessage = (ev) => {
    try {
      onEvent(JSON.parse(ev.data) as ServerEvent);
    } catch {
      // malformed frame - drop it rather than crash the call
    }
  };
  ws.onclose = () => onClose();
  ws.onerror = () => {
    /* onclose fires right after; nothing extra to do here */
  };

  return {
    sendAudio(chunk: Int16Array) {
      if (ws.readyState === WebSocket.OPEN) ws.send(chunk.buffer as ArrayBuffer);
    },
    close() {
      ws.close();
    },
  };
}
