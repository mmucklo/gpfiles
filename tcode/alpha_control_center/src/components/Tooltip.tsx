import { useState, useRef, useLayoutEffect, useCallback } from 'react';
import { createPortal } from 'react-dom';
import './Tooltip.css';

interface TooltipProps {
  text: string;
  children: React.ReactNode;
}

interface TooltipCoords {
  left: number;
  top: number;
  transformX: string;
  transformY: string;
}

const PADDING = 8; // px of breathing room from viewport edge

/**
 * Tooltip with flip() + shift() positioning — tooltip never clips at viewport edges.
 *
 * Placement algorithm (mirrors @floating-ui behaviour):
 *  1. Preferred placement: above the anchor.
 *  2. flip(): if not enough space above, place below instead.
 *  3. shift(): clamp horizontally so the box stays within [PADDING, vw-PADDING].
 *
 * Renders into a portal (document.body) so z-index always wins over stacking
 * contexts inside the dashboard, and position: fixed coordinates are always
 * relative to the viewport.
 */
function computePosition(
  anchor: DOMRect,
  tooltipW: number,
  tooltipH: number,
  viewportW: number,
  viewportH: number
): { top: number; left: number } {
  const gap = 8; // px between anchor and tooltip

  // -- Vertical: prefer above, flip to below if insufficient space --
  const spaceAbove = anchor.top;
  const spaceBelow = viewportH - anchor.bottom;
  let top: number;
  if (spaceAbove >= tooltipH + gap + PADDING || spaceAbove >= spaceBelow) {
    // Place above
    top = anchor.top - tooltipH - gap;
  } else {
    // Flip: place below
    top = anchor.bottom + gap;
  }

  // -- Horizontal: center on anchor, then shift to stay in-viewport --
  let left = anchor.left + anchor.width / 2 - tooltipW / 2;
  // shift left if overflowing right edge
  if (left + tooltipW > viewportW - PADDING) {
    left = viewportW - PADDING - tooltipW;
  }
  // shift right if overflowing left edge
  if (left < PADDING) {
    left = PADDING;
  }

  // -- Vertical clamp: ensure tooltip doesn't go off-screen top/bottom --
  if (top < PADDING) top = PADDING;
  if (top + tooltipH > viewportH - PADDING) top = viewportH - PADDING - tooltipH;

  return { top, left };
}

const Tooltip: React.FC<TooltipProps> = ({ text, children }) => {
  const [visible, setVisible] = useState(false);
  const [coords, setCoords] = useState<TooltipCoords>({ left: 0, top: 0, transformX: '-50%', transformY: '-100%' });
  const containerRef = useRef<HTMLDivElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const rafRef = useRef<number>(0);

  const reposition = useCallback(() => {
    if (!containerRef.current || !tooltipRef.current) return;
    const anchor = containerRef.current.getBoundingClientRect();
    const tt = tooltipRef.current.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;

    const { top, left } = computePosition(anchor, tt.width, tt.height, vw, vh);
    setCoords({ left, top, transformX: '0', transformY: '0' });
  }, []);

  const handleMouseEnter = useCallback(() => {
    setVisible(true);
  }, []);

  const handleMouseLeave = useCallback(() => {
    setVisible(false);
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
  }, []);

  // After tooltip renders, compute its real position
  useLayoutEffect(() => {
    if (!visible) return;
    // Initial position: center above, possibly off-screen — reposition corrects it
    if (containerRef.current) {
      const anchor = containerRef.current.getBoundingClientRect();
      setCoords({
        left: anchor.left + anchor.width / 2,
        top: anchor.top,
        transformX: '-50%',
        transformY: '-100%',
      });
    }
    // Next frame: tooltip has rendered with real dimensions — reposition properly
    rafRef.current = requestAnimationFrame(reposition);
    return () => { if (rafRef.current) cancelAnimationFrame(rafRef.current); };
  }, [visible, reposition]);

  const tooltipStyle: React.CSSProperties = {
    position: 'fixed',
    left: coords.left,
    top: coords.top,
    transform: `translateX(${coords.transformX}) translateY(${coords.transformY})`,
    zIndex: 99999,
    pointerEvents: 'none',
  };

  return (
    <div
      ref={containerRef}
      className="tooltip-container"
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
      onFocus={handleMouseEnter}
      onBlur={handleMouseLeave}
    >
      {children}
      {visible && createPortal(
        <div ref={tooltipRef} className="tooltip-box" style={tooltipStyle} role="tooltip">
          {text}
        </div>,
        document.body
      )}
    </div>
  );
};

export default Tooltip;
