/**
 * US equity market hours utilities.
 *
 * Market hours: 9:30 AM – 4:00 PM ET, Monday–Friday, excluding US federal holidays.
 * Used by IntegrityStatus to determine whether a stale/empty chain is an error
 * (during hours) or an expected idle state (off-hours → amber instead of red).
 */

// ── US federal holidays for 2026 (NYSE schedule) ─────────────────────────────
// Format: YYYY-MM-DD in Eastern Time
const US_MARKET_HOLIDAYS_2026 = new Set([
  '2026-01-01', // New Year's Day
  '2026-01-19', // MLK Jr. Day
  '2026-02-16', // Presidents' Day
  '2026-04-03', // Good Friday
  '2026-05-25', // Memorial Day
  '2026-07-03', // Independence Day (observed, Friday)
  '2026-09-07', // Labor Day
  '2026-11-26', // Thanksgiving Day
  '2026-12-25', // Christmas Day
]);

/**
 * Returns true if `date` falls within US equity market hours:
 *   9:30 AM – 4:00 PM ET, Monday–Friday, excluding NYSE holidays.
 */
export function isUSMarketHours(date: Date = new Date()): boolean {
  const fmt = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York',
    weekday: 'short',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: 'numeric',
    minute: 'numeric',
    hour12: false,
  });

  const parts = fmt.formatToParts(date);
  const get = (type: string): string => parts.find(p => p.type === type)?.value ?? '';

  const weekday = get('weekday'); // 'Mon', 'Tue', …, 'Sat', 'Sun'
  if (weekday === 'Sat' || weekday === 'Sun') return false;

  // Construct YYYY-MM-DD from ET parts
  const year  = get('year');
  const month = get('month');
  const day   = get('day');
  const dateStr = `${year}-${month}-${day}`;
  if (US_MARKET_HOLIDAYS_2026.has(dateStr)) return false;

  const h = parseInt(get('hour'), 10);
  const m = parseInt(get('minute'), 10);
  const minutesFromMidnight = h * 60 + m;

  // 9:30 AM = 570 min, 4:00 PM = 960 min
  return minutesFromMidnight >= 570 && minutesFromMidnight < 960;
}

/**
 * Returns a human-readable label for the next market open from the given date.
 * Used in the amber-state tooltip on the CHAIN integrity indicator.
 */
export function nextMarketOpenLabel(from: Date = new Date()): string {
  const fmt = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York',
    weekday: 'long',
    month: 'short',
    day: 'numeric',
  });

  // Walk forward up to 7 days to find next trading day
  for (let i = 1; i <= 7; i++) {
    const candidate = new Date(from.getTime() + i * 86_400_000);
    if (isUSMarketHours(new Date(candidate.getTime()))) {
      // candidate is a trading day — format it
      return `${fmt.format(candidate)} 9:30 AM ET`;
    }
    // Check if 9:30 AM ET on this candidate day is trading
    const etParts = new Intl.DateTimeFormat('en-US', {
      timeZone: 'America/New_York',
      weekday: 'short',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    }).formatToParts(candidate);
    const getP = (t: string) => etParts.find(p => p.type === t)?.value ?? '';
    const wd = getP('weekday');
    const ds = `${getP('year')}-${getP('month')}-${getP('day')}`;
    if (wd !== 'Sat' && wd !== 'Sun' && !US_MARKET_HOLIDAYS_2026.has(ds)) {
      return `${fmt.format(candidate)} 9:30 AM ET`;
    }
  }
  return 'next market open 9:30 AM ET';
}
