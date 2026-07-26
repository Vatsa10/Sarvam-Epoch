// Shapes mirrored from the backend: app/meet_interface/languages.py (Language),
// app/mediator.py's Negotiation.sheet() (Sheet/Term/Proposal/TurnRecord), and the
// WebSocket frames app/meet_interface/ws.py sends (ServerEvent).

export type Language = { code: string; label: string };

export type TermState =
  | "OPEN" | "PROPOSED" | "AGREED" | "DIVERGED" | "HEDGED" | "REJECTED";

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

export type PartyInfo = { party_id: string; name: string; lang: string };

export type LogEntry =
  | { kind: "note"; id: string; from: string; lang: string; text: string; final: boolean }
  | {
      kind: "turn"; id: string; speaker: string; speakerName: string;
      transcript: string; relayText: string; flagged: string[];
    };

export type ServerEvent =
  | {
      type: "joined"; you: PartyInfo; other: PartyInfo | null; sheet: Sheet;
      turn_holder: string; floor_open: boolean;
    }
  | { type: "participant_joined"; party_id: string; name: string; lang: string }
  | { type: "participant_left"; party_id: string; name: string }
  | { type: "note"; final: boolean; from: string; lang: string; text: string }
  | {
      type: "turn"; speaker: string; speaker_name: string; transcript: string;
      relay_text: string; flagged: string[]; sheet: Sheet; audio_b64: string;
    }
  | { type: "floor"; holder: string; open: boolean }
  | { type: "error"; text: string };
