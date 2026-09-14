interface Point {
  label: string;
  value: number;
}

interface Props {
  points: Point[];
  /** Accessible name describing what the chart shows. */
  ariaLabel?: string;
  /** Noun for each bar, used in the screen-reader summary. */
  unitLabel?: string;
}

/**
 * Dependency-free SVG bar chart with gridlines, hover tooltips and an
 * accessible text fallback for screen-reader users.
 */
export function BarChart({ points, ariaLabel, unitLabel = "value" }: Props) {
  if (!points.length) {
    return <div className="empty-state">No data for this period</div>;
  }

  const max = Math.max(...points.map((p) => p.value), 1);
  const total = points.reduce((sum, p) => sum + p.value, 0);
  const barWidth = 100 / points.length;
  const plotBottom = 40;
  const peak = points.reduce((best, p) => (p.value > best.value ? p : best), points[0]);

  const summary = ariaLabel ?? `Bar chart of ${unitLabel}`;

  return (
    <div>
      <div className="chart-scale">
        <span className="muted">peak {peak.value}</span>
        <span className="muted">
          total {total} {unitLabel}
        </span>
      </div>
      <svg
        viewBox="0 0 100 42"
        className="bar-chart"
        preserveAspectRatio="none"
        role="img"
        aria-label={`${summary}. Peak ${peak.value} at ${peak.label}. Total ${total} ${unitLabel}.`}
      >
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
          const visible = Math.max(height, point.value > 0 ? 1 : 0);
          return (
            <g key={`${point.label}-${i}`}>
              <title>{`${point.label}: ${point.value} ${unitLabel}`}</title>
              <rect
                x={i * barWidth + barWidth * 0.14}
                y={plotBottom - visible}
                width={barWidth * 0.72}
                height={visible}
                className="bar"
                rx={0.8}
              />
            </g>
          );
        })}
        <line x1="0" y1={plotBottom} x2="100" y2={plotBottom} className="axis" strokeWidth="0.35" />
      </svg>
      {/* Text alternative: the SVG alone conveys nothing to a screen reader. */}
      <details className="chart-data">
        <summary>View data table</summary>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th scope="col">Period</th>
                <th scope="col">{unitLabel}</th>
              </tr>
            </thead>
            <tbody>
              {points.map((point, i) => (
                <tr key={`${point.label}-row-${i}`}>
                  <td>{point.label}</td>
                  <td>{point.value}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}
