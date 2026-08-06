<script setup lang="ts">
/**
 * Roster settings: pick the model and thinking level for each task category.
 *
 * Two axes, because that is how the factory actually works:
 *   - PROFILE  — a whole roster file in adws/adw_sssf_config/. Multiple
 *                profiles are the supported way to keep several setups
 *                (`SSSF_CONFIG=cheap.yaml just sdlc "…"` swaps all of them).
 *   - CATEGORY — agents grouped by the job they do, so a change reads as
 *                "planning gets the frontier model", not "edit line 46".
 *
 * Deliberately NOT editable here: tools and `writes`. Those are the boundaries
 * that make a read-only agent read-only, and they belong in the file where the
 * comment explaining them lives — not behind a dropdown.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { Save, FolderPlus, RefreshCw, Lock } from 'lucide-vue-next'
import {
  createProfile, fetchModels, fetchProfile, fetchProfiles, patchAgent,
  type AgentEntry, type ModelOption, type ProfileDetail,
} from '../lib/api'

const THINKING = ['off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max']

/** Task categories, in the order a run actually visits them. */
const CATEGORIES: { title: string; blurb: string; match: (name: string) => boolean }[] = [
  {
    title: 'Discovery',
    blurb: 'Reads the codebase and reports where things live. Cheap and fast pays off here — it is mostly search.',
    match: (n) => n === 'scout',
  },
  {
    title: 'Planning',
    blurb: 'Turns a request into a spec the builder implements without asking questions. The highest-leverage seat in the factory.',
    match: (n) => n === 'planner',
  },
  {
    title: 'Implementation',
    blurb: 'The only agent that may change your repo. Speed matters more than subtlety once the plan is good.',
    match: (n) => n === 'builder',
  },
  {
    title: 'Review council — chairs',
    blurb: 'Split the review into one brief per seat and write the shared context pack. They never vote.',
    match: (n) => n.startsWith('chair-'),
  },
  {
    title: 'Review council — seats',
    blurb: 'Six voters, one dimension each. Mix providers on purpose: shared blind spots defeat the whole point.',
    match: (n) => n.startsWith('seat-'),
  },
  {
    title: 'Verification & recovery',
    blurb: 'The single reviewer used by simple-sdlc, and the medic that diagnoses a failed phase so a run recovers instead of dying.',
    match: (n) => n === 'reviewer' || n === 'medic',
  },
  {
    title: 'Documentation',
    blurb: 'Writes up the landed change from the diff.',
    match: (n) => n === 'documenter',
  },
]

const profiles = ref<string[]>([])
const profile = ref<string>('')
const detail = ref<ProfileDetail | null>(null)
const models = ref<ModelOption[]>([])
const loading = ref(true)
const error = ref<string | null>(null)
/** agent name → the toast line shown next to its row after a save. */
const saved = ref<Record<string, string>>({})
const busy = ref<string | null>(null)

/** Pending edits, keyed by agent — a row is dirty until Save writes it. */
const draft = ref<Record<string, { model: string; thinking: string }>>({})

const grouped = computed(() => {
  const agents = detail.value?.agents ?? []
  const claimed = new Set<string>()
  const out = CATEGORIES.map((c) => {
    const members = agents.filter((a) => c.match(a.name))
    members.forEach((m) => claimed.add(m.name))
    return { ...c, members }
  }).filter((c) => c.members.length)
  // Anything the map does not know about still gets a home — a custom agent
  // must never be invisible in the page that claims to configure the roster.
  const rest = agents.filter((a) => !claimed.has(a.name))
  if (rest.length) {
    out.push({
      title: 'Other agents',
      blurb: 'Agents this page has no category for — configured the same way.',
      match: () => false,
      members: rest,
    })
  }
  return out
})

/** Models grouped by provider for the <optgroup>s. */
const byProvider = computed(() => {
  const map = new Map<string, ModelOption[]>()
  for (const m of models.value) {
    const list = map.get(m.provider)
    if (list) list.push(m)
    else map.set(m.provider, [m])
  }
  return [...map.entries()].sort(([a], [b]) => a.localeCompare(b))
})

function effective(agent: AgentEntry): { model: string; thinking: string } {
  return {
    model: agent.model ?? detail.value?.defaults.model ?? '',
    thinking: agent.thinking ?? detail.value?.defaults.thinking ?? 'medium',
  }
}

