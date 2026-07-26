// Shapes mirrored from the backend: app/meet_interface/languages.py (Language),
// app/mediator.py's Negotiation.sheet() (Sheet/Term/Proposal/TurnRecord), and the
// WebSocket frames app/meet_interface/ws.py sends (ServerEvent).

export type Language = { code: string; label: string };

export type TermState =
  | "OPEN" | "PROPOSED" | "AGREED" | "DIVERGED" | "HEDGED" | "REJECTED" | "PARKED";

export type Proposal = {
  party: string;
  value: string;
  verbatim: string;
  lang: string;
  stance: "propose" | "accept" | "reject" | "hedge";
  turn: number;
  ts: number;
};

export type Term = {
  key: string;
  description: string;
  state: TermState;
  agreed_value: string | null;
  divergence_note: string | null;
  proposals: Proposal[];
};

export type TurnRecord = {
  idx: number;
  party: string;
  lang: string;
  transcript: string;
  relay_text: string;
  interjection: string | null;
  ts: number;
};

export type Sheet = {
  session_id: string;
  drafting_safe: boolean;
  terms: Term[];
  turns: TurnRecord[];
  blocked: string[];
};

// `lang` is what they SPEAK (STT source); `out_lang` is what they SEE/HEAR
// (captions + TTS target). Absent out_lang means "same as lang".
export type PartyInfo = { party_id: string; name: string; lang: string;
  out_lang?: string; role?: string; brief?: string };

export type LogEntry =
  | { kind: "note"; id: string; from: string; lang: string; text: string; final: boolean }
  | {
      kind: "turn"; id: string; turnIdx: number; speaker: string; speakerName: string;
      transcript: string; relayText: string; flagged: string[];
      // true from the moment the text lands until its audio arrives, so the
      // listener can see "speaking shortly" instead of silence.
      speaking: boolean;
    };

export type ServerEvent =
  | {
      type: "joined"; you: PartyInfo; other: PartyInfo | null; sheet: Sheet;
      turn_holder: string; floor_open: boolean;
    }
  | { type: "participant_joined"; party_id: string; name: string; lang: string; out_lang?: string }
  | { type: "out_lang"; party_id: string; lang: string }
  | { type: "participant_left"; party_id: string; name: string }
  | { type: "note"; final: boolean; from: string; lang: string; text: string }
  | {
      type: "turn"; turn_idx: number; speaker: string; speaker_name: string;
      transcript: string; relay_text: string; flagged: string[]; sheet: Sheet;
      speaking: boolean;
    }
  // Audio follows its turn as a separate frame - text is not held hostage to TTS.
  | { type: "audio"; turn_idx: number; audio_b64: string }
  | { type: "floor"; holder: string; open: boolean }
  | { type: "recover"; text: string }
  | { type: "resolve"; term: string; text: string; parked?: boolean }
  | { type: "error"; text: string };
