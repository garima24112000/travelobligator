import type { Metadata } from "next";
import { connection } from "next/server";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import "leaflet/dist/leaflet.css";
import { sanitizeApiBaseUrl } from "../lib/runtime-config";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "TravelObligator",
  description:
    "A polished MVP dashboard shell for route-aware travel planning.",
};

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  // Section 200E: render per request (never prerendered) so the container's RUNTIME `API_BASE_URL` reaches the
  // browser without a rebuild. Only a validated absolute http(s) URL is ever serialised; `<` is escaped so the
  // inline script can never be broken out of.
  await connection();
  const apiBaseUrl = sanitizeApiBaseUrl(process.env.API_BASE_URL);
  const runtimeConfig = JSON.stringify({ apiBaseUrl }).replace(/</g, "\\u003c");

  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <head>
        <script
          dangerouslySetInnerHTML={{
            __html: `window.__TRAVELOBLIGATOR_RUNTIME__=${runtimeConfig};`,
          }}
        />
      </head>
      <body className="min-h-full flex flex-col bg-[#07111f]">{children}</body>
    </html>
  );
}
