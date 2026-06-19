import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { VitePWA } from 'vite-plugin-pwa'

// A stale service worker registered by an earlier build keeps serving the old
// cached app on this origin (Chrome/standalone), so fresh dev code never loads
// and a normal reload can't fix it. This dev-only plugin answers the SW update
// check at /sw.js (and /dev-sw.js) with a self-destroying worker: on activate it
// clears all caches, unregisters itself, and reloads open tabs -> the next load
// comes straight from Vite. apply:'serve' keeps it out of production builds,
// where vite-plugin-pwa still generates the real service worker.
function devKillStaleServiceWorker() {
  const body = `
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    try {
      const keys = await caches.keys();
      await Promise.all(keys.map((k) => caches.delete(k)));
    } catch (_) { /* ignore */ }
    try { await self.registration.unregister(); } catch (_) { /* ignore */ }
    const clients = await self.clients.matchAll({ type: 'window' });
    for (const client of clients) {
      try { client.navigate(client.url); } catch (_) { /* ignore */ }
    }
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

export default defineConfig({
  server: {
    https: {
      cert: '../192.168.1.108.pem',
      key: '../192.168.1.108-key.pem',
    },
    // Never let the browser cache the dev app — every load is fresh code.
    headers: { 'Cache-Control': 'no-store' },
  },
  plugins: [
    devKillStaleServiceWorker(),
    react(),
    tailwindcss(),
    VitePWA({
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
    }),
  ],
})
