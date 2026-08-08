import { useTheme } from "../ThemeContext"

function BottleneckPanel({ airports }) {
  const { theme } = useTheme()
  const reliable = airports.filter(a => a.is_reliable)
  const topByBetweenness = [...reliable]
    .sort((a, b) => b.betweenness - a.betweenness)
    .slice(0, 10)

  return (
    <div style={{
      flex: "0 0 340px",
      background: theme.cardBg,
      border: `1px solid ${theme.border}`,
      borderRadius: "4px",
      padding: "20px 24px",
      boxSizing: "border-box",
      fontFamily: "'Inter', system-ui, sans-serif",
    }}>
      <div style={{ fontSize: "13px", fontWeight: 700, color: theme.textPrimary, marginBottom: "2px" }}>
        Top Bottleneck Airports
      </div>
      <div style={{ fontSize: "11px", color: theme.textMuted, lineHeight: 1.4, marginBottom: "16px" }}>
        Ranked by structural centrality — how often this airport sits on the
        shortest path between other airports in Delta's network.
      </div>

      <div style={{
        display: "flex",
        fontSize: "10px",
        color: theme.textMuted,
        padding: "0 10px 8px",
        borderBottom: `1px solid ${theme.borderLight}`,
        fontFamily: "'JetBrains Mono', monospace",
        textTransform: "uppercase",
        letterSpacing: "0.03em",
      }}>
        <div style={{ width: "26px" }}>#</div>
        <div style={{ flex: 1 }}>Airport</div>
        <div style={{ width: "100px", textAlign: "right" }}>Centrality</div>
        <div style={{ width: "90px", textAlign: "right" }}>Relay</div>
      </div>

      {topByBetweenness.map((airport, i) => (
        <div key={airport.Origin} style={{
          display: "flex",
          alignItems: "center",
          padding: "9px 10px",
          borderBottom: `1px solid ${theme.rowBorder}`,
          fontSize: "12.5px",
        }}>
          <div style={{ width: "26px", color: theme.textMuted, fontFamily: "'JetBrains Mono', monospace" }}>
            {i + 1}
          </div>
          <div style={{ flex: 1, fontWeight: 700, color: theme.textPrimary }}>
            {airport.Origin}
          </div>
          <div style={{ width: "100px", textAlign: "right", fontFamily: "'JetBrains Mono', monospace", color: theme.accentColor }}>
            {(airport.betweenness * 100).toFixed(1)}%
          </div>
          <div style={{ width: "90px", textAlign: "right", fontFamily: "'JetBrains Mono', monospace", color: theme.textSecondary }}>
            {(airport.inheritance_rate * 100).toFixed(1)}%
          </div>
        </div>
      ))}
    </div>
  )
}

export default BottleneckPanel
