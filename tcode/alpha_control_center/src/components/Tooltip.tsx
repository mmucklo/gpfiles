import { useState, useRef } from 'react';
import './Tooltip.css';

interface TooltipProps {
  text: string;
  children: React.ReactNode;
}

const Tooltip: React.FC<TooltipProps> = ({ text, children }) => {
  const [visible, setVisible] = useState(false);
  const [coords, setCoords] = useState({ x: 0, y: 0 });
  const [above, setAbove] = useState(true);
  const containerRef = useRef<HTMLDivElement>(null);

  const handleMouseEnter = (e: React.MouseEvent) => {
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    const spaceAbove = rect.top;
    setAbove(spaceAbove > 60);
    setCoords({ x: rect.left + rect.width / 2, y: above ? rect.top : rect.bottom });
    setVisible(true);
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    setCoords({ x: rect.left + rect.width / 2, y: above ? rect.top : rect.bottom });
  };

  const tooltipStyle: React.CSSProperties = {
    position: 'fixed',
    left: coords.x,
    top: above ? coords.y - 8 : coords.y + 8,
    transform: above ? 'translate(-50%, -100%)' : 'translate(-50%, 0)',
    zIndex: 99999,
  };

  return (
    <div
      ref={containerRef}
      className="tooltip-container"
      onMouseEnter={handleMouseEnter}
      onMouseMove={handleMouseMove}
      onMouseLeave={() => setVisible(false)}
    >
      {children}
      {visible && (
        <div className="tooltip-box" style={tooltipStyle}>
          {text}
        </div>
      )}
    </div>
  );
};

export default Tooltip;
