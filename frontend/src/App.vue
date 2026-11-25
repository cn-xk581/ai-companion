<script setup>
import { computed, onMounted, ref } from 'vue'
import LoginView from './components/LoginView.vue'
import SessionList from './components/SessionList.vue'
import ChatWindow from './components/ChatWindow.vue'
import SettingsDrawer from './components/SettingsDrawer.vue'
import TracePanel from './components/TracePanel.vue'
import { auth, logout } from './store/auth'
import { api } from './api'
import { initTheme } from './store/theme'

initTheme()

const authed = computed(() => !!auth.token)
const sessions = ref([])
const activeId = ref('')
const detail = ref(null)
const settingsOpen = ref(false)
const traceOpen = ref(false)
const traceEvents = ref([])
const traceLoading = ref(false)
const traceError = ref('')
const error = ref('')
const chatKey = ref(0)

const activeSession = computed(
  () => sessions.value.find((s) => s.session_id === activeId.value) || null
)

async function refreshSessions() {
  try {
    const res = await api.listSessions()
    sessions.value = res.sessions || []
    if (!activeId.value && sessions.value.length) await openSession(sessions.value[0].session_id)
    if (activeId.value && !sessions.value.some((s) => s.session_id === activeId.value)) {
      activeId.value = ''
      detail.value = null
    }
  } catch (e) {
    if (e.status === 401) doLogout()
    else error.value = e.message
  }
}

async function openSession(sessionId) {
  if (!sessionId) return
  if (sessionId === activeId.value) return
  error.value = ''
  try {
    detail.value = await api.getSession(sessionId)
    activeId.value = sessionId
    chatKey.value += 1
  } catch (e) {
    error.value = e.message || '打开会话失败'
  }
}

async function newSession() {
  error.value = ''
  try {
    const created = await api.createSession()
    await refreshSessions()
    detail.value = created
    activeId.value = created.session_id
    chatKey.value += 1
  } catch (e) {
    error.value = e.message || '创建会话失败'
  }
}

async function onSent(payload) {
  if (payload?.stage) {
    detail.value = {
      ...(detail.value || {}),
      stage: payload.stage,
      stage_label: payload.stage_label,
      score: payload.score,
      mood: payload.mood,
      mood_label: payload.mood_label,
    }
  }
  await refreshSessions()
}

async function onPersonaChanged(persona) {
  if (detail.value) detail.value = { ...detail.value, persona }
  await refreshSessions()
}

async function onReset() {
  await refreshSessions()
  if (activeId.value) {
    detail.value = await api.getSession(activeId.value)
    chatKey.value += 1
  }
}

async function openTrace(turnId) {
  if (!turnId) return
  traceOpen.value = true
  traceLoading.value = true
  traceError.value = ''
  try {
    const res = await api.traceTurn(turnId)
    traceEvents.value = res.events || []
  } catch (e) {
    traceError.value = e.message || '读取追踪失败'
    traceEvents.value = []
  } finally {
    traceLoading.value = false
  }
}

function doLogout() {
  logout()
  sessions.value = []
  activeId.value = ''
  detail.value = null
  settingsOpen.value = false
  traceOpen.value = false
}

async function onAuthed() {
  await refreshSessions()
}

onMounted(() => {
  if (authed.value) refreshSessions()
})
</script>

<template>
  <LoginView v-if="!authed" @authed="onAuthed" />

  <div v-else class="shell" :class="{ 'has-open': !!activeId }">
    <aside class="rail">
      <SessionList
        :sessions="sessions"
        :active-id="activeId"
        :user="auth.user?.username || '我'"
        @select="openSession"
        @new="newSession"
        @settings="settingsOpen = true"
        @logout="doLogout"
      />
    </aside>

    <main class="stage">
      <p v-if="error" class="error">
        <span>{{ error }}</span>
        <button class="icon-btn" aria-label="关闭" @click="error = ''">✕</button>
      </p>

      <ChatWindow
        v-if="activeId"
        :key="chatKey + activeId"
        :session-id="activeId"
        :detail="detail"
        :name="activeSession?.name || detail?.persona?.name || '小柔'"
        :avatar="activeSession?.avatar || detail?.persona?.avatar || ''"
        @sent="onSent"
        @trace="openTrace"
        @back="activeId = ''"
      />

      <section v-else class="empty">
        <p class="empty__line serif">还没有在聊的人</p>
        <p class="empty__note">
          开一段对话吧。她记得你说过的事，关系也会从陌生一点点往前走；
          想改她的性格、说话方式或者禁区，左下角「设置」里都能改。
        </p>
        <button class="btn btn--primary" @click="newSession">开始第一段对话</button>
      </section>
    </main>

    <SettingsDrawer
      v-if="settingsOpen"
      :session-id="activeId"
      :detail="detail"
      @close="settingsOpen = false"
      @persona="onPersonaChanged"
      @reset="onReset"
    />

    <TracePanel
      v-if="traceOpen"
      :events="traceEvents"
      :loading="traceLoading"
      :error="traceError"
      @close="traceOpen = false"
    />
  </div>
</template>

<style scoped>
.shell {
  display: flex;
  height: 100%;
  background: var(--bg);
}

.rail {
  width: var(--rail-w);
  flex-shrink: 0;
  background: var(--panel);
  border-right: 1px solid var(--line);
}

.stage {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 20px;
  font-size: var(--step-0);
  color: var(--bad);
  background: color-mix(in srgb, var(--bad) 10%, var(--panel));
  border-bottom: 1px solid var(--line);
}

.empty {
  margin: auto;
  max-width: 30em;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 14px;
  padding: 40px;
}
.empty__line {
  font-size: var(--step-3);
  color: var(--text);
}
.empty__note {
  font-size: var(--step-0);
  line-height: 1.85;
  color: var(--text-3);
}

@media (max-width: 900px) {
  .rail {
    width: 100%;
    border-right: 0;
  }
  .stage {
    display: none;
  }
  .shell.has-open .rail {
    display: none;
  }
  .shell.has-open .stage {
    display: flex;
  }
}
</style>
