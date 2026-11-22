# Dashboard Chat

The dashboard Chat page is a local control surface for SSSF. Start the visualizer
from the repository whose trace database should be shown, or set `SSSF_REPOS` to
a comma-separated allowlist of absolute local repository paths. The page only
shows existing allowlisted directories.

A request is executed with argv (never a shell): `just scout <request>` or
`just build <request>`, with the request kept as one argument. Conversations are
scoped to the selected repository and remain in the server process while it is
running.

Chat API routes are intentionally loopback-only and same-origin:

- `GET /api/chat/repos`
- `POST /api/chat/conversations` (`repo_id`, `message`, optional `workflow`)
- `GET/POST /api/chat/conversations/:id`

Do not expose the server through a proxy or public bind. The chat server rejects
non-loopback hosts and cross-origin requests before reading a request body.
