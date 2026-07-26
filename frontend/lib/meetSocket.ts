import type { ServerEvent } from "./types";

const BACKEND = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

export type MeetSocket = {
  sendAudio: (chunk: Int16Array) => void;
  sendControl: (type: "talk_start" | "talk_done") => void;
  setOutLang: (lang: string) => void;
  close: () => void;
};

/** Opens the room WebSocket at /api/meet/ws/{code}?name=..&lang=..&out_lang=.. and wires
 * incoming JSON frames to `onEvent`. Binary audio chunks queue behind
 * `readyState === OPEN` rather than throwing - a chunk arriving mid-connect
 * is simply dropped, never crashes the capture loop. */
export function connectMeet(
  code: string,
  name: string,
  lang: string,
  outLang: string,
  role: string,
  brief: string,
  onEvent: (e: ServerEvent) => void,
  onClose: () => void
): MeetSocket {
  const wsBase = BACKEND.replace(/^http/, "ws");
  const url = `${wsBase}/api/meet/ws/${encodeURIComponent(code)}?name=${encodeURIComponent(
    name
  )}&lang=${encodeURIComponent(lang)}&out_lang=${encodeURIComponent(
    outLang || lang
  )}&role=${encodeURIComponent(role)}&brief=${encodeURIComponent(brief)}`;

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
    sendControl(type) {
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type }));
    },
    setOutLang(lang: string) {
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "set_out_lang", lang }));
    },
    close() {
      ws.close();
    },
  };
}