function seed(): void {
  const next: Record<string, { model: string; thinking: string }> = {}
  for (const a of detail.value?.agents ?? []) next[a.name] = effective(a)
  draft.value = next
}

function dirty(agent: AgentEntry): boolean {
  const d = draft.value[agent.name]
  if (!d) return false
  const e = effective(agent)
  return d.model !== e.model || d.thinking !== e.thinking
}

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const list = await fetchProfiles()
    profiles.value = list.profiles
    if (!profile.value) profile.value = list.active ?? list.profiles[0] ?? ''
    if (profile.value) detail.value = await fetchProfile(profile.value)
    seed()
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    loading.value = false
  }
}

async function save(agent: AgentEntry): Promise<void> {
  const d = draft.value[agent.name]
  if (!d) return
  busy.value = agent.name
  error.value = null
  try {
    await patchAgent(profile.value, agent.name, { model: d.model, thinking: d.thinking })
    detail.value = await fetchProfile(profile.value)
    seed()
    saved.value = { ...saved.value, [agent.name]: 'saved' }
    setTimeout(() => {
      const { [agent.name]: _drop, ...rest } = saved.value
      saved.value = rest
    }, 2400)
  } catch (e) {
    error.value = `${agent.name}: ${(e as Error).message}`
  } finally {
    busy.value = null
  }
}

async function newProfile(): Promise<void> {
  const name = window.prompt('New profile name (copied from the current one):')
  if (!name) return
  error.value = null
  try {
    await createProfile(name, profile.value)
    profile.value = name.endsWith('.yaml') ? name : `${name}.yaml`
    await load()
  } catch (e) {
    error.value = (e as Error).message
  }
}

onMounted(async () => {
  await load()
  models.value = await fetchModels().catch(() => [])
})

watch(profile, async (next, prev) => {
  if (!next || next === prev) return
  detail.value = await fetchProfile(next)
  seed()
})
</script>

<template>
  <div class="settings">
    <header class="head">
      <div>
        <h2>Model settings</h2>
        <p class="sub">
          Which model runs each part of a run. Saved straight into
          <code>{{ detail?.path ?? 'adws/adw_sssf_config/' }}</code> — the next run picks it up.
        </p>
      </div>
      <div class="head-actions">
        <label class="field">
          <span class="lbl">Configuration</span>
          <select v-model="profile" class="sel">
            <option v-for="p in profiles" :key="p" :value="p">{{ p }}</option>
          </select>
        </label>
        <button class="btn" title="Copy this configuration to a new one" @click="newProfile">
          <FolderPlus :size="15" /> New
        </button>
        <button class="btn" title="Reload from disk" @click="load">
          <RefreshCw :size="15" />
        </button>
      </div>
    </header>

    <p v-if="profiles.length > 1" class="hint">
      Run any profile with <code>SSSF_CONFIG={{ profile }} just sdlc "…"</code>
    </p>

    <div v-if="error" class="err">{{ error }}</div>
    <div v-if="loading" class="faint">loading roster…</div>

    <div v-else-if="detail" class="defaults">
      <span class="lbl">Roster default</span>
      <code>{{ detail.defaults.model ?? '—' }}</code>
      <span class="dim">· thinking {{ detail.defaults.thinking ?? 'medium' }}</span>
      <span class="dim">— agents with no model of their own inherit this.</span>
    </div>

    <section v-for="cat in grouped" :key="cat.title" class="cat">
      <h3>{{ cat.title }}</h3>
      <p class="blurb">{{ cat.blurb }}</p>

      <div v-for="agent in cat.members" :key="agent.name" class="row">
        <div class="who">
          <span class="swatch" :style="{ background: agent.color || 'var(--faint)' }" />
          <div class="who-text">
            <span class="name">
              {{ agent.name }}
              <Lock
                v-if="agent.writes && agent.writes.length === 0"
                :size="12"
                class="ro"
                title="Read-only: this agent cannot modify the repo (enforced in code, not just prompted)"
              />
            </span>
            <span class="purpose">{{ agent.purpose }}</span>
          </div>
        </div>

        <div class="controls" v-if="draft[agent.name]">
          <select v-model="draft[agent.name]!.model" class="sel model">
            <optgroup v-for="[prov, list] in byProvider" :key="prov" :label="prov">
              <option v-for="m in list" :key="m.ref" :value="m.ref">
                {{ m.id }} · {{ m.contextWindow }}
              </option>
            </optgroup>
            <!-- Whatever is configured stays selectable even when pi cannot
                 currently list it, so opening this page never silently
                 rewrites a model the roster is deliberately pinned to. -->
            <option
              v-if="!models.some((m) => m.ref === draft[agent.name]!.model)"
              :value="draft[agent.name]!.model"
            >
              {{ draft[agent.name]!.model || '(inherits default)' }}
            </option>
          </select>

          <select v-model="draft[agent.name]!.thinking" class="sel think">
            <option v-for="t in THINKING" :key="t" :value="t">{{ t }}</option>
          </select>

          <button
            class="btn save"
            :disabled="!dirty(agent) || busy === agent.name"
            @click="save(agent)"
          >
            <Save :size="14" />
            {{ busy === agent.name ? 'saving…' : 'Save' }}
          </button>
          <span v-if="saved[agent.name]" class="ok">✓ {{ saved[agent.name] }}</span>
          <span v-else-if="!agent.model" class="inherited">inherited</span>
        </div>
      </div>
    </section>

    <p v-if="!models.length && !loading" class="hint">
      No models listed — the server could not run <code>pi --list-models</code>. The dropdowns still
      hold whatever the roster has configured.
    </p>
  </div>
