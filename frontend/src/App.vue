<script setup>
import { computed, onMounted, ref } from 'vue'
import LoginView from './components/LoginView.vue'
import NavRail from './components/NavRail.vue'
import SessionList from './components/SessionList.vue'
import CharacterGallery from './components/CharacterGallery.vue'
import ChatWindow from './components/ChatWindow.vue'
import SessionPanel from './components/SessionPanel.vue'
import SettingsDrawer from './components/SettingsDrawer.vue'
import TracePanel from './components/TracePanel.vue'
import UserCard from './components/UserCard.vue'
import { auth, logout } from './store/auth'
import { api } from './api'
import { initTheme } from './store/theme'

initTheme()

const authed = computed(() => !!auth.token)
const view = ref('chat') // chat | characters
const sessions = ref([])
const activeId = ref('')
const detail = ref(null)
const creating = ref('')
const userCardOpen = ref(false)
const settingsOpen = ref(false)
const sessionPanelOpen = ref(false)
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
    if (activeId.value && !sessions.value.some((s) => s.session_id === activeId.value)) {
      activeId.value = ''
      detail.value = null
    }
    if (view.value === 'chat' && !activeId.value && sessions.value.length) {
      await openSession(sessions.value[0].session_id)
    }
  } catch (e) {
    if (e.status === 401) doLogout()
    else error.value = e.message
  }
}

async function openSessionPanel() {
  if (!activeId.value) return
  try {
    detail.value = await api.getSession(activeId.value)
  } catch {
    /* 打不开就用缓存 */
  }
  sessionPanelOpen.value = true
}

async function onCharacterSynced() {
  if (!activeId.value) return
  try {
    detail.value = await api.getSession(activeId.value)
    chatKey.value += 1
  } catch (e) {
    error.value = e.message || '刷新会话失败'
  }
}

async function openSession(sessionId) {
  if (!sessionId) return
  error.value = ''
  view.value = 'chat'
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
    detail.value = created
    activeId.value = created.session_id
    view.value = 'chat'
    chatKey.value += 1
    await refreshSessions()
  } catch (e) {
    error.value = e.message || '创建会话失败'
  }
}

async function startCharacter(preset) {
  if (creating.value) return
  creating.value = preset.id
  error.value = ''
  try {
    const created = await api.createSession()
    await api.updatePersona(created.session_id, preset.persona)
    detail.value = await api.getSession(created.session_id)
    activeId.value = created.session_id
    view.value = 'chat'
    chatKey.value += 1
    await refreshSessions()
  } catch (e) {
    error.value = e.message || '创建角色失败'
  } finally {
    creating.value = ''
  }
}

async function startServerCharacter(char) {
  if (creating.value) return
  creating.value = 'c' + char.character_id
  error.value = ''
  try {
    const res = await api.createCharacterSession(char.character_id)
    await refreshSessions()
    await openSession(res.session_id)
  } catch (e) {
    error.value = e.message || '开始聊天失败'
  } finally {
    creating.value = ''
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

async function doLogout() {
  await logout()
  sessions.value = []
  activeId.value = ''
  detail.value = null
  userCardOpen.value = false
  settingsOpen.value = false
  sessionPanelOpen.value = false
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

  <div v-else class="shell" :class="{ 'has-chat': !!activeId, 'is-gallery': view === 'characters' }">
    <NavRail
      :active="view"
      :user="auth.user"
      @view="view = $event"
      @settings="settingsOpen = true"
      @user="userCardOpen = true"
    />

    <aside class="middle">
      <SessionList
        :sessions="sessions"
        :active-id="activeId"
        @select="openSession"
        @new="newSession"
      />
    </aside>

    <main class="main">
      <p v-if="error" class="error">
        <span>{{ error }}</span>
        <button class="icon-btn" aria-label="关闭" @click="error = ''">✕</button>
      </p>

      <CharacterGallery
        v-if="view === 'characters'"
        :creating="creating"
        @start="startServerCharacter"
        @start-preset="startCharacter"
      />

      <ChatWindow
        v-else-if="activeId"
        :key="chatKey + activeId"
        :session-id="activeId"
        :detail="detail"
        :name="activeSession?.name || detail?.persona?.name || '小柔'"
        :avatar="activeSession?.avatar || detail?.persona?.avatar || ''"
        @session-settings="openSessionPanel"
        @sent="onSent"
        @trace="openTrace"
        @back="activeId = ''"
      />

      <section v-else class="empty">
        <p class="empty__line serif">还没有打开任何对话</p>
        <p class="empty__note">
          左边选一个人接着说，或者点「人物」，从几位预设角色里挑一个开始。
        </p>
        <div class="empty__actions">
          <button class="btn btn--primary" @click="newSession">开一段新对话</button>
          <button class="btn btn--quiet" @click="view = 'characters'">去看看人物</button>
        </div>
      </section>
    </main>

    <SessionPanel
      v-if="sessionPanelOpen && activeId"
      :session-id="activeId"
      :detail="detail"
      @close="sessionPanelOpen = false"
      @persona="onPersonaChanged"
      @reset="onReset"
      @synced="onCharacterSynced"
    />

    <SettingsDrawer v-if="settingsOpen" @close="settingsOpen = false" />

    <TracePanel
      v-if="traceOpen"
      :events="traceEvents"
      :loading="traceLoading"
      :error="traceError"
      @close="traceOpen = false"
    />

    <UserCard v-if="userCardOpen" :user="auth.user" @close="userCardOpen = false" @logout="doLogout" />
  </div>
</template>

<style scoped>
.shell {
  display: grid;
  grid-template-columns: var(--rail-w-sm) 292px minmax(0, 1fr);
  height: 100%;
  background: var(--bg);
}
.middle {
  min-width: 0;
  border-right: 1px solid var(--line);
  overflow: hidden;
}
.main {
  position: relative;
  display: flex;
  flex-direction: column;
  min-width: 0;
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
  max-width: 34em;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 12px;
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
.empty__actions {
  display: flex;
  gap: 10px;
  margin-top: 6px;
  flex-wrap: wrap;
}

/* 人物页：隐藏中间会话列，让卡片有整块空间 */
.shell.is-gallery {
  grid-template-columns: var(--rail-w-sm) minmax(0, 1fr);
}
.shell.is-gallery .middle {
  display: none;
}

@media (max-width: 900px) {
  .shell,
  .shell.is-gallery {
    grid-template-columns: var(--rail-w-sm) minmax(0, 1fr);
  }
  .middle {
    display: none;
  }
  /* 手机：没选会话时显示列表，选了会话显示聊天，靠返回键回来 */
  .shell:not(.has-chat) .middle {
    display: block;
  }
  .shell:not(.has-chat) .main {
    display: none;
  }
}
</style>
