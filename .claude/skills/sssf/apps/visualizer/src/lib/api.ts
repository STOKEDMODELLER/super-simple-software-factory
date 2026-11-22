import type {
  Envelope,
  EventRow,
  EventsPage,
  GateResult,
  HealthResponse,
  PromptsResponse,
  SessionDetail,
  SessionSummary,
  SessionsLiveResponse,
} from './types'

async function getJson(url: string): Promise<unknown> {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`GET ${url} → ${res.status}`)
  return res.json()
}

export function fetchSessions(): Promise<SessionSummary[]> {
  return getJson('/api/sessions') as Promise<SessionSummary[]>
}

export function fetchSessionsLive(cursors: Record<string, number>): Promise<SessionsLiveResponse> {
  const query = encodeURIComponent(JSON.stringify(cursors))
  return getJson(`/api/sessions/live?cursors=${query}`) as Promise<SessionsLiveResponse>
}

export async function fetchSession(adwId: string): Promise<SessionDetail> {
  const detail = (await getJson(`/api/sessions/${encodeURIComponent(adwId)}`)) as SessionDetail
  return {
    session: detail.session,
    usage: detail.usage ?? { read: 0, written: 0 },
    phases: detail.phases ?? [],
    agents: detail.agents ?? [],
  }
}

export async function fetchEvents(adwId: string, after: number, limit = 500): Promise<EventsPage> {
  const page = (await getJson(
    `/api/sessions/${encodeURIComponent(adwId)}/events?after=${after}&limit=${limit}`,
  )) as EventsPage | EventRow[]
  if (Array.isArray(page)) {
    const cursor = page.reduce((max, e) => Math.max(max, e.rowid), after)
    return { events: page, cursor, has_more: page.length === limit }
  }
  return { events: page.events ?? [], cursor: page.cursor ?? after, has_more: page.has_more ?? false }
}

/** Archive a run out of the review list (or restore it with archived=false). */
export async function archiveSession(adwId: string, archived = true): Promise<void> {
  const url = `/api/sessions/${encodeURIComponent(adwId)}/archive`
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ archived }),
  })
  if (!res.ok) throw new Error(`POST ${url} → ${res.status}`)
}

export interface ChatRepo { id: string; name: string; path: string }
export interface ChatMessage { id: string; role: 'user' | 'assistant'; content: string; workflow?: 'scout' | 'build'; status?: string; created_at: string }
export interface ChatConversation { id: string; repo: ChatRepo; messages: ChatMessage[]; created_at: string }

export function fetchChats(repo: string): Promise<ChatConversation[]> {
  return getJson('/api/chat/conversations?repo=' + encodeURIComponent(repo)) as Promise<ChatConversation[]>
}

export function fetchChatRepos(): Promise<ChatRepo[]> { return getJson('/api/chat/repos') as Promise<ChatRepo[]> }
export async function createChat(repo_id: string, message: string, workflow: 'scout' | 'build'): Promise<ChatConversation> {
  const data = await send('/api/chat/conversations', 'POST', { repo_id, message, workflow })
  return data as ChatConversation
}
export async function continueChat(id: string, message: string, workflow: 'scout' | 'build'): Promise<ChatConversation> {
  return await send(`/api/chat/conversations/${encodeURIComponent(id)}`, 'POST', { message, workflow }) as ChatConversation
}

export function fetchHealth(): Promise<HealthResponse> {
  return getJson('/api/health') as Promise<HealthResponse>
}

// PhaseDetail imports the prompts type from here alongside fetchPrompts.
export type { PromptsResponse }

export async function fetchPrompts(adwId: string, agent: string): Promise<PromptsResponse> {
  const res = await fetch(
    `/api/sessions/${encodeURIComponent(adwId)}/agents/${encodeURIComponent(agent)}/prompts`,
  )
  // Not recorded (or endpoint not deployed yet) renders as "no prompts", not an error.
  if (res.status === 404) return { system: null, user: null }
  if (!res.ok) throw new Error(`GET prompts → ${res.status}`)
  const data = (await res.json()) as Partial<PromptsResponse>
  return { system: data.system ?? null, user: data.user ?? null }
}

export function fetchEnvelopes(adwId: string): Promise<Envelope[]> {
  return getJson(`/api/sessions/${encodeURIComponent(adwId)}/envelopes`) as Promise<Envelope[]>
}

export function fetchGates(adwId: string): Promise<GateResult[]> {
  return getJson(`/api/sessions/${encodeURIComponent(adwId)}/gates`) as Promise<GateResult[]>
}

// ── roster settings ──────────────────────────────────────────────────────────

export interface AgentEntry {
  name: string
  /** null = inherits defaults.model */
  model: string | null
  thinking: string | null
  color: string | null
  purpose: string
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
  ref: string
  contextWindow: string
}

export function fetchModels(): Promise<ModelOption[]> {
  return getJson('/api/models') as Promise<ModelOption[]>
}

export function fetchProfiles(): Promise<{ repo_root: string; profiles: string[]; active: string | null }> {
  return getJson('/api/config') as Promise<{ repo_root: string; profiles: string[]; active: string | null }>
}

export function fetchProfile(profile: string): Promise<ProfileDetail> {
  return getJson(`/api/config/${encodeURIComponent(profile)}`) as Promise<ProfileDetail>
}

/** Server errors carry a message worth showing — a rejected model ref explains itself. */
async function send(url: string, method: string, body: unknown): Promise<unknown> {
  const res = await fetch(url, {
    method,
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  })
  const data = (await res.json().catch(() => ({}))) as { error?: string }
  if (!res.ok) throw new Error(data.error ?? `${method} ${url} → ${res.status}`)
  return data
}

export function patchAgent(
  profile: string,
  agent: string,
  patch: { model?: string; thinking?: string },
): Promise<unknown> {
  return send(
    `/api/config/${encodeURIComponent(profile)}/agents/${encodeURIComponent(agent)}`,
    'PATCH',
    patch,
  )
}

export function createProfile(name: string, from: string): Promise<unknown> {
  return send('/api/config-profiles', 'POST', { name, from })
}
