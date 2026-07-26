import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "NyayBandhan Meet",
  description: "Two-party video conference with a live term-tracking notes panel.",
};

// Without this, mobile browsers lay the page out at ~980px and scale it down:
// every control ends up too small to tap and the term sheet is unreadable. One
// party is expected to join from a phone, so this is not optional.
// `maximumScale` is deliberately left alone - pinch-zoom is an accessibility
// affordance and locking it out to look tidier is not a trade worth making.
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#0a0a0a",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="antialiased">{children}</body>
    </html>
  );
}
