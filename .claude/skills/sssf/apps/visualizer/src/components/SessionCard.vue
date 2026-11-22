<script setup lang="ts">
import { computed, onMounted, onUnmounted, shallowRef, watch } from 'vue'
import type { EventRow, SessionSummary } from '../lib/types'
import { archiveSession, fetchEvents } from '../lib/api'
import { axisTicks, fmtDate, fmtOffset, ts } from '../lib/format'
import { agentColor, dotColor, eventLabel } from '../lib/events'
import { hrefFor } from '../lib/router'
import StatusChip from './StatusChip.vue'
import StatChip from './StatChip.vue'
import PhaseDots from './PhaseDots.vue'

const props = defineProps<{ session: SessionSummary; nowMs: number }>()
const emit = defineEmits<{ archived: [adwId: string] }>()

// The card is an <a>; the button lives inside it, so the click must not
// navigate. Told the parent optimistically — the poll would take up to half a
// second to drop the card, and a triage click should feel instant.
async function archive(event: MouseEvent) {
  event.preventDefault()
  event.stopPropagation()
  emit('archived', props.session.adw_id)
  try {
    await archiveSession(props.session.adw_id)
  } catch {
    emit('archived', '')   // signals the parent to re-sync from the server
  }
}

// Each card tails its own event stream: one full fetch on mount, then the
// same rowid-cursor poll as the trace view — but only while the run is live.
const events = shallowRef<EventRow[]>([])
let cursor = 0
let inflight = false
let timer: ReturnType<typeof setInterval> | undefined

function stopPolling() {
  clearInterval(timer)
  timer = undefined
}

async function pull() {
  if (inflight) return
  inflight = true
  try {
    const fresh: EventRow[] = []
    let page
    do {
      // Cursor pagination is inherently sequential: each request needs the previous cursor.
      // oxlint-disable-next-line no-await-in-loop
      page = await fetchEvents(props.session.adw_id, cursor, 1000)
      cursor = Math.max(cursor, page.cursor)
      fresh.push(...page.events)
    } while (page.has_more)
    if (fresh.length) events.value = [...events.value, ...fresh]
    if (props.session.status !== 'running') stopPolling()
  } catch {
    /* the list view surfaces api errors; a card just retries next poll */
  } finally {
    inflight = false
  }
}

onMounted(() => {
  void pull()
  if (props.session.status === 'running') timer = setInterval(() => void pull(), 500)
})

onUnmounted(stopPolling)

watch(
  () => props.session.status,
  (status) => {
    if (status === 'running' && !timer) timer = setInterval(() => void pull(), 500)
    // On the transition out of running, one last pull drains the tail and stops the timer.
    else if (status !== 'running') void pull()
  },
)

const running = computed(() => props.session.status === 'running')

const range = computed(() => {
  const s = props.session
  let t0 = ts(s.started_at)
  if (!Number.isFinite(t0)) {
    t0 = Math.min(...events.value.map((e) => ts(e.started_at)).filter(Number.isFinite))
  }
  if (!Number.isFinite(t0)) t0 = props.nowMs
  let t1 = running.value ? props.nowMs : ts(s.ended_at)
  if (!Number.isFinite(t1)) {
    t1 = Math.max(...events.value.map((e) => ts(e.started_at)).filter(Number.isFinite))
  }
  if (!Number.isFinite(t1)) t1 = t0 + 1000
  return { t0, span: Math.max(t1 - t0, 1000) }
})

const ticks = computed(() => axisTicks(range.value.span, 5))

interface TimelineDot {
  id: string
  xPct: number
  color: string
  title: string
  latest: boolean
}

interface TimelineRow {
  owner: string
  color: string
  title: string
  dots: TimelineDot[]
}

