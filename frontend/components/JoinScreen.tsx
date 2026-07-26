"use client";

import { useEffect, useState } from "react";
import { createRoom, fetchLanguages } from "@/lib/api";
import type { Language } from "@/lib/types";

export default function JoinScreen({
  onReady,
}: {
  onReady: (code: string, name: string, lang: string) => void;
}) {
  const [mode, setMode] = useState<"create" | "join">("create");
  const [name, setName] = useState("");
  const [lang, setLang] = useState("en-IN");
  const [code, setCode] = useState("");
  const [languages, setLanguages] = useState<Language[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchLanguages().then(setLanguages).catch(() => setLanguages([{ code: "en-IN", label: "English" }]));
    const params = new URLSearchParams(window.location.search);
    const c = params.get("code");
    if (c) {
      setCode(c.toUpperCase());
      setMode("join");
    }
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const roomCode = mode === "create" ? (await createRoom()).code : code.trim().toUpperCase();
      if (!roomCode) throw new Error("Enter a meeting code");
      onReady(roomCode, name.trim(), lang);
    } catch (err) {
      setError((err as Error).message || "Could not start the call");
      setBusy(false);
    }
  };

  return (
    <main className="min-h-screen flex items-center justify-center bg-neutral-950 text-neutral-100 p-6">
      <form onSubmit={submit} className="w-full max-w-sm bg-neutral-900 border border-neutral-800 rounded-xl p-6 space-y-4">
        <div>
          <h1 className="text-xl font-semibold">NyayBandhan Meet</h1>
          <p className="text-sm text-neutral-400 mt-1">Live translated call for two people.</p>
        </div>

        <div className="flex rounded-lg bg-neutral-800 p-1 text-sm">
          <button type="button" onClick={() => setMode("create")}
            className={`flex-1 rounded-md py-1.5 transition-colors ${mode === "create" ? "bg-blue-600" : "text-neutral-400"}`}>
            Create meeting
          </button>
          <button type="button" onClick={() => setMode("join")}
            className={`flex-1 rounded-md py-1.5 transition-colors ${mode === "join" ? "bg-blue-600" : "text-neutral-400"}`}>
            Join with code
          </button>
        </div>

        {mode === "join" && (
          <label className="block text-sm">
            Meeting code
            <input
              className="mt-1 w-full rounded-lg bg-neutral-800 border border-neutral-700 px-3 py-2 text-sm uppercase tracking-widest focus:outline-none focus:ring-2 focus:ring-blue-500"
              value={code}
              onChange={(e) => setCode(e.target.value.toUpperCase())}
              maxLength={6}
              required
            />
          </label>
        )}

        <label className="block text-sm">
          Your name
          <input
            className="mt-1 w-full rounded-lg bg-neutral-800 border border-neutral-700 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
        </label>

        <label className="block text-sm">
          Your language
          <select
            className="mt-1 w-full rounded-lg bg-neutral-800 border border-neutral-700 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            value={lang}
            onChange={(e) => setLang(e.target.value)}
          >
            {languages.map((l) => (
              <option key={l.code} value={l.code}>{l.label}</option>
            ))}
          </select>
        </label>

        {error && <p className="text-sm text-red-400">{error}</p>}

        <button
          type="submit"
          disabled={busy}
          className="w-full rounded-lg bg-blue-600 hover:bg-blue-500 disabled:opacity-50 py-2 text-sm font-medium transition-colors"
        >
          {mode === "create" ? "Create & join" : "Join"}
        </button>
      </form>
    </main>
  );
}
