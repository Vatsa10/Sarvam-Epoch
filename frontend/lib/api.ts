import axios from "axios";
import type { Language } from "./types";

const API = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

export type RoomInfo = { code: string; participant_count: number; is_full: boolean };

export async function fetchLanguages(): Promise<Language[]> {
  const res = await axios.get<Language[]>(`${API}/api/meet/languages`);
  return res.data;
}

export async function createRoom(): Promise<RoomInfo> {
  const res = await axios.post<RoomInfo>(`${API}/api/meet/rooms`);
  return res.data;
}
