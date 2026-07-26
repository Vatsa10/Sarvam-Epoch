import type { ServerEvent } from "./types";

// Same-origin by default. FastAPI serves this static export itself, so a relative
// URL is correct on localhost, on the LAN, and through an ngrok tunnel with no
// rebuild. Baking an absolute "http://localhost:8000" in at build time meant a
// phone loading the page over ngrok then called localhost:8000 ON THE PHONE - the
// API failed, and fetchLanguages() fell back to English-only, which is how this
// was first noticed. Only `npm run dev` on :3000 needs the override.
const BACKEND = process.env.NEXT_PUBLIC_BACKEND_URL ?? "";

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
  // With no override, derive the socket origin from the page itself, so an
  // https:// tunnel becomes wss:// automatically. A hardcoded ws://localhost also
  // fails the browser's mixed-content rule on an https page.
  const wsBase = BACKEND
    ? BACKEND.replace(/^http/, "ws")
    : `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}`;
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