// Per-agent rows: events attribute to an agent through their phase's owner.
const rows = computed<TimelineRow[]>(() => {
  const owners: string[] = []
  const ownerByPhase = new Map<string, string>()
  for (const p of props.session.phases ?? []) {
    if (p.kind !== 'agent' || !p.owner) continue
    ownerByPhase.set(p.phase_id, p.owner)
    if (!owners.includes(p.owner)) owners.push(p.owner)
  }
  if (!owners.length) return []

  const { t0, span } = range.value
  const byOwner = new Map<string, TimelineDot[]>(owners.map((o) => [o, []]))
  let latest: TimelineDot | null = null
  let latestT = -Infinity

  for (const e of events.value) {
    const owner = e.phase_id ? ownerByPhase.get(e.phase_id) : undefined
    const color = dotColor(e.type)
    if (!owner || !color) continue
    const t = ts(e.started_at)
    if (!Number.isFinite(t)) continue
    const dot: TimelineDot = {
      id: e.event_id,
      xPct: Math.min(Math.max(((t - t0) / span) * 100, 0), 100),
      color,
      title: `${e.type} ${eventLabel(e)} at ${fmtOffset(t - t0)}`,
      latest: false,
    }
    byOwner.get(owner)?.push(dot)
    if (t >= latestT) {
      latestT = t
      latest = dot
    }
  }
  if (running.value && latest) latest.latest = true

  // /api/sessions embeds agents so the labels can use config colors with no
  // extra request; historical sessions return color null → fallback palette.
  return owners.map((owner, i) => {
    const info = (props.session.agents ?? []).find((a) => a.agent === owner)
    return {
      owner,
      color: agentColor(info?.color, null, i),
      title: info?.model ? `${owner} ${info.model}` : owner,
      dots: byOwner.get(owner) ?? [],
    }
  })
})

const durationMs = computed(() => {
  const s = props.session
  const start = ts(s.started_at)
  if (!Number.isFinite(start)) return NaN
  const end = running.value ? props.nowMs : ts(s.ended_at)
  return (Number.isFinite(end) ? end : props.nowMs) - start
})

// Cards are a fixed size, so the timeline region fits exactly MAX_VISIBLE_ROWS
// row slots. A roster that overflows spends one slot on the "+N more" line and
// shows MIN_VISIBLE_ROWS agents in the rest — never fewer than three, so a
// five-agent chain still reads as a chain rather than as a pair and a count.
const MAX_VISIBLE_ROWS = 4
const MIN_VISIBLE_ROWS = 3

const overflowing = computed(() => rows.value.length > MAX_VISIBLE_ROWS)

const visibleRows = computed(() =>
  overflowing.value ? rows.value.slice(0, MIN_VISIBLE_ROWS) : rows.value,
)

const hiddenRowCount = computed(() =>
  overflowing.value ? rows.value.length - MIN_VISIBLE_ROWS : 0,
)
</script>

<template>
  <a class="card" :class="session.status" :href="hrefFor(session.adw_id)">
    <button
      class="card-archive"
      type="button"
      title="Archive — remove this run from review"
      aria-label="Archive run"
      @click="archive"
    >
      ×
    </button>
    <div class="card-run">
      <span class="card-id">{{ session.adw_id }}</span>
      <span class="card-adw" :title="session.adw_name ?? ''">{{ session.adw_name ?? '—' }}</span>
    </div>
    <span class="card-req" :title="session.request ?? ''">{{ session.request }}</span>

    <div v-if="rows.length" class="tl">
      <div class="tl-axis">
        <span class="tl-gutter" />
        <span class="tl-scale">
          <span
            v-for="(t, i) in ticks"
            :key="i"
            class="tl-tick"
            :class="{ edge: t.pct === 0 }"
            :style="{ left: `${t.pct}%` }"
            >{{ t.label }}</span
          >
        </span>
      </div>
      <div v-for="row in visibleRows" :key="row.owner" class="tl-row">
        <span class="tl-agent" :style="{ color: `color-mix(in srgb, ${row.color} 55%, #161819)` }" :title="row.title">{{
          row.owner
        }}</span>
        <span class="tl-track">
          <span
            v-for="dot in row.dots"
            :key="dot.id"
            class="tl-dot"
            :class="{ latest: dot.latest }"
            :style="{ left: `${dot.xPct}%`, background: dot.color }"
            :title="dot.title"
          />
        </span>
      </div>
      <div v-if="hiddenRowCount" class="tl-more dim">+{{ hiddenRowCount }} more agents</div>
    </div>
    <div v-else class="tl tl-empty faint">no agent activity yet</div>

    <div class="card-foot">
      <span class="foot-status">
        <StatusChip :status="session.status ?? 'fail'" />
        <PhaseDots :phases="session.phases ?? []" />
      </span>
      <span class="dim">{{ fmtDate(session.started_at) }}</span>
    </div>
    <div class="card-stat cost-stat"><span>Cost</span><StatChip kind="cost" :value="session.total_cost" /></div>
    <div class="card-stat duration-stat"><span>Duration</span><StatChip kind="runtime" :value="durationMs" /></div>
    <div class="card-stat tokens-stat"><span>Tokens</span><StatChip kind="tokens" :value="session.total_tokens" /></div>
  </a>
