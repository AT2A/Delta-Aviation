// Vite only exposes env vars prefixed VITE_ to client code. Empty-string
// default preserves today's relative-path behavior, which the dev server's
// proxy (vite.config.js) resolves to localhost:8000 -- so local dev is
// unaffected when VITE_API_BASE_URL is unset. In production there's no
// dev proxy, so a build needs this set (see .env.production) to reach the
// deployed backend directly.
export const API_BASE = import.meta.env.VITE_API_BASE_URL || ""
