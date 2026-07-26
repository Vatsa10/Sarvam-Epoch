"use client";

import { useState } from "react";
import type { LogEntry, Sheet } from "@/lib/types";

const API = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

// The lawyer is a third party to the call and may read a third language again.
const LAWYER_LANGS = [
  ["en-IN", "English"], ["hi-IN", "हिन्दी"], ["ml-IN", "മലയാളം"],
  ["ta-IN", "தமிழ்"], ["te-IN", "తెలుగు"], ["kn-IN", "ಕನ್ನಡ"],
  ["mr-IN", "मराठी"], ["gu-IN", "ગુજરાતી"], ["bn-IN", "বাংলা"],
];

const STATE_STYLE: Record<string, string> = {
  OPEN: "bg-neutral-800 text-neutral-400",
  PROPOSED: "bg-blue-950 text-blue-300",
  AGREED: "bg-emerald-950 text-emerald-400",
  DIVERGED: "bg-red-950 text-red-400",
  HEDGED: "bg-amber-950 text-amber-400",
  REJECTED: "bg-neutral-800 text-neutral-500",
};

export default function TermSheetPanel({
  sheet,
  log,
  code,
  className = "",
}: {
  sheet: Sheet | null;
  log: LogEntry[];
  code?: string;
  className?: string;
}) {
  const blocked = sheet?.blocked ?? [];
  const [lawyerLang, setLawyerLang] = useState("en-IN");
  // Falls back to the ?code= the share link carries, so the button works even
  // where the room code isn't passed down as a prop.
  const roomCode =
    code ??
    (typeof window === "undefined"
      ? null
      : new URLSearchParams(window.location.search).get("code"));

  return (
    <div className={`rounded-xl bg-neutral-900 border border-neutral-800 flex flex-col min-h-0 ${className}`}>
      <div className="flex items-center gap-2 px-4 py-3 border-b border-neutral-800 shrink-0">
        <span className="h-2 w-2 rounded-full bg-emerald-500" />
        <h2 className="text-sm font-semibold text-neutral-200">Live term sheet</h2>
        <span className="text-xs text-neutral-500 ml-auto">Shared with both parties</span>
        {roomCode && (
          <>
            <select
              value={lawyerLang}
              onChange={(e) => setLawyerLang(e.target.value)}
              title="Lawyer's language"
              className="text-xs bg-neutral-800 border border-neutral-700 rounded px-1.5 py-1 text-neutral-300"
            >
              {LAWYER_LANGS.map(([c, label]) => (
                <option key={c} value={c}>{label}</option>
              ))}
            </select>
            <button
              onClick={() =>
                window.open(
                  `${API}/api/meet/rooms/${encodeURIComponent(roomCode)}/draft?lang=${lawyerLang}`,
                  "_blank"
                )
              }
              className="text-xs font-medium rounded px-2 py-1 bg-emerald-700 hover:bg-emerald-600 text-white"
            >
              Draft agreement
            </button>
          </>
        )}
      </div>

      {blocked.length > 0 && (
        <div className="mx-4 mt-3 rounded-lg bg-red-950 border border-red-900 text-red-300 text-xs px-3 py-2">
          Cannot draft: {blocked.join(", ")} — both sides said yes to different things, or hedged.
        </div>
      )}

      <div className="flex-1 min-h-0 overflow-y-auto p-4 grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <h3 className="text-xs uppercase tracking-wide text-neutral-500 mb-2">Terms</h3>
          {!sheet && <div className="text-sm text-neutral-500">Waiting to connect…</div>}
          {sheet?.terms.map((t) => (
            <div key={t.key} className="py-2 border-b border-neutral-800 last:border-0">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <div className="text-sm font-medium capitalize">{t.key}</div>
                  <div className="text-xs text-neutral-400">{t.agreed_value || t.description}</div>
                </div>
                <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full shrink-0 ${STATE_STYLE[t.state]}`}>
                  {t.state}
                </span>
              </div>
              {t.divergence_note && (
                <div className="mt-1 text-xs text-red-300 bg-red-950/60 border-l-2 border-red-600 pl-2 py-1">
                  {t.divergence_note}
                </div>
              )}
              {t.proposals.length > 0 && (
                <div className="mt-1 space-y-0.5">
                  {t.proposals.map((p, i) => (
                    <div key={i} className="text-xs text-neutral-500">
                      &ldquo;{p.verbatim}&rdquo; → <span className="text-neutral-300">{p.value}</span>{" "}
                      <i>[{p.stance}]</i>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>

        <div className="min-h-0">
          <h3 className="text-xs uppercase tracking-wide text-neutral-500 mb-2">Conversation</h3>
          <div className="space-y-2">
            {log.length === 0 && <div className="text-sm text-neutral-500">No conversation yet.</div>}
            {[...log].reverse().map((entry) =>
              entry.kind === "note" ? (
                <div key={entry.id} className="border-l-2 border-blue-600 bg-blue-950/30 rounded-r-lg px-3 py-1.5">
                  <div className="text-[11px] uppercase tracking-wide text-neutral-500">
                    {entry.from} {entry.final ? "" : "…"}
                  </div>
                  <div className="text-sm text-neutral-300">{entry.text}</div>
                </div>
              ) : (
                <div
                  key={entry.id}
                  className={`border-l-2 rounded-r-lg px-3 py-1.5 ${
                    entry.flagged.length ? "border-red-600 bg-red-950/30" : "border-neutral-700 bg-neutral-800/50"
                  }`}
                >
                  <div className="text-[11px] uppercase tracking-wide text-neutral-500">{entry.speakerName}</div>
                  <div className="text-sm text-neutral-200">{entry.transcript}</div>
                  <div className="text-xs text-neutral-400 italic">→ {entry.relayText}</div>
                  {entry.flagged.length > 0 && (
                    <div className="text-xs text-red-400 mt-0.5">Flagged: {entry.flagged.join(", ")}</div>
                  )}
                </div>
              )
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
