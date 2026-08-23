import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL("https://stockprediction2025-research-desk.rnanda442.chatgpt.site"),
  title: "OSL Stock Research Desk",
  description:
    "An evidence-first stock research desk that keeps large archives in Open Science Lab and publishes only compact, reviewable analysis.",
  openGraph: {
    title: "OSL Stock Research Desk",
    description:
      "Large evidence stays in Open Science Lab. Model gates, calibration checks, and next actions reach the research desk.",
    images: [{ url: "/og.png", width: 1672, height: 940 }],
  },
  twitter: {
    card: "summary_large_image",
    title: "OSL Stock Research Desk",
    description: "Evidence-first model monitoring with an OSL-backed warehouse.",
    images: ["/og.png"],
  },
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
