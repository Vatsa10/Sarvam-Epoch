"use client";

import { colorOf, initialsOf } from "@/lib/avatar";
import type { PartyInfo } from "@/lib/types";

export default function ParticipantBox({
  party,
  placeholder,
  speaking,
  muted,
}: {
  party: PartyInfo | null;
  placeholder: string;
  speaking?: boolean;
  muted?: boolean;
}) {
  if (!party) {
    return (
      <div className="flex-1 h-28 lg:h-40 rounded-xl bg-neutral-900 border border-neutral-800 flex items-center justify-center text-neutral-500 text-xs">
        {placeholder}
      </div>
    );
  }

  return (
    <div
      className={`flex-1 h-28 lg:h-40 rounded-xl bg-neutral-900 border flex flex-col items-center justify-center gap-2 transition-colors ${
        speaking ? "border-emerald-500" : "border-neutral-800"
      }`}
    >
      <div
        className="h-14 w-14 rounded-full flex items-center justify-center font-semibold text-lg text-white shrink-0"
        style={{ backgroundColor: colorOf(party.name) }}
      >
        {initialsOf(party.name)}
      </div>
      <div className="text-center">
        <div className="text-sm font-medium">{party.name}{muted ? " (muted)" : ""}</div>
        <div className="text-xs text-neutral-500">{party.lang}</div>
      </div>
    </div>
  );
}
