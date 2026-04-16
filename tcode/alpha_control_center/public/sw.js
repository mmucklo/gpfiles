/**
 * TSLA Alpha Command — Service Worker (Phase 16 PWA)
 *
 * Strategy:
 * - Cache static assets on install (app shell)
 * - Network-first for API calls (always fresh data)
 * - Push notifications for: new trade proposal, regime shift, circuit breaker, daily target
 */

const CACHE_NAME = 'alpha-cmd-v1';
const STATIC_ASSETS = [
  '/',
  '/index.html',
  '/manifest.json',
];

// ── Install ──────────────────────────────────────────────────────────────────

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(STATIC_ASSETS))
  );
  self.skipWaiting();
});

// ── Activate ─────────────────────────────────────────────────────────────────

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// ── Fetch ─────────────────────────────────────────────────────────────────────

self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // Always network-first for API calls
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/dev/')) {
    event.respondWith(fetch(event.request));
    return;
  }

  // Network-first with cache fallback for app assets
  event.respondWith(
    fetch(event.request)
      .then(response => {
        if (response.ok) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
        }
        return response;
      })
      .catch(() => caches.match(event.request))
  );
});

// ── Push Notifications ────────────────────────────────────────────────────────

self.addEventListener('push', event => {
  if (!event.data) return;

  let payload;
  try {
    payload = event.data.json();
  } catch {
    payload = { type: 'generic', message: event.data.text() };
  }

  const options = buildNotificationOptions(payload);
  event.waitUntil(self.registration.showNotification(options.title, options));
});

function buildNotificationOptions(payload) {
  const base = {
    icon: '/icon-192.png',
    badge: '/icon-192.png',
    vibrate: [200, 100, 200],
    requireInteraction: false,
    tag: payload.type,
  };

  switch (payload.type) {
    case 'trade_proposal':
      return {
        ...base,
        title: `⚡ Trade Proposal — ${payload.strategy}`,
        body: `${payload.direction} · ${payload.contract} · Conf: ${payload.confidence}%`,
        requireInteraction: true,
        actions: [
          { action: 'execute', title: '✓ Execute' },
          { action: 'skip', title: '✗ Skip' },
        ],
        data: { proposalId: payload.id, url: '/#trade-queue' },
      };

    case 'regime_shift':
      return {
        ...base,
        title: `⚡ Regime Shifted: ${payload.from} → ${payload.to}`,
        body: `Recommended: ${payload.recommended_strategy}`,
        vibrate: [200, 100, 200, 100, 400],
        data: { url: '/#morning-briefing' },
      };

    case 'circuit_breaker':
      return {
        ...base,
        title: '🚨 Circuit Breaker — Trading Halted',
        body: `Daily loss limit reached. No more trades today.`,
        requireInteraction: true,
        vibrate: [500, 200, 500, 200, 500],
        data: { url: '/#pnl' },
      };

    case 'daily_target':
      return {
        ...base,
        title: '🎯 Daily Target Hit!',
        body: `$10,000 target reached. Consider stopping to lock in gains.`,
        vibrate: [100, 50, 100, 50, 500],
        data: { url: '/#pnl' },
      };

    case 'trade_filled':
      return {
        ...base,
        title: `✓ Trade Filled — ${payload.strategy}`,
        body: `${payload.contract} filled at $${payload.fill_price}`,
        data: { url: '/#trade-queue' },
      };

    default:
      return {
        ...base,
        title: 'TSLA Alpha Command',
        body: payload.message || 'New notification',
        data: { url: '/' },
      };
  }
}

// ── Notification click ────────────────────────────────────────────────────────

self.addEventListener('notificationclick', event => {
  event.notification.close();

  const url = event.notification.data?.url ?? '/';

  if (event.action === 'execute' && event.notification.data?.proposalId) {
    // Fire-and-forget execute via fetch
    const proposalId = event.notification.data.proposalId;
    fetch(`/api/trades/proposed/${proposalId}/execute`, { method: 'POST' });
  }

  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
      const existing = list.find(c => c.url.includes(self.location.origin));
      if (existing) {
        existing.focus();
        existing.navigate(url);
      } else {
        clients.openWindow(url);
      }
    })
  );
});
