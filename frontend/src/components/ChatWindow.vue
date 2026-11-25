<script setup>
import { computed, nextTick, onUnmounted, reactive, ref, watch } from 'vue'
import MessageBubble from './MessageBubble.vue'
import ReviewGate from './ReviewGate.vue'
import { api, streamChat } from '../api'
import { EMOJIS, moodEmoji, moodLabel, stagePhrase } from '../constants'
import { uuid } from '../utils'

const props = defineProps({
  sessionId: { type: String, required: true },
  detail: { type: Object, default: null },
  name: { type: String, default: '小柔' },
  avatar: { type: String, default: '' },
})
const emit = defineEmits(['sent', 'trace', 'back', 'session-settings'])

const messages = ref([])
const input = ref('')
const streaming = ref(false)
const loading = ref(false)
const showEmoji = ref(false)
const listEl = ref(null)
const inputEl = ref(null)
const review = ref(null)
const reviewing = ref(false)
const lastTurnId = ref('')
const error = ref('')

let streamCtrl = null

const visibleMessages = computed(() => messages.value.filter((m) => m.role === 'user' || (m.content && m.content.trim())))

const stage = computed(() => props.detail?.stage || 'stranger')
const mood = computed(() => props.detail?.mood?.current_mood || 'neutral')
const score = computed(() => Math.round(props.detail?.score || 0))

async function loadHistory() {
  loading.value = true
  error.value = ''
  messages.value = []
  review.value = null
  try {
    const res = await api.getHistory(props.sessionId)
    messages.value = (res.messages || []).map((m) => ({
      role: m.role,
      content: m.content || '',
      timestamp: m.timestamp || '',
    }))
    await scrollToBottom()
  } catch (e) {
    error.value = e.message || '读取历史失败'
  } finally {
    loading.value = false
  }
}

async function scrollToBottom() {
  await nextTick()
  if (listEl.value) listEl.value.scrollTop = listEl.value.scrollHeight
}

async function resetInputHeight() {
  await nextTick()
  if (inputEl.value) inputEl.value.style.height = 'auto'
}

function autosize(ev) {
  const el = ev.target
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 132) + 'px'
}

let turnBubbles = []
let turnText = ''

function pushAssistant(initial = '', source = '') {
  // 用 reactive 包一层：直接改原始对象不会触发渲染（Vue 3 只追踪代理）
  const bubble = reactive({
    role: 'assistant',
    content: initial,
    timestamp: '',
    failed: false,
    text: source,
  })
  messages.value.push(bubble)
  turnBubbles.push(bubble)
  return bubble
}

function currentBubble() {
  if (!turnBubbles.length) return pushAssistant('', turnText)
  return turnBubbles[turnBubbles.length - 1]
}

function appendStreamChunk(chunk) {
  // 模型用换行分隔“连发”的多条消息：遇到换行就开下一个气泡
  const pieces = String(chunk).split('\n')
  pieces.forEach((piece, idx) => {
    if (idx > 0) pushAssistant('', turnText)
    currentBubble().content += piece
  })
  scrollToBottom()
}

function renderFinalReply(finalText) {
  const lines = String(finalText || '')
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
  const kept = lines.length ? lines : ['']
  messages.value = messages.value.filter((m) => !turnBubbles.includes(m))
  turnBubbles = []
  kept.forEach((line, idx) => {
    const add = () => {
      messages.value.push(
        reactive({
          role: 'assistant',
          content: line,
          timestamp: new Date().toISOString(),
          failed: false,
          text: turnText,
        })
      )
      scrollToBottom()
    }
    if (idx === 0) add()
    else setTimeout(add, idx * 160) // 多条时逐条出现，像在连发
  })
  scrollToBottom()
}

function send() {
  const text = input.value.trim()
  if (!text || streaming.value || loading.value) return
  input.value = ''
  showEmoji.value = false
  resetInputHeight()
  error.value = ''
  review.value = null

  const clientMsgId = uuid()
  messages.value.push({ role: 'user', content: text, timestamp: new Date().toISOString() })
  turnText = text
  turnBubbles = []
  pushAssistant('', text)
  streaming.value = true
  scrollToBottom()

  streamCtrl = streamChat({
    sessionId: props.sessionId,
    userInput: text,
    clientMsgId,
    onMeta: (meta) => {
      lastTurnId.value = meta.turn_id || ''
    },
    onToken: appendStreamChunk,
    onDone: (payload) => {
      streamCtrl = null
      streaming.value = false
      renderFinalReply(payload.reply || '')
      lastTurnId.value = payload.turn_id || lastTurnId.value
      if (payload.status === 'awaiting_review') review.value = payload
      emit('sent', payload)
      scrollToBottom()
    },
    onError: (err) => {
      streamCtrl = null
      streaming.value = false
      currentBubble().failed = true
      error.value = err.message || '发送失败'
      if (!currentBubble().content) currentBubble().content = '（没发出去，点下面重试）'
    },
  })
}

