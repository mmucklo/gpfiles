import './SkeletonLoader.css';

/**
 * Skeleton loading placeholders — shown when data has been loading >300ms.
 * All pulse via CSS animation with no JS timers.
 */

export const SkeletonLine = ({ width = '100%', height = '14px' }: { width?: string; height?: string }) => (
  <div
    className="skeleton-line"
    style={{ width, height }}
    aria-hidden="true"
    role="presentation"
  />
);

export const SkeletonCard = ({ rows = 3 }: { rows?: number }) => (
  <div className="skeleton-card" aria-busy="true" aria-label="Loading…">
    <SkeletonLine width="60%" height="12px" />
    {Array.from({ length: rows }).map((_, i) => (
      <SkeletonLine key={i} width={i % 2 === 0 ? '85%' : '70%'} />
    ))}
  </div>
);

export const SkeletonPill = () => (
  <div className="skeleton-pill" aria-hidden="true" />
);

export const SkeletonTable = ({ rows = 4, cols = 5 }: { rows?: number; cols?: number }) => (
  <div className="skeleton-table" aria-busy="true" aria-label="Loading table…">
    {Array.from({ length: rows }).map((_, r) => (
      <div key={r} className="skeleton-table-row">
        {Array.from({ length: cols }).map((_, c) => (
          <SkeletonLine key={c} width={c === 0 ? '120px' : '60px'} />
        ))}
      </div>
    ))}
  </div>
);
