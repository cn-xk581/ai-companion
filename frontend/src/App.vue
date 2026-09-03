<script setup>
import { computed, onMounted, ref } from 'vue'
import LoginView from './components/LoginView.vue'
import ChatList from './components/ChatList.vue'
import ChatWindow from './components/ChatWindow.vue'
import SettingsDrawer from './components/SettingsDrawer.vue'
import { auth, logout } from './store/auth'
import { api } from './api'
import { uuid } from './utils'

const authed = computed(() => !!auth.token)
const sessions = ref([])
const active = ref(null)
const showSettings = ref(false)

async function refreshSessions() {
  try {
    const res = await api.listSessions()
    sessions.value = res.sessions || []
  } catch (e) {
    if (e.status === 401) return doLogout()
    return
  }
  // 让 active 始终指向最新列表项，保证昵称/预览同步
  if (active.value) {
    const found = sessions.value.find((s) => s.session_id === active.value.session_id)
    if (found) active.value = found
  }
}

function selectSession(s) {
  active.value = s
  showSettings.value = false
}

function newChat() {
  active.value = {
    session_id: uuid(),
    name: '小柔',
    avatar: '',
    relationship_stage: 'stranger',
    relationship_score: 0,
    mood: 'neutral',
    mood_intensity: 0.5,
    last_message: '',
    last_time: '',
    pending: true,
  }
  showSettings.value = false
}

async function onSent(done) {
  if (active.value && active.value.session_id === done.session_id) {
    active.value.pending = false
  }
  await refreshSessions()
}

async function onChanged(payload) {
  if (payload.type === 'reset') {
    active.value = null
    await refreshSessions()
  } else if (payload.type === 'persona') {
    if (active.value && payload.persona?.name) {
      active.value.name = payload.persona.name
    }
    await refreshSessions()
  }
}

function doLogout() {
  logout()
  sessions.value = []
  active.value = null
  showSettings.value = false
}

onMounted(() => {
  if (authed.value) refreshSessions()
})
</script>

<template>
  <LoginView v-if="!authed" @authed="refreshSessions" />

  <div v-else class="shell">
    <aside class="sidebar">
      <ChatList
        :sessions="sessions"
        :active-id="active?.session_id || ''"
        @select="selectSession"
        @new-chat="newChat"
        @logout="doLogout"
      />
    </aside>

    <main class="main">
      <ChatWindow
        v-if="active"
        :session="active"
        @sent="onSent"
        @open-settings="showSettings = true"
      />
      <div v-else class="placeholder">
        <div class="ph-logo">柔</div>
        <p>选择一个会话，或点左上角「＋」开始新的聊天</p>
      </div>
    </main>

    <SettingsDrawer
      v-if="showSettings && active"
      :session="active"
      @close="showSettings = false"
      @changed="onChanged"
    />
  </div>
</template>

<style scoped>
.shell {
  display: flex;
  height: 100%;
}
.sidebar {
  width: 280px;
  flex-shrink: 0;
  border-right: 1px solid var(--wx-border);
}
.main {
  flex: 1;
  min-width: 0;
}
.placeholder {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16px;
  color: #b2b2b2;
  background: var(--wx-bg);
}
.ph-logo {
  width: 80px;
  height: 80px;
  border-radius: 18px;
  background: var(--wx-green);
  color: #fff;
  font-size: 42px;
  display: flex;
  align-items: center;
  justify-content: center;
  opacity: 0.85;
}
</style>