</template>

<style scoped>
.card { min-height: 150px; display: grid; grid-template-columns: minmax(190px, 1fr) minmax(220px, 1.2fr) minmax(300px, 1.5fr) 110px 130px 120px; grid-template-rows: auto 1fr; gap: 8px 18px; padding: 18px 22px; position: relative; border: 0; border-top: 1px solid var(--border); background: transparent; color: var(--text); cursor: pointer; overflow: hidden; transition: background .15s ease; }
.card:hover { background: #e9eef8; }
.card-archive { position: absolute; top: 8px; right: 4px; width: 26px; height: 26px; border: 0; background: transparent; color: var(--dim); opacity: 0; cursor: pointer; font-size: 20px; }
.card:hover .card-archive, .card-archive:focus-visible { opacity: 1; }
.card-archive:hover { color: var(--red); }
.card-run { grid-column: 1; display: flex; flex-direction: column; min-width: 0; }
.card-id { font: 700 18px var(--mono); color: var(--text); overflow: hidden; text-overflow: ellipsis; }
.card-adw { color: var(--cyan); font: 700 14px var(--mono); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.card-req { grid-column: 2; grid-row: 1 / span 2; overflow: hidden; line-height: 1.4; }
.tl { grid-column: 3; grid-row: 1 / span 2; min-height: 68px; display: flex; flex-direction: column; overflow: hidden; }
.tl-axis { display: flex; align-items: flex-end; height: 26px; margin-bottom: 4px; }
.tl-gutter, .tl-agent { flex: none; width: 82px; padding-right: 7px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tl-scale { position: relative; flex: 1; height: 100%; border-bottom: 1px solid var(--border); }
.tl-tick { position: absolute; bottom: 4px; transform: translateX(-50%); color: var(--faint); font: 11px var(--mono); white-space: nowrap; }
.tl-tick.edge { transform: none; }
.tl-row { display: flex; align-items: center; height: 30px; }
.tl-agent { color: var(--dim); font-size: 13px; }
.tl-track { position: relative; flex: 1; height: 100%; border-bottom: 1px solid var(--border-soft); }
.tl-dot { position: absolute; top: 50%; width: 8px; height: 8px; border-radius: 50%; transform: translate(-50%, -50%); }
.tl-dot.latest { width: 12px; height: 12px; animation: pulse 1.4s ease-in-out infinite; }
.tl-more { padding-left: 82px; color: var(--dim); font-size: 12px; }
.tl-empty { display: flex; align-items: center; justify-content: center; color: var(--faint); }
.card-foot { grid-column: 1; grid-row: 2; align-self: end; display: flex; align-items: center; gap: 10px; font-size: 12px; }
.foot-status { display: inline-flex; align-items: center; gap: 8px; }
.card-stat { grid-row: 1 / span 2; align-self: center; display: flex; flex-direction: column; gap: 3px; padding-left: 14px; border-left: 1px solid var(--border); color: var(--faint); font: 11px var(--mono); text-transform: uppercase; letter-spacing: .06em; }
.card-stat :deep(.stat) { padding: 0; border: 0; background: transparent; font-size: 15px; }
.card-stat :deep(.stat-icon) { color: var(--dim); }
.cost-stat { grid-column: 4; } .duration-stat { grid-column: 5; } .tokens-stat { grid-column: 6; }
@media (max-width: 1220px) { .card { display: flex; flex-wrap: wrap; gap: 8px 14px; padding: 14px 8px; } .card-run { width: 100%; } .card-req, .tl { width: 100%; } .card-req { order: 2; } .tl { order: 3; } .card-foot { order: 4; } .card-stat { order: 5; flex: 1 1 28%; border-top: 1px solid var(--border); padding-top: 7px; margin: 0; } }
</style>
