"use client";

import { useState } from "react";
import { Copy, Mic, MicOff, PhoneOff } from "lucide-react";

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
}) {
  const [copied, setCopied] = useState(false);

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
    <div className="flex items-center justify-between px-6 py-4 border-t border-neutral-800 shrink-0">
      <button
        onClick={copyLink}
        className="flex items-center gap-2 text-sm text-neutral-400 hover:text-neutral-200"
        title="Copy invite link"
      >
        <Copy size={14} />
        {copied ? "Copied!" : `Code: ${code}`}
      </button>

      <div className="flex items-center gap-3 mx-auto">
        <button
          onClick={floorOpen ? onTalkDone : onTalkStart}
          disabled={talkDisabled}
          className={`flex items-center gap-2 rounded-full px-5 py-2.5 text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
            floorOpen ? "bg-emerald-600 hover:bg-emerald-500" : "bg-blue-600 hover:bg-blue-500"
          }`}
        >
          {floorOpen ? <MicOff size={16} /> : <Mic size={16} />}
          {talkLabel}
        </button>
        <button
          onClick={onToggleMute}
          disabled={!floorOpen}
          title={floorOpen ? undefined : "Mute only applies while you're talking"}
          className={`flex items-center gap-2 rounded-full px-4 py-2.5 text-sm font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
            muted ? "bg-neutral-700 hover:bg-neutral-600" : "bg-neutral-800 hover:bg-neutral-700"
          }`}
        >
          {muted ? <MicOff size={16} /> : <Mic size={16} />}
          {muted ? "Unmute" : "Mute"}
        </button>
        <button
          onClick={onLeave}
          className="flex items-center gap-2 rounded-full bg-red-600 hover:bg-red-500 px-6 py-2.5 text-sm font-medium transition-colors"
        >
          <PhoneOff size={16} />
          Leave call
        </button>
      </div>

      <div className="w-24" />
    </div>
  );
}
