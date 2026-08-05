# Plan: Add /ping endpoint to visualizer server

## Context

`.claude/skills/sssf/apps/visualizer/server/index.ts` registers Bun routes in
a `Bun.serve({ routes: { ... } })` object, each wrapped with the `safely(...)`
helper so a handler error returns a 500 instead of crashing the process. The
existing `/api/health` route (around line 116) is the closest analog: it's a
plain `GET` route returning JSON. Non-`/api/` paths currently fall through to
`serveStatic`, which either serves the built SPA or a plaintext status
message — so an unmatched top-level route like `/ping` needs its own entry in
`routes` or it will be swallowed by that fallback.

## Steps

1. In `.claude/skills/sssf/apps/visualizer/server/index.ts`, add a `/ping`
   entry to the `routes` object in `Bun.serve({...})`, placed near
   `/api/health` for readability. Wrap it with `safely(...)` for consistency
   with every other route. Return a plain-text `pong` body with status 200,
   e.g.:

   ```ts
   "/ping": safely(() => new Response("pong", {
     headers: { "content-type": "text/plain; charset=utf-8" },
   })),
   ```

2. No changes needed elsewhere — this route is intentionally outside `/api/`
   (a liveness check, not part of the JSON API), so it doesn't touch
   `../shared/types.ts`, `db.ts`, or the `fetch` fallback's
   `pathname.startsWith("/api/")` guard.

## Verification

- `cd .claude/skills/sssf/apps/visualizer && bun run server` (or
  `bun run server/index.ts` from that dir), then in another shell:
  `curl -i http://localhost:4600/ping` — expect `HTTP/1.1 200 OK` and body
  `pong`.
- `bun run typecheck` (runs `vue-tsc --noEmit`) from the same `visualizer`
  directory to confirm no type errors were introduced.
