<script setup>
import { nextTick, onUnmounted, ref, watch } from 'vue'
import Avatar from './Avatar.vue'
import MessageBubble from './MessageBubble.vue'
import { api, streamChat } from '../api'
import { STAGE_LABELS, MOOD_LABELS, MOOD_EMOJI, EMOJIS } from '../constants'
import { uuid } from '../utils'

const props = defineProps({
  session: { type: Object, required: true },
})
const emit = defineEmits(['sent', 'open-settings'])

const messages = ref([])
const input = ref('')
const streaming = ref(false)
const loading = ref(false)
const showEmoji = ref(false)
const listEl = ref(null)

const meta = ref({
  relationship_stage: props.session.relationship_stage || 'stranger',
  relationship_score: props.session.relationship_score || 0,
  mood: props.session.mood || 'neutral',
  mood_intensity: props.session.mood_intensity ?? 0.5,
})

let streamCtrl = null // 当前流式请求的 AbortController

const stageLabel = () => STAGE_LABELS[meta.value.relationship_stage] || meta.value.relationship_stage
const moodLabel = () => MOOD_LABELS[meta.value.mood] || meta.value.mood
const moodEmoji = () => MOOD_EMOJI[meta.value.mood] || '😐'

async function loadHistory() {
  const sid = props.session.session_id
  messages.value = []
  if (props.session.pending) return
  loading.value = true
  try {
    const res = await api.getHistory(sid)
    if (props.session.session_id !== sid) return // 加载期间已切换会话：丢弃过期响应
    messages.value = (res.messages || []).map((m) => ({
      role: m.role,
      content: m.content || '',
      timestamp: m.timestamp || '',
    }))
    await scrollToBottom()
  } catch {
    // 历史加载失败不阻塞聊天
  } finally {
    if (props.session.session_id === sid) loading.value = false
  }
}

async function scrollToBottom() {
  await nextTick()
  if (listEl.value) listEl.value.scrollTop = listEl.value.scrollHeight
}

function startStream(assistant, text, clientMsgId) {
  streamCtrl = streamChat(props.session.session_id, text, {
    clientMsgId,
    onToken: (t) => {
      assistant.content += t
      scrollToBottom()
    },
    onDone: (done) => {
      streamCtrl = null
      // 以服务端最终文本为准：流式被兜底/截断时与落库、侧栏预览保持一致
      assistant.content = done.reply || assistant.content
      assistant.timestamp = new Date().toISOString()
      assistant.failed = false
      streaming.value = false
      meta.value = {
        relationship_stage: done.relationship_stage || meta.value.relationship_stage,
        relationship_score: done.relationship_score ?? meta.value.relationship_score,
        mood: done.mood || meta.value.mood,
        mood_intensity: done.mood_intensity ?? meta.value.mood_intensity,
      }
      emit('sent', done)
      scrollToBottom()
    },
    onError: (err) => {
      streamCtrl = null
      streaming.value = false
      assistant.failed = true
      assistant.retryText = text
      assistant.clientMsgId = clientMsgId
      if (!assistant.content) {
        assistant.content = '（发送失败：' + (err.message || '网络错误') + '）'
      } else {
        assistant.content += '\n\n（回复中断）'
      }
    },
  })
}

async function send() {
  const text = input.value.trim()
  if (!text || streaming.value || loading.value) return
  input.value = ''
  showEmoji.value = false

  // 每条消息生成一个幂等键；重试复用同一键，避免服务端重复落库
  const clientMsgId = uuid()
  messages.value.push({ role: 'user', content: text, timestamp: new Date().toISOString() })
  const assistant = { role: 'assistant', content: '', timestamp: '', failed: false }
  messages.value.push(assistant)
  streaming.value = true
  await scrollToBottom()

  startStream(assistant, text, clientMsgId)
}

function retry(assistant) {
  if (streaming.value || loading.value || !assistant.retryText) return
  assistant.failed = false
  assistant.content = ''
  streaming.value = true
  scrollToBottom()
  startStream(assistant, assistant.retryText, assistant.clientMsgId)
}

function addEmoji(e) {
  input.value += e
}

function onKeydown(ev) {
  // 中文输入法组词中（isComposing / keyCode 229）按 Enter 是上屏，不应发送
  if (ev.isComposing || ev.keyCode === 229) return
  if (ev.key === 'Enter' && !ev.shiftKey) {
    ev.preventDefault()
    send()
  }
}

watch(
  () => props.session.session_id,
  () => {
    // 切换会话：中止仍在进行的流式请求，避免旧请求串台/阻塞
    if (streamCtrl) {
      streamCtrl.abort()
      streamCtrl = null
    }
    streaming.value = false
    meta.value = {
      relationship_stage: props.session.relationship_stage || 'stranger',
      relationship_score: props.session.relationship_score || 0,
      mood: props.session.mood || 'neutral',
      mood_intensity: props.session.mood_intensity ?? 0.5,
    }
    loadHistory()
  },
  { immediate: true }
)

onUnmounted(() => {
  if (streamCtrl) {
    streamCtrl.abort()
    streamCtrl = null
  }
})
</script>

