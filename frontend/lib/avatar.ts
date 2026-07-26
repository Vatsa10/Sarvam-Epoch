// Deterministic initials + color for a participant box, derived only from their
// typed name - no upload, no accounts.
const PALETTE = [
  "#2563eb", "#7c3aed", "#db2777", "#dc2626", "#d97706",
  "#65a30d", "#0d9488", "#0891b2", "#4338ca", "#be123c",
];

export function initialsOf(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

export function colorOf(name: string): string {
  let hash = 0;
  for (let i = 0; i < name.length; i++) {
    hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  }
  return PALETTE[hash % PALETTE.length];
}
