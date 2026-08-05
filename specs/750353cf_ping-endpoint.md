# Plan: Add /ping endpoint

1. In `.claude/skills/sssf/apps/visualizer/server/index.ts`, add a `GET /ping` Bun route (alongside the existing `/api/health`/`/health` routes) that returns HTTP 200 with the plain-text body `pong`. Wrap it with `safely(...)` like the other routes for consistency.
2. Verify: start `bun run server`, request `http://localhost:4600/ping`, confirm status 200 and body `pong`; run `bun run typecheck`.
