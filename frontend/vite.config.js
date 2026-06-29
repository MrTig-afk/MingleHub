import { existsSync, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { VitePWA } from 'vite-plugin-pwa'

// Dev TLS cert for the CURRENT LAN IP. scripts/dev_certs.ps1 (re)mints these
// fixed-name files for whatever IP the laptop has now, so a network move needs
// no edit here. Read them if present; otherwise dev still boots over HTTP.
function devHttps() {
  const root = fileURLToPath(new URL('../certs/', import.meta.url))
  const cert = root + 'dev.pem'
  const key = root + 'dev-key.pem'
  if (existsSync(cert) && existsSync(key)) {
    return { cert: readFileSync(cert), key: readFileSync(key) }
  }
  return undefined
}

// A stale service worker registered by an earlier build keeps serving the old
// cached app on this origin (Chrome/standalone), so fresh dev code never loads
// and a normal reload can't fix it. This dev-only plugin answers the SW update
// check at /sw.js (and /dev-sw.js) with a self-destroying worker: on activate it
// clears all caches, unregisters itself, and reloads open tabs -> the next load
// comes straight from Vite. apply:'serve' keeps it out of production builds,
// where vite-plugin-pwa still generates the real service worker.
function devKillStaleServiceWorker() {
  // NOTE: this worker clears caches and unregisters itself, but must NOT force-
  // reload the page. An earlier version called client.navigate(client.url) on
  // activate, which created an infinite dev reload loop (leftover SW -> update
  // check -> activate -> reload -> re-register -> reload...), wiping any form
  // input mid-type. main.jsx already unregisters SWs + clears caches on load,
  // so the forced reload was redundant; dropping it stops the loop.
  const body = `
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    try {
      const keys = await caches.keys();
      await Promise.all(keys.map((k) => caches.delete(k)));
    } catch (_) { /* ignore */ }
    try { await self.registration.unregister(); } catch (_) { /* ignore */ }
  })());
});
`
  return {
    name: 'dev-kill-stale-service-worker',
    apply: 'serve',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const url = (req.url || '').split('?')[0]
        if (url === '/sw.js' || url === '/dev-sw.js') {
          res.setHeader('Content-Type', 'application/javascript')
          res.setHeader('Cache-Control', 'no-store')
          res.end(body)
          return
        }
        next()
      })
    },
  }
}

export default defineConfig(({ command }) => ({
  server: {
    // Bind all interfaces so a phone on the LAN can reach the dev app at the
    // laptop's current IP without `-- --host`.
    host: true,
    https: devHttps(),
    // Never let the browser cache the dev app — every load is fresh code.
    headers: { 'Cache-Control': 'no-store' },
  },
  plugins: [
    devKillStaleServiceWorker(),
    react(),
    tailwindcss(),
    // PWA service worker is generated ONLY for production builds. In dev a
    // registered SW caused reload loops + stale-code headaches, so dev stays
    // SW-free (devKillStaleServiceWorker above cleans up any leftover worker).
    ...(command === 'build' ? [VitePWA({
      // Self-destroying worker: the precaching PWA kept serving stale bundles on
      // phones (an NFC tap loaded the OLD app until the SW updated on a LATER
      // load). This app is online-only (needs the backend) and is reached by tap,
      // so the SW gave no benefit. selfDestroying makes the shipped sw.js
      // unregister the old worker and delete its caches -> phones always get the
      // latest build straight from the network. (Re-add a network-first PWA later
      // if home-screen install is ever wanted.)
      selfDestroying: true,
      registerType: 'autoUpdate',
      includeAssets: ['favicon.svg', 'apple-touch-icon.png'],
      manifest: {
        name: 'MingleHub',
        short_name: 'MingleHub',
        description: 'The social game for bars and groups.',
        theme_color: '#0A0A0C',
        background_color: '#0A0A0C',
        display: 'standalone',
        orientation: 'portrait',
        start_url: '/',
        scope: '/',
        icons: [
          { src: '/icon-192.png', sizes: '192x192', type: 'image/png' },
          { src: '/icon-512.png', sizes: '512x512', type: 'image/png' },
          { src: '/icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'any maskable' },
        ],
      },
      workbox: {
        globPatterns: ['**/*.{js,css,html,json,png,svg}'],
        runtimeCaching: [
          {
            urlPattern: /\/api\/packs/,
            handler: 'NetworkFirst',
            options: { cacheName: 'packs-cache' },
          },
        ],
      },
    })] : []),
  ],
}))