function retry(message) {
  if (streaming.value || !message.text) return
  messages.value = messages.value.filter((m) => m !== message)
  input.value = message.text
  send()
}

async function decide({ decision, feedback }) {
  if (!review.value) return
  reviewing.value = true
  error.value = ''
  try {
    const payload = await api.resumeTurn(review.value.turn_id, decision, feedback)
    const last = [...messages.value].reverse().find((m) => m.role === 'assistant')
    if (last) last.content = payload.reply || last.content
    review.value = null
    emit('sent', payload)
  } catch (e) {
    error.value = e.message || '复核失败'
  } finally {
    reviewing.value = false
  }
}

function addEmoji(e) {
  input.value += e
  nextTick(() => inputEl.value?.focus())
}

function onKeydown(ev) {
  if (ev.isComposing || ev.keyCode === 229) return
  if (ev.key === 'Enter' && !ev.shiftKey) {
    ev.preventDefault()
    send()
  }
}

watch(() => props.sessionId, () => loadHistory(), { immediate: true })

onUnmounted(() => {
  if (streamCtrl) {
    streamCtrl.abort()
    streamCtrl = null
  }
})
</script>

<template>
  <div class="chat">
    <header class="head">
      <button class="back" aria-label="返回会话列表" @click="emit('back')">‹</button>
      <span class="head__avatar">
        <img v-if="avatar" :src="avatar" alt="" />
        <span v-else class="serif">{{ (name || '柔').slice(0, 1) }}</span>
      </span>
      <div class="who">
        <h1 class="who__name serif">{{ name }}</h1>
        <p class="who__line">
          <span>{{ stagePhrase(stage) }}</span>
          <i class="split" aria-hidden="true"></i>
          <span>{{ moodEmoji(mood) }} {{ moodLabel(mood) }}</span>
        </p>
      </div>
      <div class="head__actions">
        <button
          class="btn btn--quiet btn--sm"
          title="人设 / 记忆 / 能力（只对当前会话）"
          @click="emit('session-settings')"
        >
          会话设置
        </button>
        <button class="btn btn--quiet btn--sm" :disabled="!lastTurnId" @click="emit('trace', lastTurnId)">
          追踪
        </button>
      </div>
    </header>

    <div ref="listEl" class="thread paper-rules">
      <p v-if="loading" class="hint">正在取回之前的对话…</p>
      <template v-else>
        <p v-if="!messages.length" class="hint hint--first">
          {{ name }}在等你开口。先说一句「在吗」也可以。
        </p>
        <template v-for="(m, i) in visibleMessages" :key="i">
          <MessageBubble
            :role="m.role"
            :content="m.content"
            :timestamp="m.timestamp"
            :name="name"
            :avatar="avatar"
          />
          <button v-if="m.failed" class="retry" @click="retry(m)">重新发送</button>
        </template>
      </template>

      <ReviewGate v-if="review" :turn="review" :busy="reviewing" @decide="decide" />

      <div v-if="streaming && messages[messages.length - 1]?.content === ''" class="typing" aria-live="polite">
        <span></span><span></span><span></span>
      </div>
    </div>

    <p v-if="error" class="error" @click="error = ''">{{ error }}</p>

    <div class="composer">
      <div v-if="showEmoji" class="emoji-panel">
        <button v-for="e in EMOJIS" :key="e" class="emoji" @click="addEmoji(e)">{{ e }}</button>
      </div>
      <div class="composer__row">
        <button
          class="emoji-toggle"
          :class="{ 'is-on': showEmoji }"
          title="表情"
          aria-label="表情"
          @click="showEmoji = !showEmoji"
        >
          ☺
        </button>
        <textarea
          ref="inputEl"
          v-model="input"
          class="composer__input"
          rows="1"
          placeholder="说点什么…"
          title="Enter 发送，Shift+Enter 换行"
          @input="autosize"
          @keydown="onKeydown"
        ></textarea>
        <button class="btn btn--primary send" :disabled="streaming || loading || !input.trim()" @click="send">
          发送
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chat {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-width: 0;
  overflow: hidden;
}
.head {
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 12px clamp(16px, 4vw, 34px);
  border-bottom: 1px solid var(--line);
  background: var(--panel);
}
.back {
  display: none;
  font-size: 22px;
  line-height: 1;
  color: var(--text-2);
  padding: 0 4px;
}
.head__avatar {
  width: 34px;
  height: 34px;
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 9px;
  overflow: hidden;
  background: var(--paper);
  color: var(--paper-ink);
  font-size: 16px;
}
.head__avatar img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.who {
  flex: 1;
  min-width: 0;
}
.who__name {
  font-size: var(--step-2);
  font-weight: 500;
  line-height: 1.15;
  color: var(--text);
}
.who__line {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 3px;
  font-size: var(--step--1);
  color: var(--text-2);
  flex-wrap: wrap;
}
.split {
  width: 1px;
  height: 10px;
  background: var(--line);
}
.head__actions {
  display: flex;
  gap: 6px;
}

