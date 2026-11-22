import { ref } from 'vue'

// Hash routes: #/ → sessions · #/<adw_id> → waterfall · #/<adw_id>/<phase_id> → phase panel open
//              #/settings → roster settings (a reserved id, never an adw_id)
export interface Route {
  adwId: string | null
  phaseId: string | null
  settings: boolean
  chat: boolean
}

/** Reserved first segment. adw_ids are 8 hex chars, so this can never collide. */
const SETTINGS = 'settings'

function parse(): Route {
  const parts = window.location.hash
    .replace(/^#\/?/, '')
    .split('/')
    .filter(Boolean)
    .map(decodeURIComponent)
  if (parts[0] === SETTINGS) return { adwId: null, phaseId: null, settings: true, chat: false }
  if (parts[0] === 'chat') return { adwId: null, phaseId: null, settings: false, chat: true }
  return { adwId: parts[0] ?? null, phaseId: parts[1] ?? null, settings: false, chat: false }
}

const route = ref<Route>(parse())

window.addEventListener('hashchange', () => {
  route.value = parse()
})

export function useRoute() {
  return route
}

// Display name for the phase crumb — set by the trace view once phases load,
// since the phase_id in the URL is not the display name.
export const phaseCrumb = ref<string | null>(null)

export function hrefFor(adwId?: string | null, phaseId?: string | null): string {
  let h = '#/'
  if (adwId) h += encodeURIComponent(adwId)
  if (adwId && phaseId) h += `/${encodeURIComponent(phaseId)}`
  return h
}

export const settingsHref = `#/${SETTINGS}`
export const chatHref = '#/chat'

export function navigate(adwId?: string | null, phaseId?: string | null): void {
  window.location.hash = hrefFor(adwId, phaseId)
}
