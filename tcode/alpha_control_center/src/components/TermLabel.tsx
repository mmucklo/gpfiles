/**
 * TermLabel — hoverable / clickable glossary term wrapper.
 *
 * Usage:
 *   <TermLabel term="CORRELATION_REGIME" />
 *   <TermLabel term="IDIOSYNCRATIC" inline />          // span (default)
 *   <TermLabel term="DIRECTIONAL_STRONG" badge />      // pill style
 *   <TermLabel term="REGIME_KELLY_MULTIPLIER" value={1.2} />
 *
 * Behavior:
 *   - Renders display text with a dotted underline
 *   - Hover → tooltip (short description, viewport-safe via Tooltip component)
 *   - Click → drill-down popover (long, formula, source, trading_impact, related)
 *   - Related links open their own drill-down
 *   - data-glossary-term attribute for Playwright audit
 */
import { useState, useRef, useLayoutEffect, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { GLOSSARY, lookupTerm, type GlossaryEntry } from '../lib/term_glossary';
import './TermLabel.css';

// ── Tooltip (reuses same flip+shift logic as Tooltip.tsx) ────────────────────

const PADDING = 8;

function computeTooltipPosition(
  anchor: DOMRect,
  ttW: number,
  ttH: number,
  vw: number,
  vh: number,
): { top: number; left: number } {
  const gap = 8;
  const spaceAbove = anchor.top;
  const spaceBelow = vh - anchor.bottom;
  let top: number;
  if (spaceAbove >= ttH + gap + PADDING || spaceAbove >= spaceBelow) {
    top = anchor.top - ttH - gap;
  } else {
    top = anchor.bottom + gap;
  }
  let left = anchor.left + anchor.width / 2 - ttW / 2;
  if (left + ttW > vw - PADDING) left = vw - PADDING - ttW;
  if (left < PADDING) left = PADDING;
  if (top < PADDING) top = PADDING;
  if (top + ttH > vh - PADDING) top = vh - PADDING - ttH;
  return { top, left };
}

// ── DrillDown popover ────────────────────────────────────────────────────────

interface DrillDownProps {
  entry: GlossaryEntry;
  value?: number | string;
  onClose: () => void;
  onNavigate: (key: string) => void;
}

const DrillDown = ({ entry, value, onClose, onNavigate }: DrillDownProps) => {
  return createPortal(
    <div
      className="term-drill-overlay"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={`Glossary: ${entry.display}`}
    >
      <div className="term-drill-card" onClick={e => e.stopPropagation()}>
        <div className="term-drill-header">
          <span className="term-drill-title">{entry.display}</span>
          <button className="term-drill-close" onClick={onClose} aria-label="Close">✕</button>
        </div>

        {/* Short description */}
        <div className="term-drill-short" data-testid="drill-short">{entry.short}</div>

        {/* Live value if provided */}
        {value !== undefined && (
          <div style={{ marginBottom: 10, fontSize: 12, color: '#e6edf3' }}>
            Current value:{' '}
            <strong className="term-label-val-number">{value}</strong>
          </div>
        )}

        {/* Long description (plain text, preserve line-breaks) */}
        <div className="term-drill-long" data-testid="drill-long">
          {entry.long}
        </div>

        {/* Meta rows: formula, source */}
        {(entry.formula || entry.source) && (
          <div className="term-drill-meta">
            {entry.formula && (
              <div className="term-drill-meta-row">
                <span className="term-drill-meta-label" data-testid="drill-source">Formula</span>
                <span className="term-drill-meta-value" style={{ fontFamily: 'monospace', color: '#79c0ff' }}>
                  {entry.formula}
                </span>
              </div>
            )}
            {entry.source && (
              <div className="term-drill-meta-row">
                <span className="term-drill-meta-label" data-testid="drill-source">Source</span>
                <span className="term-drill-meta-value">{entry.source}</span>
              </div>
            )}
          </div>
        )}

        {/* Trading impact */}
        {entry.trading_impact && (
          <div className="term-drill-impact">
            <div className="term-drill-impact-label">Trading Impact</div>
            <div data-testid="drill-trading-impact">{entry.trading_impact}</div>
          </div>
        )}

        {/* Phase note */}
        {entry.phase_note && (
          <div className="term-drill-phase-note">{entry.phase_note}</div>
        )}

        {/* Related terms */}
        {entry.related && entry.related.length > 0 && (
          <div>
            <div style={{ fontSize: 11, color: '#6e7681', marginTop: 12, marginBottom: 6 }}>
              Related terms
            </div>
            <div className="term-drill-related">
              {entry.related
                .filter(k => GLOSSARY[k])
                .map(k => (
                  <button
                    key={k}
                    className="term-drill-related-chip"
                    onClick={() => onNavigate(k)}
                    aria-label={`Open glossary: ${GLOSSARY[k]?.display ?? k}`}
                  >
                    {GLOSSARY[k]?.display ?? k}
                  </button>
                ))}
            </div>
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
};

// ── TermLabel ────────────────────────────────────────────────────────────────

interface TermLabelProps {
  /** Canonical GLOSSARY key, e.g. "CORRELATION_REGIME" */
  term: string;
  /** Override rendered text (default: entry.display) */
  children?: React.ReactNode;
  /** Use span layout (default). Explicit flag for clarity. */
  inline?: boolean;
  /** Render as pill badge (for archetype / regime labels) */
  badge?: boolean;
  /** Show a live value next to the term in the rendered label */
  value?: number | string;
  /** Extra className on the wrapper */
  className?: string;
  /** Pass-through style */
  style?: React.CSSProperties;
}

const TermLabel: React.FC<TermLabelProps> = ({
  term,
  children,
  badge = false,
  value,
  className = '',
  style,
}) => {
  const entry = lookupTerm(term);
  const [tooltipVisible, setTooltipVisible] = useState(false);
  const [tooltipCoords, setTooltipCoords] = useState({ top: 0, left: 0 });
  const [drillOpen, setDrillOpen] = useState(false);
  const [drillEntry, setDrillEntry] = useState<GlossaryEntry | undefined>(entry);
  const containerRef = useRef<HTMLSpanElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const rafRef = useRef<number>(0);

  // Recompute tooltip position after it renders (same pattern as Tooltip.tsx)
  const reposition = useCallback(() => {
    if (!containerRef.current || !tooltipRef.current) return;
    const anchor = containerRef.current.getBoundingClientRect();
    const tt = tooltipRef.current.getBoundingClientRect();
    const { top, left } = computeTooltipPosition(anchor, tt.width, tt.height, window.innerWidth, window.innerHeight);
    setTooltipCoords({ top, left });
  }, []);

  useLayoutEffect(() => {
    if (!tooltipVisible) return;
    if (containerRef.current) {
      const a = containerRef.current.getBoundingClientRect();
      setTooltipCoords({ top: a.top, left: a.left + a.width / 2 });
    }
    rafRef.current = requestAnimationFrame(reposition);
    return () => { if (rafRef.current) cancelAnimationFrame(rafRef.current); };
  }, [tooltipVisible, reposition]);

  const handleMouseEnter = useCallback(() => setTooltipVisible(true), []);
  const handleMouseLeave = useCallback(() => {
    setTooltipVisible(false);
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
  }, []);

  const handleClick = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    setTooltipVisible(false);
    setDrillEntry(entry);
    setDrillOpen(true);
  }, [entry]);

  const handleNavigate = useCallback((key: string) => {
    const next = lookupTerm(key);
    if (next) {
      setDrillEntry(next);
    }
  }, []);

  // Render the label text
  const labelText = children ?? (entry?.display ?? term);

  // If no glossary entry exists, render plain text (graceful degradation)
  if (!entry) {
    return <span className={className} style={style}>{children ?? term}</span>;
  }

  const wrapperCls = `${badge ? 'term-label-badge' : 'term-label'} ${className}`.trim();

  return (
    <>
      <span
        ref={containerRef}
        className={wrapperCls}
        style={style}
        data-glossary-term={term}
        onMouseEnter={handleMouseEnter}
        onMouseLeave={handleMouseLeave}
        onClick={handleClick}
        role="button"
        tabIndex={0}
        onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') handleClick(e as any); }}
        aria-label={`${entry.display} — ${entry.short}`}
      >
        {value !== undefined ? (
          <span className="term-label-value">
            <span>{labelText}</span>
            <span className="term-label-val-number">{value}</span>
          </span>
        ) : labelText}
      </span>

      {/* Tooltip (portal, z-index 99999) */}
      {tooltipVisible && createPortal(
        <div
          ref={tooltipRef}
          className="tooltip-box"
          style={{
            position: 'fixed',
            top: tooltipCoords.top,
            left: tooltipCoords.left,
            zIndex: 99999,
            pointerEvents: 'none',
            maxWidth: 320,
          }}
          role="tooltip"
        >
          {entry.short}
        </div>,
        document.body,
      )}

      {/* Drill-down popover */}
      {drillOpen && drillEntry && (
        <DrillDown
          entry={drillEntry}
          value={value}
          onClose={() => setDrillOpen(false)}
          onNavigate={handleNavigate}
        />
      )}
    </>
  );
};

export default TermLabel;
