/**
 * Roster configuration: read the profiles in adws/adw_sssf_config/, and patch
 * an agent's model or thinking level in place.
 *
 * WHY LINE SURGERY AND NOT A YAML ROUND-TRIP: sssf.config.yaml is more comment
 * than data. The comments explain why each agent has the tools it has, why the
 * council seats are read-only, which provider needs which auth — that is the
 * documentation an engineer edits the file by. `yaml.parse` → `yaml.stringify`
 * would silently delete all of it. So a patch finds the one line it owns and
 * rewrites only that line's value. Everything else on disk is untouched, byte
 * for byte.
 *
 * The one thing a patch does drop is a trailing comment on the line it edits —
 * it described the OLD model ("Codex via pi's OAuth login") and would be a lie
 * about the new one.
 */
import { existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from 'node:fs'
import { resolve, sep } from 'node:path'

const CONFIG_DIR_RELATIVE = 'adws/adw_sssf_config'
/** Profile / agent names are path segments and YAML keys — keep them boring. */
const SAFE_NAME = /^[A-Za-z0-9._-]+$/

export interface AgentEntry {
  name: string
  /** null when the agent inherits defaults.model — the UI shows it as inherited. */
  model: string | null
  thinking: string | null
  color: string | null
  purpose: string
  /** [] = read-only, null = unrestricted (subject to protected_files). */
  writes: string[] | null
  tools: string[] | null
}

export interface ProfileDetail {
  profile: string
  path: string
  defaults: { model: string | null; thinking: string | null }
  agents: AgentEntry[]
}

export interface ModelOption {
  provider: string
  id: string
  /** "provider/id" — exactly what a config `model:` line wants. */
  ref: string
  contextWindow: string
}

export function isSafeName(value: string): boolean {
  return SAFE_NAME.test(value) && value !== '.' && value !== '..'
}

/** The config directory of the repo the visualizer is pointed at. */
export function configDir(repoRoot: string): string {
  return resolve(repoRoot, CONFIG_DIR_RELATIVE)
}

/** Resolve a profile name to a path, refusing anything that escapes the dir. */
export function profilePath(repoRoot: string, profile: string): string {
  if (!isSafeName(profile)) throw new Error(`invalid profile name: ${profile}`)
  const dir = configDir(repoRoot)
  const file = resolve(dir, profile.endsWith('.yaml') ? profile : `${profile}.yaml`)
  if (file !== dir && !file.startsWith(dir + sep)) throw new Error('invalid profile path')
  return file
}

export function listProfiles(repoRoot: string): string[] {
  const dir = configDir(repoRoot)
  if (!existsSync(dir)) return []
  return readdirSync(dir)
    .filter((f) => f.endsWith('.yaml') || f.endsWith('.yml'))
    .sort()
}

// ── minimal YAML reading ─────────────────────────────────────────────────────
//
// Only the shapes this file actually contains: two-space indent, `agents:` as a
// list of maps, scalar values, and inline `[a, b]` lists for tools/writes. A
// dependency-free reader keeps the server's install surface at zero and cannot
// reformat what it does not write.

function stripComment(value: string): string {
  // A `#` inside quotes is data (color: "#a78bfa"); outside quotes it's a comment.
  let quote: string | null = null
  for (let i = 0; i < value.length; i++) {
    const c = value[i]
    if (quote) {
      if (c === quote) quote = null
    } else if (c === '"' || c === "'") {
      quote = c
    } else if (c === '#' && (i === 0 || /\s/.test(value[i - 1]!))) {
      return value.slice(0, i)
    }
  }
  return value
}

function scalar(raw: string): string {
  const v = stripComment(raw).trim()
  if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) {
    return v.slice(1, -1)
  }
  return v
}

