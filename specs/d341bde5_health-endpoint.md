# Plan: Add health endpoint

1. Update `.claude/skills/sssf/apps/visualizer/server/index.ts` to register a `GET /health` Bun route that returns HTTP 200 with the plain-text body `ok`. Leave the existing `/api/health` diagnostic response unchanged.
2. Verify from the visualizer app: start `bun run server`, request `http://localhost:4600/health`, and confirm status 200 and body `ok`; run `bun run typecheck`.
