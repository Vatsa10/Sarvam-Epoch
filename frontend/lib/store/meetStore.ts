import { create } from "zustand";
import type { LogEntry, PartyInfo, ServerEvent, Sheet } from "@/lib/types";

type ConnectionStatus = "idle" | "connecting" | "connected" | "closed" | "error";

type MeetState = {
  status: ConnectionStatus;
  errorText: string | null;
  me: PartyInfo | null;
  other: PartyInfo | null;
  sheet: Sheet | null;
  log: LogEntry[];
  muted: boolean;
  speaking: Record<string, boolean>;
  turnHolder: string | null;
  floorOpen: boolean;

  setStatus: (s: ConnectionStatus) => void;
  setError: (text: string) => void;
  setMuted: (muted: boolean) => void;
  applyServerEvent: (evt: ServerEvent) => void;
  reset: () => void;
};

const MAX_LOG = 200;
// Speaking-indicator pulses clear on their own shortly after the last frame
// from that party - not reactive state, just timer bookkeeping.
const speakingTimers: Record<string, ReturnType<typeof setTimeout>> = {};

function markSpeaking(set: (fn: (s: MeetState) => Partial<MeetState>) => void, partyId: string) {
  set((s) => ({ speaking: { ...s.speaking, [partyId]: true } }));
  clearTimeout(speakingTimers[partyId]);
  speakingTimers[partyId] = setTimeout(() => {
    set((s) => ({ speaking: { ...s.speaking, [partyId]: false } }));
  }, 1200);
}

function playAudio(b64: string) {
  if (!b64) return;
  const audio = new Audio(`data:audio/wav;base64,${b64}`);
  audio.play().catch(() => {
    /* autoplay can be blocked before the first user gesture - not fatal */
  });
}

export const useMeetStore = create<MeetState>((set, get) => ({
  status: "idle",
  errorText: null,
  me: null,
  other: null,
  sheet: null,
  log: [],
  muted: false,
  speaking: {},
  turnHolder: null,
  floorOpen: false,

  setStatus: (status) => set({ status }),
  setError: (errorText) => set({ errorText, status: "error" }),
  setMuted: (muted) => set({ muted }),

  applyServerEvent: (evt) => {
    switch (evt.type) {
      case "joined":
        set({
          me: evt.you, other: evt.other, sheet: evt.sheet, status: "connected",
          turnHolder: evt.turn_holder, floorOpen: evt.floor_open,
        });
        return;

      case "participant_joined":
        set({ other: { party_id: evt.party_id, name: evt.name, lang: evt.lang } });
        return;

      case "participant_left":
        set({ other: null });
        return;

      case "note": {
        const id = `note-${evt.from}-${evt.lang}`;
        set((s) => {
          const next = s.log.filter((e) => !(e.kind === "note" && e.id === id));
          next.push({ kind: "note", id, from: evt.from, lang: evt.lang, text: evt.text, final: evt.final });
          return { log: next.slice(-MAX_LOG) };
        });
        const me = get().me;
        if (me) markSpeaking(set, evt.from === me.name ? me.party_id : (get().other?.party_id ?? evt.from));
        return;
      }

      case "turn": {
        set((s) => {
          const withoutPartials = s.log.filter(
            (e) => !(e.kind === "note" && e.from === evt.speaker_name && !e.final)
          );
          withoutPartials.push({
            kind: "turn", id: `turn-${evt.speaker}-${evt.sheet.turns.length}`,
            speaker: evt.speaker, speakerName: evt.speaker_name,
            transcript: evt.transcript, relayText: evt.relay_text, flagged: evt.flagged,
          });
          return { log: withoutPartials.slice(-MAX_LOG), sheet: evt.sheet };
        });
        playAudio(evt.audio_b64);
        return;
      }

      case "floor":
        set({ turnHolder: evt.holder, floorOpen: evt.open });
        return;

      case "error":
        set({ errorText: evt.text });
        return;
    }
  },

  reset: () =>
    set({
      status: "idle", errorText: null, me: null, other: null,
      sheet: null, log: [], muted: false, speaking: {},
      turnHolder: null, floorOpen: false,
    }),
}));