function inlineList(raw: string): string[] | null {
  const v = scalar(raw)
  if (!v.startsWith('[')) return null
  const inner = v.slice(1, v.endsWith(']') ? -1 : undefined).trim()
  if (!inner) return []
  return inner.split(',').map((s) => s.trim().replace(/^["']|["']$/g, '')).filter(Boolean)
}

/** Indent width of a line, or -1 for blank/comment-only lines. */
function indentOf(line: string): number {
  if (!line.trim() || line.trim().startsWith('#')) return -1
  return line.length - line.trimStart().length
}

export function readProfile(repoRoot: string, profile: string): ProfileDetail {
  const path = profilePath(repoRoot, profile)
  if (!existsSync(path)) throw new Error(`profile not found: ${profile}`)
  const lines = readFileSync(path, 'utf8').split('\n')

  const defaults: { model: string | null; thinking: string | null } = { model: null, thinking: null }
  const agents: AgentEntry[] = []
  let section: 'defaults' | 'agents' | null = null
  let current: AgentEntry | null = null
  /** Indent of the keys inside the current agent map, for nested-block skipping. */
  let agentKeyIndent = -1

  for (const line of lines) {
    const indent = indentOf(line)
    if (indent === 0) {
      const key = line.split(':')[0]!.trim()
      section = key === 'defaults' ? 'defaults' : key === 'agents' ? 'agents' : null
      current = null
      continue
    }
    if (indent < 0) continue
    const body = line.trim()

    if (section === 'defaults' && indent === 2) {
      if (body.startsWith('model:')) defaults.model = scalar(body.slice(6)) || null
      else if (body.startsWith('thinking:')) defaults.thinking = scalar(body.slice(9)) || null
      continue
    }

    if (section !== 'agents') continue

    if (body.startsWith('- name:')) {
      current = {
        name: scalar(body.slice(7)),
        model: null, thinking: null, color: null, purpose: '', writes: null, tools: null,
      }
      agents.push(current)
      agentKeyIndent = indent + 2      // "- " is two columns wide
      continue
    }
    if (!current || indent !== agentKeyIndent) continue   // nested block-list item — not a key

    if (body.startsWith('model:')) current.model = scalar(body.slice(6)) || null
    else if (body.startsWith('thinking:')) current.thinking = scalar(body.slice(9)) || null
    else if (body.startsWith('color:')) current.color = scalar(body.slice(6)) || null
    else if (body.startsWith('purpose:')) current.purpose = scalar(body.slice(8))
    else if (body.startsWith('writes:')) current.writes = inlineList(body.slice(7))
    else if (body.startsWith('tools:')) current.tools = inlineList(body.slice(6))
  }

  return { profile, path, defaults, agents }
}

// ── patching ─────────────────────────────────────────────────────────────────

export interface AgentPatch {
  model?: string
  thinking?: string
}

const THINKING_LEVELS = ['off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max']

/**
 * Set `model` and/or `thinking` on one agent, in place.
 *
 * Replaces the key's line when it exists, inserts it right under `- name:` when
 * the agent was inheriting from defaults. Returns the keys actually written.
 */
export function patchAgent(
  repoRoot: string,
  profile: string,
  agent: string,
  patch: AgentPatch,
): string[] {
  if (!isSafeName(agent)) throw new Error(`invalid agent name: ${agent}`)
  if (patch.model !== undefined && !/^[A-Za-z0-9._-]+\/[A-Za-z0-9._\-/[\]]+$/.test(patch.model)) {
    // agents.validate() enforces provider/id too, but failing here means the
    // engineer learns it in the settings page, not partway into a chain.
    throw new Error(`model must be written as provider/model-id, got: ${patch.model}`)
  }
  if (patch.thinking !== undefined && !THINKING_LEVELS.includes(patch.thinking)) {
    throw new Error(`thinking must be one of ${THINKING_LEVELS.join(', ')}`)
  }

  const path = profilePath(repoRoot, profile)
  if (!existsSync(path)) throw new Error(`profile not found: ${profile}`)
  const lines = readFileSync(path, 'utf8').split('\n')

  // Locate the agent's block: from its `- name:` line to the next one (or EOF).
  let start = -1
  let keyIndent = 4
  for (let i = 0; i < lines.length; i++) {
    const indent = indentOf(lines[i]!)
    if (indent < 0) continue
    const body = lines[i]!.trim()
    if (body.startsWith('- name:') && scalar(body.slice(7)) === agent) {
      start = i
      keyIndent = indent + 2
      break
    }
  }
  if (start === -1) throw new Error(`agent not found in ${profile}: ${agent}`)

  let end = lines.length
  for (let i = start + 1; i < lines.length; i++) {
    const indent = indentOf(lines[i]!)
    if (indent < 0) continue
    if (indent === 0 || (indent < keyIndent && lines[i]!.trim().startsWith('- name:'))) {
      end = i
      break
    }
    if (lines[i]!.trim().startsWith('- name:') && indent === keyIndent - 2) {
      end = i
      break
    }
  }

  const pad = ' '.repeat(keyIndent)
  const written: string[] = []
  const inserts: string[] = []
  for (const [key, value] of Object.entries(patch)) {
    if (value === undefined) continue
    let found = false
    for (let i = start + 1; i < end; i++) {
      if (indentOf(lines[i]!) !== keyIndent) continue
      if (!lines[i]!.trim().startsWith(`${key}:`)) continue
      lines[i] = `${pad}${key}: ${value}`     // stale trailing comment intentionally dropped
      found = true
      break
    }
    if (!found) inserts.push(`${pad}${key}: ${value}`)
    written.push(key)
  }
  // Spliced as one block so new keys keep patch order (model above thinking),
  // rather than the reversed order repeated single-index splices produce.
  if (inserts.length) lines.splice(start + 1, 0, ...inserts)

  writeFileSync(path, lines.join('\n'))
  return written
}

/** Copy an existing profile to a new name — the "configurations I have" story. */
export function createProfile(repoRoot: string, name: string, from: string): string {
  if (!isSafeName(name)) throw new Error(`invalid profile name: ${name}`)
  const source = profilePath(repoRoot, from)
  if (!existsSync(source)) throw new Error(`source profile not found: ${from}`)
  const target = profilePath(repoRoot, name)
  if (existsSync(target)) throw new Error(`profile already exists: ${name}`)
  mkdirSync(configDir(repoRoot), { recursive: true })
  writeFileSync(target, readFileSync(source, 'utf8'))
  return target
}

// ── available models ─────────────────────────────────────────────────────────

let modelCache: { at: number; models: ModelOption[] } | null = null
const MODEL_TTL_MS = 60_000

/**
 * What pi can actually reach right now, from `pi --list-models`.
 *
 * Authoritative on purpose: a hard-coded list goes stale the moment a provider
 * is authenticated or an extension registers one (claude-bridge does exactly
 * that), and offering a model the roster cannot resolve just moves the failure
 * to the middle of a run.
 */
export async function availableModels(): Promise<ModelOption[]> {
  if (modelCache && Date.now() - modelCache.at < MODEL_TTL_MS) return modelCache.models
  const bin = process.env.PI_PATH ?? 'pi'
  const models: ModelOption[] = []
  try {
    // PI_OFFLINE skips pi's startup network work — catalog refresh and update
    // checks. Without it the call can run past the HTTP server's idle timeout,
    // which is exactly how this endpoint first failed.
    const proc = Bun.spawn([bin, '--list-models'], {
      // stdin ignored, deliberately — the same trap agent_pi.py documents. The
      // server usually runs backgrounded, so an inherited terminal stdin puts
      // the child in a background process group where a read raises SIGTTIN and
      // it stops: no output, no error, an empty model list forever.
      stdin: 'ignore',
      stdout: 'pipe', stderr: 'pipe',
      env: { ...process.env, PI_OFFLINE: '1' },
    })
    const timer = setTimeout(() => proc.kill(), 8_000)
    // BOTH streams, deliberately. pi prints the catalog to stdout when it
    // thinks a human is watching and to stderr otherwise — under Bun.serve it
    // lands on stderr, which read the obvious way looks exactly like "no
    // models installed". Reading both makes the parse independent of pi's
    // TTY heuristics; the row filter below rejects whatever isn't a model.
    const [out, err] = await Promise.all([
      new Response(proc.stdout).text(),
      new Response(proc.stderr).text(),
    ])
    await proc.exited
    clearTimeout(timer)
    for (const line of `${out}\n${err}`.split('\n')) {
      const cols = line.trim().split(/\s+/)
      // provider · model · context · max-out · thinking · images — the header
      // row and any stray log line fail this shape and are skipped.
      if (cols.length < 3) continue
      const [provider, id, contextWindow] = cols as [string, string, string]
      if (provider === 'provider' || !/^[\d.]+[KM]?$/.test(contextWindow)) continue
      if (models.some((m) => m.ref === `${provider}/${id}`)) continue
      models.push({ provider, id, ref: `${provider}/${id}`, contextWindow })
    }
  } catch (error) {
    // pi missing or unauthenticated: an empty list renders as "run pi
    // --list-models to see options", never a broken settings page. Logged,
    // never swallowed — a settings page showing no models must be able to
    // tell you why.
    console.error(`[sssf] pi --list-models failed: ${(error as Error).message}`)
  }
  // Only a real answer is cached. Caching an empty list would pin a transient
  // failure — a timeout, a half-started pi — in place for the whole TTL, and
  // the settings page would show "no models" long after the cause cleared.
  if (models.length) modelCache = { at: Date.now(), models }
  return models
}
