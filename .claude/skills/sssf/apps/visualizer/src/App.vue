<script setup lang="ts">
import { Home, Layers3, Settings2, SlidersHorizontal } from 'lucide-vue-next'
import { useRoute, hrefFor, phaseCrumb, settingsHref } from './lib/router'
import SessionsList from './components/SessionsList.vue'
import SessionTrace from './components/SessionTrace.vue'
import SettingsView from './components/SettingsView.vue'

const route = useRoute()
</script>

<template>
  <div class="app">
    <aside class="rail" aria-label="Primary navigation">
      <a :href="hrefFor()" class="rail-mark" aria-label="SSSF home">SS<br />SF</a>
      <nav class="rail-nav">
        <a :href="hrefFor()" :class="{ active: !route.adwId && !route.settings }" aria-label="Sessions"><Home :size="22" /></a>
        <a v-if="route.adwId" :href="hrefFor(route.adwId)" :class="{ active: !!route.adwId }" aria-label="Trace"><Layers3 :size="22" /></a>
        <a :href="settingsHref" :class="{ active: route.settings }" aria-label="Settings"><SlidersHorizontal :size="22" /></a>
      </nav>
    </aside>
    <div class="workspace">
    <header class="topbar">
      <nav class="crumbs">
        <!-- Inline copy of public/logo.svg (the favicon) so the mark renders
             crisply with no fetch; keep the two in sync. -->
        <svg class="logo" viewBox="0 0 32 32" aria-hidden="true">
          <rect x="4" y="6" width="17" height="5" rx="2.5" fill="#e8b64a" />
          <rect x="8" y="13.5" width="20" height="5" rx="2.5" fill="#c89bff" />
          <rect x="4" y="21" width="13" height="5" rx="2.5" fill="#5ad2dd" />
        </svg>
        <span class="brand">Super Simple Software Factory</span>
        <span class="sep">›</span>
        <a :href="hrefFor()" :class="{ current: !route.adwId && !route.settings }">sessions</a>
        <template v-if="route.settings">
          <span class="sep">›</span>
          <span class="current">settings</span>
        </template>
        <template v-if="route.adwId">
          <span class="sep">›</span>
          <a :href="hrefFor(route.adwId)" :class="{ current: !route.phaseId }">{{
            route.adwId
          }}</a>
        </template>
        <template v-if="route.adwId && route.phaseId">
          <span class="sep">›</span>
          <span class="current">{{ phaseCrumb ?? route.phaseId }}</span>
        </template>
      </nav>
      <div class="top-right">
        <a
          :href="settingsHref"
          class="settings-link"
          :class="{ on: route.settings }"
          title="Model settings — which model runs each task category"
        >
          <Settings2 :size="17" />
        </a>
        <span class="live-hint"><span class="live-dot" /> live</span>
      </div>
    </header>
    <main>
      <SettingsView v-if="route.settings" />
      <SessionsList v-else-if="!route.adwId" />
      <SessionTrace v-else :key="route.adwId" :adw-id="route.adwId" :phase-id="route.phaseId" />
    </main>
    </div>
  </div>
</template>

<style scoped>
.rail { position: fixed; inset: 0 auto 0 0; width: 78px; background: #27292a; color: #fff; display: flex; flex-direction: column; align-items: center; z-index: 20; }
.rail-mark { color: #fff; font-weight: 700; font-size: 20px; line-height: .85; letter-spacing: -.08em; padding: 27px 0 25px; border-bottom: 1px solid #5c5e5e; width: 58px; text-align: center; }
.rail-nav { display: flex; flex-direction: column; gap: 8px; padding-top: 25px; width: 100%; align-items: center; }
.rail-nav a { width: 52px; height: 52px; display: grid; place-items: center; color: #f0f1ef; border-radius: 9px; transition: background .15s ease, color .15s ease; }
.rail-nav a:hover { background: #3c3f40; } .rail-nav a.active { background: #1266df; color: #fff; }
.workspace { margin-left: 78px; min-height: 100vh; }
.topbar { display: flex; align-items: center; justify-content: space-between; padding: 17px 32px; background: rgba(245, 244, 239, .96); border-bottom: 1px solid var(--border); position: sticky; top: 0; z-index: 10; }

.crumbs {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 17px;
  min-width: 0;
}

.logo { width: 27px; height: 27px; flex: none; filter: none; }

.brand { color: var(--text); font-weight: 700; letter-spacing: -.02em; white-space: nowrap; }

.sep { color: var(--faint); font-size: 23px; }

.crumbs a {
  color: var(--dim);
}

.crumbs a:hover {
  color: var(--text);
}

.crumbs .current {
  color: var(--text);
}

.top-right {
  display: flex;
  align-items: center;
  gap: 16px;
}

.settings-link {
  display: inline-flex;
  align-items: center;
  color: var(--dim);
  padding: 5px;
  border-radius: 7px;
  line-height: 0;
}

.settings-link:hover,
.settings-link.on {
  color: var(--text);
  background: rgba(255, 255, 255, 0.06);
}

.live-hint {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: var(--dim);
  font-size: 16px;
  white-space: nowrap;
}

.live-dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: var(--green);
  box-shadow: 0 0 10px rgba(74, 222, 128, 0.7);
  animation: pulse 1.6s ease-in-out infinite;
}
</style>
