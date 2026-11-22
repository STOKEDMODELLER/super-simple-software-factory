<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, shallowRef } from 'vue'
import type { SessionSummary } from '../lib/types'
import { fetchSessions } from '../lib/api'
import { ts } from '../lib/format'
import SessionCard from './SessionCard.vue'

const sessions = shallowRef<SessionSummary[]>([])
const apiError = ref<string | null>(null)
const loaded = ref(false)
const nowMs = ref(Date.now())

let timer: ReturnType<typeof setInterval> | undefined
let inflight = false

async function tick() {
  if (inflight) return
  inflight = true
  try {
    sessions.value = await fetchSessions()
    nowMs.value = Date.now()
    apiError.value = null
    loaded.value = true
  } catch (err) {
    apiError.value = err instanceof Error ? err.message : String(err)
  } finally {
    inflight = false
  }
}

onMounted(() => {
  void tick()
  timer = setInterval(() => void tick(), 500)
})

onUnmounted(() => clearInterval(timer))

/** Optimistic removal; an empty id means the write failed, so re-sync instead. */
function onArchived(adwId: string) {
  if (!adwId) {
    void tick()
    return
  }
  sessions.value = sessions.value.filter((s) => s.adw_id !== adwId)
}

const ordered = computed(() =>
  sessions.value.toSorted((a, b) => (ts(b.started_at) || 0) - (ts(a.started_at) || 0)),
)
</script>

<template>
  <div class="sessions">
    <div v-if="apiError" class="error-bar">api unreachable — retrying {{ apiError }}</div>

    <div v-if="ordered.length" class="list-head"><h1>Sessions</h1><span>{{ ordered.length }} runs</span></div>

    <div v-if="ordered.length" class="ledger">
      <div class="ledger-head" aria-hidden="true">
        <span>Run</span><span>Task</span><span>Timeline</span><span>Cost</span><span>Duration</span><span>Tokens</span>
      </div>
      <div class="cards">
      <SessionCard
        v-for="s in ordered"
        :key="s.adw_id"
        :session="s"
        :now-ms="nowMs"
        @archived="onArchived"
      />
      </div>
    </div>
    <div v-else-if="loaded" class="empty-state">no sessions yet — run an ADW to see it here</div>
    <div v-else-if="!apiError" class="empty-state">loading sessions…</div>
  </div>
</template>

<style scoped>
.sessions {
  display: flex;
  flex-direction: column;
}

.list-head { padding: 26px 28px 8px; display: flex; flex-direction: column; gap: 0; }
.list-head h1 { margin: 0; font-size: 48px; letter-spacing: -.04em; line-height: 1.1; }
.list-head span { color: var(--dim); font-size: 19px; }

.ledger { margin: 12px 28px 40px; }
.ledger-head { display: grid; grid-template-columns: minmax(190px, 1fr) minmax(220px, 1.2fr) minmax(300px, 1.5fr) 110px 130px 120px; gap: 18px; padding: 0 22px 8px; color: var(--faint); font: 11px var(--mono); letter-spacing: .08em; text-transform: uppercase; }
.cards { display: flex; flex-direction: column; }
@media (max-width: 1220px) { .list-head h1 { font-size: 38px; } .ledger { margin-left: 16px; margin-right: 16px; } .ledger-head { display: none; } }
</style>