</template>

<style scoped>
.settings {
  padding: 22px 28px 60px;
  max-width: 1100px;
}

.head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 24px;
  flex-wrap: wrap;
}

h2 {
  margin: 0 0 4px;
  font-size: 21px;
}

.sub,
.blurb,
.hint {
  color: var(--dim);
  font-size: 13px;
  margin: 0;
}

.hint {
  margin-top: 14px;
}

.head-actions {
  display: flex;
  align-items: flex-end;
  gap: 8px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.lbl {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--faint);
}

.sel {
  background: rgba(255, 255, 255, 0.05);
  color: var(--text);
  border: 1px solid var(--border-soft);
  border-radius: 7px;
  padding: 6px 9px;
  font-size: 13px;
  font-family: inherit;
}

.sel:hover {
  border-color: var(--purple);
}

.model {
  min-width: 260px;
}

.think {
  min-width: 96px;
}

.btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: rgba(255, 255, 255, 0.05);
  color: var(--text);
  border: 1px solid var(--border-soft);
  border-radius: 7px;
  padding: 6px 11px;
  font-size: 13px;
  cursor: pointer;
}

.btn:hover:not(:disabled) {
  border-color: var(--purple);
}

.btn:disabled {
  opacity: 0.4;
  cursor: default;
}

.save {
  min-width: 84px;
  justify-content: center;
}

.defaults {
  display: flex;
  align-items: center;
  gap: 9px;
  flex-wrap: wrap;
  margin: 18px 0 6px;
  padding: 10px 14px;
  border: 1px solid var(--border-soft);
  border-radius: 9px;
  font-size: 13px;
}

.cat {
  margin-top: 26px;
}

.cat h3 {
  margin: 0 0 2px;
  font-size: 15px;
  color: var(--text);
}

.row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  flex-wrap: wrap;
  padding: 11px 2px;
  border-bottom: 1px solid var(--border-soft);
}

.who {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 260px;
  flex: 1;
}

.swatch {
  width: 9px;
  height: 26px;
  border-radius: 3px;
  flex: none;
}

.who-text {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.name {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  font-size: 13.5px;
}

.ro {
  color: var(--faint);
}

.purpose {
  color: var(--dim);
  font-size: 11.5px;
}

.controls {
  display: flex;
  align-items: center;
  gap: 8px;
}

.ok {
  color: var(--green);
  font-size: 12px;
  min-width: 60px;
}

.inherited {
  color: var(--faint);
  font-size: 11px;
  min-width: 60px;
}

.err {
  margin: 14px 0;
  padding: 9px 13px;
  border: 1px solid var(--red);
  border-radius: 8px;
  color: var(--red);
  font-size: 13px;
}

code {
  font-size: 12px;
  color: var(--cyan);
}
</style>
