"use client";

import { useEffect, useState } from "react";
import { Copy, Mic, MicOff, PhoneOff } from "lucide-react";
import { fetchLanguages } from "@/lib/api";
import type { Language } from "@/lib/types";

export default function ControlBar({
  code,
  muted,
  onToggleMute,
  onLeave,
  isMyTurn,
  floorOpen,
  processing,
  otherPresent,
  otherName,
  onTalkStart,
  onTalkDone,
  outLang,
  onOutLangChange,
}: {
  code: string;
  muted: boolean;
  onToggleMute: () => void;
  onLeave: () => void;
  isMyTurn: boolean;
  floorOpen: boolean;
  processing: boolean;
  otherPresent: boolean;
  otherName: string | null;
  onTalkStart: () => void;
  onTalkDone: () => void;
  outLang: string;
  onOutLangChange: (lang: string) => void;
}) {
  const [copied, setCopied] = useState(false);
  const [languages, setLanguages] = useState<Language[]>([]);

  useEffect(() => {
    fetchLanguages().then(setLanguages).catch(() => setLanguages([]));
  }, []);

  const talkDisabled = !otherPresent || !isMyTurn || processing;
  const talkLabel = !otherPresent
    ? "Waiting for other participant…"
    : processing
    ? "Translating…"
    : !isMyTurn
    ? `${otherName ?? "Other participant"} is speaking…`
    : floorOpen
    ? "Done"
    : "Talk";

  const copyLink = async () => {
    const url = `${window.location.origin}${window.location.pathname}?code=${code}`;
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard can be blocked; the code is still visible below */
    }
  };

  return (
    // Two layouts, one component. On a phone Talk gets its own full-width row and
    // everything else is secondary; on a laptop it collapses back to one bar.
    // Talk is the only control that matters mid-call, and a 38px target in a row
    // of five is not something you hit reliably while also speaking.
    <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 px-4 sm:px-6 py-3 sm:py-4 border-t border-neutral-800 shrink-0">
      <button
        onClick={copyLink}
        className="order-2 sm:order-1 flex items-center justify-center sm:justify-start gap-2 text-sm text-neutral-400 hover:text-neutral-200 min-h-[44px] sm:min-h-0"
        title="Copy invite link"
      >
        <Copy size={14} />
        {copied ? "Copied!" : `Code: ${code}`}
      </button>

      <div className="order-1 sm:order-2 flex items-center gap-2 sm:gap-3 sm:mx-auto">
        <button
          onClick={floorOpen ? onTalkDone : onTalkStart}
          disabled={talkDisabled}
          className={`flex flex-1 sm:flex-none items-center justify-center gap-2 rounded-full px-5 py-3.5 sm:py-2.5 text-base sm:text-sm font-semibold sm:font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
            floorOpen ? "bg-emerald-600 hover:bg-emerald-500" : "bg-blue-600 hover:bg-blue-500"
          }`}
        >
          {floorOpen ? <MicOff size={18} /> : <Mic size={18} />}
          {talkLabel}
        </button>
        <button
          onClick={onToggleMute}
          disabled={!floorOpen}
          title={floorOpen ? undefined : "Mute only applies while you're talking"}
          className={`flex items-center justify-center gap-2 rounded-full px-4 py-3.5 sm:py-2.5 text-sm font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
            muted ? "bg-neutral-700 hover:bg-neutral-600" : "bg-neutral-800 hover:bg-neutral-700"
          }`}
        >
          {muted ? <MicOff size={16} /> : <Mic size={16} />}
          {/* The word is noise next to Talk on a narrow screen; the icon carries it. */}
          <span className="hidden sm:inline">{muted ? "Unmute" : "Mute"}</span>
        </button>
        <button
          onClick={onLeave}
          className="flex items-center justify-center gap-2 rounded-full bg-red-600 hover:bg-red-500 px-4 sm:px-6 py-3.5 sm:py-2.5 text-sm font-medium transition-colors"
        >
          <PhoneOff size={16} />
          <span className="hidden sm:inline">Leave call</span>
        </button>
      </div>

      <select
        value={outLang}
        onChange={(e) => onOutLangChange(e.target.value)}
        title="Language you read and hear"
        className="order-3 w-full sm:w-auto rounded-lg bg-neutral-800 border border-neutral-700 px-3 sm:px-2 py-2.5 sm:py-1.5 text-sm sm:text-xs text-neutral-300 focus:outline-none focus:ring-2 focus:ring-blue-500"
      >
        {languages.map((l) => (
          <option key={l.code} value={l.code}>Hear: {l.label}</option>
        ))}
      </select>
    </div>
  );
}
