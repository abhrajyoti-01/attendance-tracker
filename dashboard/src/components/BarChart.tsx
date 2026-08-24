interface Point {
  label: string;
  value: number;
}

/** Dependency-free SVG bar chart with gridlines and hover tooltips. */
export function BarChart({ points }: { points: Point[] }) {
  if (!points.length) {
    return <div className="empty-state">No data for this period</div>;
  }
  const max = Math.max(...points.map((p) => p.value), 1);
  const barWidth = 100 / points.length;
  const plotBottom = 40;

  return (
    <div>
      <div style={{ textAlign: "right", fontSize: "0.72rem", color: "var(--muted)", marginBottom: 4 }}>
        peak {max}
      </div>
      <svg viewBox="0 0 100 42" className="bar-chart" preserveAspectRatio="none" role="img">
        {[0.25, 0.5, 0.75].map((fraction) => (
          <line
            key={fraction}
            x1="0"
            x2="100"
            y1={plotBottom - plotBottom * fraction}
            y2={plotBottom - plotBottom * fraction}
            className="gridline"
            strokeWidth="0.25"
          />
        ))}
        {points.map((point, i) => {
          const height = (point.value / max) * plotBottom;
          return (
            <g key={`${point.label}-${i}`}>
              <title>{`${point.label}: ${point.value}`}</title>
              <rect
                x={i * barWidth + barWidth * 0.14}
                y={plotBottom - Math.max(height, point.value > 0 ? 1 : 0)}
                width={barWidth * 0.72}
                height={Math.max(height, point.value > 0 ? 1 : 0)}
                className="bar"
                rx={0.8}
              />
            </g>
          );
        })}
        <line x1="0" y1={plotBottom} x2="100" y2={plotBottom} className="axis" strokeWidth="0.35" />
      </svg>
    </div>
  );
}
