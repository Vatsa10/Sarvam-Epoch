import axios from "axios";
import type { Language } from "./types";

// Same-origin by default. FastAPI serves this static export itself, so a relative
// URL is correct on localhost, on the LAN, and through an ngrok tunnel with no
// rebuild. Baking an absolute "http://localhost:8000" in at build time meant a
// phone loading the page over ngrok then called localhost:8000 ON THE PHONE - the
// API failed, and fetchLanguages() fell back to English-only, which is how this
// was first noticed. Only `npm run dev` on :3000 needs the override.
const API = process.env.NEXT_PUBLIC_BACKEND_URL ?? "";

export type RoomInfo = { code: string; participant_count: number; is_full: boolean };

export async function fetchLanguages(): Promise<Language[]> {
  const res = await axios.get<Language[]>(`${API}/api/meet/languages`);
  return res.data;
}

export async function createRoom(): Promise<RoomInfo> {
  const res = await axios.post<RoomInfo>(`${API}/api/meet/rooms`);
  return res.data;
}
