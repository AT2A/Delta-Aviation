import { NavLink } from "react-router-dom"

const pillStyle = ({ isActive }) => ({
  padding: "8px 16px",
  borderRadius: "7px",
  fontSize: "13px",
  fontWeight: 600,
  fontFamily: "inherit",
  textDecoration: "none",
  border: "none",
  cursor: "pointer",
  background: isActive ? "#14181f" : "transparent",
  color: isActive ? "#ffffff" : "#5b6472",
})

function NavBar() {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        height: "64px",
        padding: "0 32px",
        background: "#ffffff",
        borderBottom: "1px solid #e2e5e9",
        position: "sticky",
        top: 0,
        zIndex: 10,
        fontFamily: "'Inter', system-ui, sans-serif",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
        <svg width="26" height="26" viewBox="0 0 26 26">
          <circle cx="6" cy="20" r="3" fill="#2f5fd6" />
          <circle cx="20" cy="6" r="3" fill="#2f5fd6" />
          <circle cx="20" cy="20" r="3" fill="#8b93a0" />
          <line x1="6" y1="20" x2="20" y2="6" stroke="#2f5fd6" strokeWidth="2" />
          <line x1="20" y1="6" x2="20" y2="20" stroke="#c9cdd4" strokeWidth="2" />
        </svg>
        <div>
          <div style={{ fontSize: "14px", fontWeight: 700, letterSpacing: "0.02em" }}>
            ROTATION NETWORK
          </div>
          <div style={{ fontSize: "11px", color: "#8b93a0", fontFamily: "'JetBrains Mono', monospace" }}>
            DELTA AIR LINES · DELAY PROPAGATION
          </div>
        </div>
      </div>

      <div style={{ display: "flex", gap: "4px", background: "#f0f1f3", padding: "4px", borderRadius: "10px" }}>
        <NavLink to="/" end style={pillStyle}>
          Network Overview
        </NavLink>
        <NavLink to="/replay" style={pillStyle}>
          Live Replay &amp; Disruption
        </NavLink>
      </div>
    </div>
  )
}

export default NavBar