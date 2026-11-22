<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { createChat, continueChat, fetchChats, fetchChatRepos, type ChatConversation, type ChatRepo } from '../lib/api'

const repos = ref<ChatRepo[]>([])
const repoId = ref('')
const conversation = ref<ChatConversation | null>(null)
const threads = ref<ChatConversation[]>([])
watch(repoId, async id => {
  conversation.value = null
  threads.value = []
  text.value = ''
  error.value = ''
  try {
    const items = await fetchChats(id)
    if (repoId.value === id) threads.value = items.reverse()
  } catch (e) { if (repoId.value === id) error.value = (e as Error).message }
})
const text = ref('')
const workflow = ref<'scout' | 'build'>('scout')
const busy = ref(false)
const error = ref('')
const selectedRepo = computed(() => repos.value.find((repo) => repo.id === repoId.value))

onMounted(async () => { try { repos.value = await fetchChatRepos(); repoId.value = repos.value[0]?.id ?? '' } catch (e) { error.value = (e as Error).message } })
async function submit() {
  if (!text.value.trim() || !repoId.value || busy.value) return
  busy.value = true; error.value = ''
  try {
    conversation.value = conversation.value
      ? await continueChat(conversation.value.id, text.value, workflow.value)
      : await createChat(repoId.value, text.value, workflow.value)
    threads.value = [conversation.value, ...threads.value.filter(c => c.id !== conversation.value?.id)]
    text.value = ''
  } catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
function newConversation() { conversation.value = null }
</script>

<template>
  <section class="chat-page">
    <div class="chat-heading"><div><h1>Chat</h1><p class="subhead">Run a scout or build against a local SSSF repository.</p></div><button class="secondary" :disabled="busy" @click="newConversation">New conversation</button></div>
    <div class="chat-toolbar">
      <label>Repository <select v-model="repoId" :disabled="busy"><option v-for="repo in repos" :key="repo.id" :value="repo.id">{{ repo.name }} — {{ repo.path }}</option></select></label>
      <label v-if="threads.length">Conversation <select :value="conversation?.id ?? ''" :disabled="busy" @change="conversation = threads.find(c => c.id === ($event.target as HTMLSelectElement).value) ?? null"><option value="">New conversation</option><option v-for="thread in threads" :key="thread.id" :value="thread.id">{{ thread.messages[0]?.content.slice(0,60) }}</option></select></label>
      <label>Workflow <select v-model="workflow" :disabled="busy"><option value="scout">Scout (read-only)</option><option value="build">Build</option></select></label>
    </div>
    <div v-if="error" class="error-bar">{{ error }}</div>
    <div class="conversation" aria-live="polite">
      <div v-if="!conversation && !busy" class="chat-empty"><strong>What should we work on?</strong><span>{{ workflow === 'build' ? 'Describe the change you want to make.' : 'Ask a question about the selected repository.' }}</span></div>
      <article v-for="item in conversation?.messages" :key="item.id" class="message" :class="item.role"><div class="message-label">{{ item.role === 'user' ? 'You' : 'SSSF' }} <span v-if="item.workflow">· {{ item.workflow }}</span></div><pre>{{ item.content }}</pre></article>
      <article v-if="busy" class="message user"><div class="message-label">You</div><pre>{{ text }}</pre><span role="status">Running {{ workflow }}…</span></article>
    </div>
    <form class="composer" @submit.prevent="submit"><textarea v-model="text" :disabled="busy || !repoId" rows="4" placeholder="Ask the scout a question, or describe a build…" @keydown.meta.enter.prevent="submit" @keydown.ctrl.enter.prevent="submit" /><button class="primary" :disabled="busy || !text.trim() || !repoId">{{ busy ? 'Running…' : 'Run workflow' }}</button></form>
    <p v-if="selectedRepo" class="repo-note">Local only · {{ selectedRepo.path }} · Ctrl/⌘ + Enter to run</p>
  </section>
</template>

<style scoped>
.chat-page { max-width: 1040px; margin: 0 auto; padding: 40px 32px 64px; }
.chat-heading { display:flex; justify-content:space-between; align-items:end; gap:20px; margin-bottom:28px; } h1 { margin:0; font-size:38px; letter-spacing:-.04em; } .eyebrow { margin:0 0 4px; color:var(--blue); font:700 12px var(--mono); letter-spacing:.1em; } .subhead { margin:5px 0 0; color:var(--dim); }
.chat-toolbar { display:flex; gap:16px; padding:16px; background:var(--panel); border:1px solid var(--border-soft); border-radius:8px; } label { display:flex; flex-direction:column; gap:5px; color:var(--dim); font-size:13px; font-weight:700; flex:1; } select, textarea { border:1px solid var(--border); background:#fff; color:var(--text); border-radius:5px; padding:9px 10px; } select { font-size:15px; } .conversation { min-height:310px; padding:24px 4px; } .chat-empty { display:flex; flex-direction:column; align-items:center; justify-content:center; min-height:260px; color:var(--dim); gap:5px; } .chat-empty strong { color:var(--text); font-size:20px; } code { font-family:var(--mono); } .message { max-width:88%; margin:0 0 18px; } .message.user { margin-left:auto; } .message-label { color:var(--faint); font-size:12px; font-weight:bold; margin-bottom:5px; } .message pre { white-space:pre-wrap; } .message.user pre { background:#e8f0ff; border-color:#b9cdf6; } .composer { display:flex; gap:12px; align-items:end; } textarea { resize:vertical; flex:1; font-size:16px; line-height:1.45; } button { border:0; border-radius:5px; cursor:pointer; padding:10px 15px; font-weight:700; } button:disabled { opacity:.5; cursor:not-allowed; } .primary { background:var(--blue); color:white; } .secondary { background:transparent; border:1px solid var(--border); color:var(--text); } .repo-note { color:var(--faint); font:12px var(--mono); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; } @media(max-width:700px) { .chat-page { padding:24px 16px; } .chat-heading,.chat-toolbar,.composer { flex-direction:column; align-items:stretch; } .secondary { align-self:flex-start; } .message { max-width:100%; } }
</style>
