import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Personal Stock Research Command Center",
  description:
    "A private stock prediction run dashboard for freshness, model trust, ranked ideas, and next actions.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