.thread {
  flex: 1;
  overflow-y: auto;
  padding: 22px 0 8px;
  background-color: var(--bg);
}
.hint {
  padding: 26px clamp(16px, 4vw, 34px);
  font-size: var(--step-0);
  color: var(--text-3);
  text-align: center;
}
.hint--first {
  padding-top: 15vh;
  font-size: var(--step-1);
  color: var(--text-2);
}
.typing {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 0 clamp(16px, 4vw, 34px) 4px 58px;
  margin-bottom: 14px;
}
.typing span {
  width: 3px;
  height: 12px;
  border-radius: 2px;
  background: var(--accent);
  opacity: 0.35;
  animation: breath 1.05s var(--ease) infinite;
}
.typing span:nth-child(2) {
  animation-delay: 0.16s;
}
.typing span:nth-child(3) {
  animation-delay: 0.32s;
}
@keyframes breath {
  0%,
  100% {
    opacity: 0.22;
    transform: scaleY(0.7);
  }
  45% {
    opacity: 1;
    transform: scaleY(1);
  }
}
.retry {
  display: block;
  margin: -8px 0 16px 56px;
  padding: 3px 12px;
  border-radius: var(--r-tight);
  font-size: var(--step--1);
  color: var(--text-2);
  box-shadow: inset 0 0 0 1px var(--line);
}
.retry:hover {
  color: var(--text);
  background: var(--line-soft);
}
.error {
  padding: 6px clamp(16px, 4vw, 34px);
  font-size: var(--step--1);
  color: var(--bad);
  background: color-mix(in srgb, var(--bad) 8%, transparent);
  cursor: pointer;
}

.composer {
  border-top: 1px solid var(--line);
  background: var(--panel);
}
.emoji-panel {
  display: grid;
  grid-template-columns: repeat(11, 1fr);
  gap: 2px;
  padding: 10px clamp(14px, 3vw, 28px);
  max-height: 168px;
  overflow-y: auto;
  border-bottom: 1px solid var(--line-soft);
}
.emoji {
  font-size: 20px;
  line-height: 1;
  padding: 5px 0;
  border-radius: var(--r-tight);
}
.emoji:hover {
  background: var(--line-soft);
}
.composer__row {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  padding: 10px clamp(14px, 3vw, 28px) 12px;
}
.emoji-toggle {
  flex-shrink: 0;
  width: 34px;
  height: 34px;
  border-radius: var(--r-tight);
  font-size: 18px;
  color: var(--text-2);
}
.emoji-toggle:hover,
.emoji-toggle.is-on {
  color: var(--accent);
  background: var(--accent-soft);
}
.composer__input {
  flex: 1;
  min-width: 0;
  min-height: 34px;
  padding: 7px 12px;
  border-radius: var(--r-box);
  background: var(--raise);
  box-shadow: inset 0 0 0 1px var(--line);
  font-size: var(--step-1);
  line-height: 1.5;
  resize: none;
}
.composer__input:focus,
.composer__input:focus-visible {
  outline: none;
  box-shadow: inset 0 0 0 1px var(--accent);
}
.send {
  flex-shrink: 0;
  height: 34px;
  padding: 0 20px;
}

@media (max-width: 900px) {
  .back {
    display: inline-flex;
  }
}
@media (max-width: 640px) {
  .emoji-panel {
    grid-template-columns: repeat(8, 1fr);
  }
  .send {
    padding: 0 14px;
  }
}
</style>