<template>
  <div class="chat-window">
    <header class="chat-header">
      <Avatar :name="session.name || '小柔'" :src="session.avatar" :size="38" />
      <div class="title">
        <div class="title-name">{{ session.name || '小柔' }}</div>
        <div class="title-status">
          <span class="badge stage">{{ stageLabel() }}</span>
          <span class="badge mood">{{ moodEmoji() }} {{ moodLabel() }}</span>
          <span class="score" :title="'亲密度 ' + Math.round(meta.relationship_score)">
            <i class="score-bar"><i class="score-fill" :style="{ width: meta.relationship_score + '%' }"></i></i>
            <em>亲密度 {{ Math.round(meta.relationship_score) }}</em>
          </span>
        </div>
      </div>
      <button class="more" title="设置" @click="emit('open-settings')">⋯</button>
    </header>

    <div ref="listEl" class="messages">
      <div v-if="loading" class="hint">加载历史消息…</div>
      <template v-else>
        <div v-if="messages.length === 0" class="hint">
          和 {{ session.name || '小柔' }} 打个招呼吧 👋
        </div>
        <template v-for="(m, i) in messages" :key="i">
          <MessageBubble
            :role="m.role"
            :content="m.content"
            :timestamp="m.timestamp"
            :name="session.name || '小柔'"
            :avatar="session.avatar"
          />
          <button
            v-if="m.failed && m.role === 'assistant'"
            class="retry"
            @click="retry(m)"
          >重试</button>
        </template>
      </template>
      <div v-if="streaming && messages[messages.length - 1]?.content === ''" class="typing">
        <i></i><i></i><i></i>
      </div>
    </div>

    <div class="input-area">
      <div v-if="showEmoji" class="emoji-panel">
        <button v-for="e in EMOJIS" :key="e" class="emoji" @click="addEmoji(e)">{{ e }}</button>
      </div>
      <div class="input-bar">
        <button class="tool" :class="{ on: showEmoji }" @click="showEmoji = !showEmoji">😊</button>
        <textarea
          v-model="input"
          rows="1"
          placeholder="输入消息…（Enter 发送，Shift+Enter 换行）"
          @keydown="onKeydown"
        ></textarea>
        <button class="btn btn-primary send" :disabled="streaming || loading || !input.trim()" @click="send">
          发送
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chat-window {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: var(--wx-bg);
}
.chat-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 18px;
  background: var(--wx-header-bg);
  border-bottom: 1px solid var(--wx-border);
}
.title {
  flex: 1;
  min-width: 0;
}
.title-name {
  font-size: 16px;
  font-weight: 600;
}
.title-status {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 3px;
  flex-wrap: wrap;
}
.badge {
  font-size: 11px;
  padding: 1px 7px;
  border-radius: 10px;
  line-height: 18px;
}
.badge.stage {
  background: #e8f7ee;
  color: var(--wx-green-dark);
}
.badge.mood {
  background: #fff3e0;
  color: #e08a00;
}
.score {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-style: normal;
}
.score em {
  font-size: 11px;
  color: var(--wx-text-sub);
}
.score-bar {
  display: inline-block;
  width: 60px;
  height: 6px;
  background: #e0e0e0;
  border-radius: 3px;
  overflow: hidden;
}
.score-fill {
  display: block;
  height: 100%;
  background: linear-gradient(90deg, #ff9a9e, #fa5151);
  border-radius: 3px;
  transition: width 0.4s;
}
.more {
  font-size: 22px;
  color: #555;
  width: 34px;
  height: 34px;
  border-radius: 6px;
}
.more:hover {
  background: #e6e6e6;
}

.messages {
  flex: 1;
  overflow-y: auto;
  padding: 20px 0;
}
.hint {
  text-align: center;
  color: #b2b2b2;
  font-size: 13px;
  padding: 30px 20px;
}
.typing {
  display: flex;
  gap: 4px;
  padding: 0 16px 0 64px;
  margin-bottom: 18px;
}
.typing i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #b2b2b2;
  animation: blink 1.2s infinite;
}
.typing i:nth-child(2) {
  animation-delay: 0.2s;
}
.typing i:nth-child(3) {
  animation-delay: 0.4s;
}
@keyframes blink {
  0%, 60%, 100% { opacity: 0.3; }
  30% { opacity: 1; }
}

.retry {
  margin: -10px 0 16px 64px;
  color: #fa5151;
  font-size: 13px;
  background: #fff;
  border: 1px solid #f0c0c0;
  border-radius: 14px;
  padding: 3px 14px;
}
.retry:hover {
  background: #fff0f0;
}
.input-area {
  border-top: 1px solid var(--wx-border);
  background: #f7f7f7;
}
.emoji-panel {
  display: grid;
  grid-template-columns: repeat(8, 1fr);
  gap: 2px;
  padding: 10px 14px;
  background: #fff;
  border-bottom: 1px solid var(--wx-border);
  max-height: 160px;
  overflow-y: auto;
}
.emoji {
  font-size: 22px;
  padding: 4px;
  border-radius: 6px;
}
.emoji:hover {
  background: #f2f2f2;
}
.input-bar {
  display: flex;
  align-items: flex-end;
  gap: 10px;
  padding: 10px 14px;
}
.tool {
  font-size: 22px;
  width: 34px;
  height: 34px;
  border-radius: 6px;
  flex-shrink: 0;
}
.tool.on {
  background: #e0e0e0;
}
.input-bar textarea {
  flex: 1;
  resize: none;
  border: none;
  background: #fff;
  border-radius: 6px;
  padding: 9px 12px;
  font-size: 15px;
  line-height: 1.5;
  max-height: 120px;
  min-height: 38px;
}
.send {
  flex-shrink: 0;
  padding: 8px 22px;
  font-size: 15px;
}
</style>
