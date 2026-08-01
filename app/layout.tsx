import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL("https://stockprediction2025-research-desk.rnanda442.chatgpt.site"),
  title: "Stockprediction2025 Research Command Center",
  description:
    "An OSL-powered dashboard for model trust, leakage checks, calibration evidence, and next actions.",
  openGraph: {
    title: "Stockprediction2025 Research Command Center",
    description:
      "Model gates, calibration, leakage checks, and a reviewable action plan from Open Science Lab.",
    images: [{ url: "/og.png", width: 1672, height: 940 }],
  },
  twitter: {
    card: "summary_large_image",
    title: "Stockprediction2025 Research Command Center",
    description: "Evidence-first model monitoring from Open Science Lab.",
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
