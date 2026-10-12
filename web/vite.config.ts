import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";
import { defineConfig } from "vitest/config";

// The API lives on another subdomain (VITE_API_URL), so the service worker only ever sees this origin's files plus cover art.
// Audio, JSON and the WebSocket go straight to the API and are never intercepted (ranges and live streams must not be cached).
export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["icon.svg", "apple-touch-icon.png"],
      manifest: {
        name: "Resonance",
        short_name: "Resonance",
        description: "Your music, on tap.",
        start_url: "/",
        scope: "/",
        display: "standalone",
        background_color: "#000000",
        theme_color: "#000000",
        icons: [
          { src: "icon-192.png", sizes: "192x192", type: "image/png" },
          { src: "icon-512.png", sizes: "512x512", type: "image/png" },
          { src: "icon-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
        ],
        shortcuts: [
          { name: "Search", url: "/search", icons: [{ src: "icon-192.png", sizes: "192x192" }] },
          { name: "Your library", url: "/library", icons: [{ src: "icon-192.png", sizes: "192x192" }] },
        ],
      },
      workbox: {
        navigateFallback: "/index.html",
        runtimeCaching: [
          {
            urlPattern: ({ url }) => url.pathname.startsWith("/api/art/"),
            handler: "CacheFirst",
            options: { cacheName: "art", expiration: { maxEntries: 600, maxAgeSeconds: 60 * 60 * 24 * 30 }, cacheableResponse: { statuses: [200] } },
          },
        ],
      },
    }),
  ],
  test: { environment: "node" },
});
