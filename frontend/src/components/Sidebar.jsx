const INHERITED_PCT = 30.3

function Sidebar({ airports }) {
  const reliable = airports.filter(a => a.is_reliable)
  const topByBetweenness = [...reliable]
    .sort((a, b) => b.betweenness - a.betweenness)
    .slice(0, 10)

  return (
    <div style={{
      width: "320px",
      height: "100vh",
      backgroundColor: "#111",
      color: "#eee",
      padding: "20px",
      overflowY: "auto",
      fontFamily: "sans-serif",
      boxSizing: "border-box",
    }}>
      <h2 style={{ color: "#fff", marginTop: 0 }}>
        Delta Delay Network
      </h2>

      <div style={{
        backgroundColor: "#1a1a2e",
        borderLeft: "3px solid #4a9eff",
        padding: "12px",
        marginBottom: "24px",
        borderRadius: "4px",
      }}>
        <div style={{ fontSize: "28px", fontWeight: "bold", color: "#4a9eff" }}>
          {INHERITED_PCT}%
        </div>
        <div style={{ fontSize: "13px", color: "#aaa", marginTop: "4px" }}>
          of Delta's 2025 delay minutes were inherited from a prior late flight,
          not caused fresh at the departure airport.
        </div>
      </div>

      <h3 style={{ color: "#fff", marginBottom: "8px" }}>
        Top Bottleneck Airports
      </h3>
      <p style={{ fontSize: "12px", color: "#888", marginBottom: "16px" }}>
        Ranked by structural centrality — how often this airport sits on the
        shortest path between other airports in Delta's network.
      </p>

      {topByBetweenness.map((airport, i) => (
        <div key={airport.Origin} style={{
          marginBottom: "12px",
          padding: "10px",
          backgroundColor: "#1a1a1a",
          borderRadius: "4px",
          borderLeft: `3px solid ${airport.inheritance_rate > 0.12 ? "#ff6464" : "#4a9eff"}`,
        }}>
          <div style={{ display: "flex", justifyContent: "space-between" }}>
            <span style={{ fontWeight: "bold", fontSize: "15px" }}>
              {i + 1}. {airport.Origin}
            </span>
            <span style={{ fontSize: "12px", color: "#888" }}>
              {(airport.betweenness * 100).toFixed(1)}% centrality
            </span>
          </div>
          <div style={{ fontSize: "12px", color: "#aaa", marginTop: "4px" }}>
            Relay rate: {(airport.inheritance_rate * 100).toFixed(1)}%
            {airport.inheritance_rate > 0.12
              ? " · high delay relay"
              : " · well buffered"}
          </div>
        </div>
      ))}

      <div style={{ marginTop: "24px", fontSize: "11px", color: "#555" }}>
        <div style={{ marginBottom: "6px" }}>
          <span style={{
            display: "inline-block", width: "10px", height: "10px",
            backgroundColor: "#ff6464", borderRadius: "50%", marginRight: "6px"
          }} />
          High relay rate (&gt;12%)
        </div>
        <div>
          <span style={{
            display: "inline-block", width: "10px", height: "10px",
            backgroundColor: "#4a9eff", borderRadius: "50%", marginRight: "6px"
          }} />
          Well buffered
        </div>
      </div>
    </div>
  )
}

export default Sidebar