<script setup>
import { computed } from 'vue'
import Avatar from './Avatar.vue'
import { formatMsgTime } from '../utils'

const props = defineProps({
  role: { type: String, required: true }, // user | assistant | system | tool
  content: { type: String, default: '' },
  timestamp: { type: String, default: '' },
  name: { type: String, default: '' },
  avatar: { type: String, default: '' },
})

const isUser = computed(() => props.role === 'user')
</script>

<template>
  <div class="msg" :class="isUser ? 'msg-right' : 'msg-left'">
    <Avatar :name="isUser ? '我' : name" :src="isUser ? '' : avatar" :size="38" />
    <div class="msg-body">
      <div class="bubble" :class="isUser ? 'bubble-out' : 'bubble-in'">
        <span class="bubble-text">{{ content }}</span>
      </div>
      <div v-if="timestamp" class="msg-time">{{ formatMsgTime(timestamp) }}</div>
    </div>
  </div>
</template>

<style scoped>
.msg {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin-bottom: 18px;
  padding: 0 16px;
}
.msg-left {
  flex-direction: row;
}
.msg-right {
  flex-direction: row-reverse;
}
.msg-body {
  max-width: 62%;
  display: flex;
  flex-direction: column;
}
.msg-right .msg-body {
  align-items: flex-end;
}
.bubble {
  padding: 10px 13px;
  border-radius: 6px;
  position: relative;
  word-break: break-word;
}
.bubble-in {
  background: #fff;
  border: 1px solid #f0f0f0;
}
.bubble-out {
  background: var(--wx-bubble-out);
}
.bubble-text {
  font-size: 15px;
  line-height: 1.55;
  white-space: pre-wrap;
}
.msg-time {
  font-size: 11px;
  color: var(--wx-text-sub);
  margin-top: 4px;
}
</style>
