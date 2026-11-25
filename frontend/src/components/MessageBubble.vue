<script setup>
import { computed } from 'vue'

const props = defineProps({
  role: { type: String, required: true },
  content: { type: String, default: '' },
  timestamp: { type: String, default: '' },
  name: { type: String, default: '' },
  avatar: { type: String, default: '' },
})

const isMe = computed(() => props.role === 'user')
const time = computed(() => {
  if (!props.timestamp) return ''
  const d = new Date(props.timestamp)
  if (Number.isNaN(d.getTime())) return ''
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
})
</script>

<template>
  <div class="msg" :class="isMe ? 'is-me' : 'is-her'">
    <span class="avatar">
      <img v-if="!isMe && avatar" :src="avatar" alt="" />
      <span v-else class="serif">{{ isMe ? '我' : (name || '柔').slice(0, 1) }}</span>
    </span>
    <div class="body">
      <div class="bubble">
        <span class="text">{{ content }}</span>
      </div>
      <time v-if="time" class="time num">{{ time }}</time>
    </div>
  </div>
</template>

<style scoped>
.msg {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin-bottom: 16px;
  padding: 0 clamp(16px, 4vw, 34px);
}
.is-me {
  flex-direction: row-reverse;
}
.avatar {
  flex-shrink: 0;
  width: 32px;
  height: 32px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 8px;
  overflow: hidden;
  background: var(--paper);
  color: var(--paper-ink);
  font-size: 15px;
  user-select: none;
}
.avatar img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.is-me .avatar {
  background: var(--me-bg);
  color: var(--me-ink);
}
.body {
  display: flex;
  flex-direction: column;
  max-width: min(68%, 62ch);
  min-width: 0;
}
.is-me .body {
  align-items: flex-end;
}
.bubble {
  padding: 9px 13px;
  word-break: break-word;
}
.is-her .bubble {
  background: var(--paper);
  color: var(--paper-ink);
  border-radius: 3px 13px 13px 13px;
  box-shadow: 0 1px 0 rgba(255, 255, 255, 0.25) inset, 0 2px 10px rgba(0, 0, 0, 0.14);
}
[data-theme='day'] .is-her .bubble {
  box-shadow: inset 0 0 0 1px rgba(27, 33, 30, 0.07), 0 1px 2px rgba(27, 33, 30, 0.06);
}
.is-me .bubble {
  background: var(--me-bg);
  color: var(--me-ink);
  border-radius: 13px 3px 13px 13px;
}
.text {
  display: block;
  font-size: var(--step-1);
  line-height: 1.68;
  white-space: pre-wrap;
}
.time {
  margin-top: 4px;
  font-size: var(--step--1);
  color: var(--text-3);
}
</style>
