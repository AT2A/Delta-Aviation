import { NavLink } from "react-router-dom"
import { useTheme } from "../ThemeContext"

function NavBar() {
  const { theme, mode, toggleTheme } = useTheme()

  const pillStyle = ({ isActive }) => ({
    padding: "8px 16px",
    borderRadius: "7px",
    fontSize: "13px",
    fontWeight: 600,
    fontFamily: "inherit",
    textDecoration: "none",
    border: "none",
    cursor: "pointer",
    background: isActive ? theme.pillActiveBg : "transparent",
    color: isActive ? theme.pillActiveText : theme.textSecondary,
  })

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        height: "64px",
        padding: "0 32px",
        background: theme.navBg,
        borderBottom: `1px solid ${theme.border}`,
        position: "sticky",
        top: 0,
        zIndex: 10,
        fontFamily: "'Inter', system-ui, sans-serif",
        color: theme.textPrimary,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
        <svg width="26" height="26" viewBox="0 0 26 26">
          <circle cx="6" cy="20" r="3" fill={theme.accentColor} />
          <circle cx="20" cy="6" r="3" fill={theme.accentColor} />
          <circle cx="20" cy="20" r="3" fill={theme.textMuted} />
          <line x1="6" y1="20" x2="20" y2="6" stroke={theme.accentColor} strokeWidth="2" />
          <line x1="20" y1="6" x2="20" y2="20" stroke={theme.textFaint} strokeWidth="2" />
        </svg>
        <div>
          <div style={{ fontSize: "14px", fontWeight: 700, letterSpacing: "0.02em" }}>
            ROTATION NETWORK
          </div>
          <div style={{ fontSize: "11px", color: theme.textMuted, fontFamily: "'JetBrains Mono', monospace" }}>
            DELTA AIR LINES · DELAY PROPAGATION
          </div>
        </div>
      </div>

      <div style={{ display: "flex", gap: "4px", background: theme.pillTrackBg, padding: "4px", borderRadius: "10px" }}>
        <NavLink to="/" end style={pillStyle}>
          Network Overview
        </NavLink>
        <NavLink to="/replay" style={pillStyle}>
          Live Replay &amp; Disruption
        </NavLink>
        <NavLink to="/analysis" style={pillStyle}>
          Propagation Analysis
        </NavLink>
      </div>

      <button
        onClick={toggleTheme}
        style={{
          border: `1px solid ${theme.border}`,
          background: theme.cardBg,
          color: theme.textSecondary,
          borderRadius: "20px",
          padding: "7px 16px",
          fontSize: "12px",
          fontWeight: 600,
          fontFamily: "'Inter', system-ui, sans-serif",
          cursor: "pointer",
        }}
      >
        {mode === "dark" ? "☼ Light" : "☾ Dark"}
      </button>
    </div>
  )
}

export default NavBar
