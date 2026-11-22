import { existsSync, realpathSync, readFileSync } from 'node:fs'
import { isAbsolute, join } from 'node:path'

export type Workflow = 'scout' | 'build'
export interface ChatRepo { id: string; name: string; path: string }
export interface ChatMessage { id: string; role: 'user' | 'assistant'; content: string; workflow?: Workflow; status?: string; created_at: string }
export interface ChatConversation { id: string; repo: ChatRepo; messages: ChatMessage[]; created_at: string }

const MAX_MESSAGE = 20_000
const SAFE_ID = /^[A-Za-z0-9._-]+$/

function canonical(path: string): string | null {
  if (!isAbsolute(path) || !existsSync(path)) return null
  try { return realpathSync(path) } catch { return null }
}

/** Repositories are explicit, local, and never accepted from the request path. */
export function allowedRepos(repoRoot: string, raw = process.env.SSSF_REPOS): ChatRepo[] {
  const paths = (raw ? raw.split(',').map((p) => p.trim()).filter(Boolean) : [repoRoot])
    .map((p) => canonical(p)).filter((p): p is string => !!p)
  const unique = [...new Set(paths)]
  return unique.map((path) => ({
    id: Buffer.from(path).toString('base64url'),
    name: path.split('/').pop() || path,
    path,
  }))
}

export function repoFor(repos: ChatRepo[], id: unknown): ChatRepo | undefined {
  return typeof id === 'string' ? repos.find((repo) => repo.id === id) : undefined
}

export function validWorkflow(value: unknown): value is Workflow { return value === 'scout' || value === 'build' }
export function validConversationId(value: string): boolean { return SAFE_ID.test(value) && value !== '.' && value !== '..' }
export function validateMessage(value: unknown): string {
  if (typeof value !== 'string' || !value.trim()) throw new Error('message is required')
  if (value.length > MAX_MESSAGE) throw new Error(`message exceeds ${MAX_MESSAGE} characters`)
  return value.trim()
}

export async function runWorkflow(conversation: ChatConversation, workflow: Workflow) {
  const { repo, messages } = conversation
  const history = messages.slice(0, -1).map(m => m.role + ': ' + m.content).join('\n').slice(-12000)
  const prompt = 'Repository conversation\n' + history + '\nCurrent request\n' + messages.at(-1)!.content
  const runId = crypto.randomUUID().replaceAll('-', '').slice(0, 8)
  try {
    const child = Bun.spawn(['just', workflow, '--adw-id', runId, '--', prompt], {
      cwd: repo.path, stdout: 'pipe', stderr: 'pipe',
    })
    const [stdout, stderr, code] = await Promise.all([
      new Response(child.stdout).text(), new Response(child.stderr).text(), child.exited,
    ])
    if (!code) {
      const agent = workflow === 'build' ? 'builder' : 'scout'
      const file = join(repo.path, 'adws/adw_data/sessions', runId, agent, 'envelope.json')
      if (existsSync(file)) {
        const data = JSON.parse(readFileSync(file, 'utf8'))
        const details = workflow === 'build' ? data.changed_files :
          data.findings?.map((f: {file: string; note: string}) => f.file + ' — ' + f.note)
        return {code, output: [data.summary, ...(details || []), 'Run ' + runId].join('\n')}
      }
    }
    return {code, output: (stdout + '\n' + stderr).trim().slice(-12000)}
  } catch (error) {
    return {code: 1, output: (error as Error).message}
  }
}

export function newId(): string { return `${Date.now().toString(36)}-${crypto.randomUUID().slice(0, 8)}` }
export function message(role: ChatMessage['role'], content: string, extra: Partial<ChatMessage> = {}): ChatMessage {
  return { id: newId(), role, content, created_at: new Date().toISOString(), ...extra }
}
