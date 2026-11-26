<script setup>
import { computed } from 'vue'
import { moodEmoji, stagePhrase } from '../constants'
import { formatListTime, truncate } from '../utils'

const props = defineProps({
  sessions: { type: Array, default: () => [] },
  activeId: { type: String, default: '' },
})
const emit = defineEmits(['select', 'new'])

const rows = computed(() =>
  props.sessions.map((s) => ({
    ...s,
    status: `${stagePhrase(s.stage)}${s.mood ? ' · ' + moodEmoji(s.mood) : ''}`,
    preview: truncate(s.last_message || '还没开始说话', 22),
  }))
)
</script>

<template>
  <div class="list">
    <header class="list__head">
      <h2 class="list__title serif">聊天</h2>
      <button class="new" title="开始一段新对话" aria-label="新对话" @click="emit('new')">＋</button>
    </header>

    <div class="list__body">
      <p v-if="!rows.length" class="empty">
        还没有在聊的人。<br />点右上角「＋」，或者去「人物」里挑一个。
      </p>
      <button
        v-for="(s, i) in rows"
        :key="s.session_id"
        class="item"
        :class="{ 'is-active': s.session_id === activeId }"
        :style="{ '--i': i }"
        @click="emit('select', s.session_id)"
      >
        <span class="item__avatar">
          <img v-if="s.avatar" :src="s.avatar" alt="" />
          <span v-else class="serif">{{ (s.name || '柔').slice(0, 1) }}</span>
        </span>
        <span class="item__main">
          <span class="item__top">
            <span class="item__name">{{ s.name }}</span>
            <span class="item__time num">{{ formatListTime(s.last_time) }}</span>
          </span>
          <span class="item__msg">{{ s.preview }}</span>
          <span class="item__status">{{ s.status }}</span>
        </span>
      </button>
    </div>
  </div>
</template>

<style scoped>
.list {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--panel);
}
.list__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 18px 16px 10px;
}
.list__title {
  font-size: var(--step-2);
  font-weight: 500;
  color: var(--text);
}
.new {
  width: 28px;
  height: 28px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--r-tight);
  font-size: 18px;
  line-height: 1;
  color: var(--text-2);
  background: var(--raise);
  box-shadow: inset 0 0 0 1px var(--line);
}
.new:hover {
  color: var(--accent);
  box-shadow: inset 0 0 0 1px var(--accent);
}
.list__body {
  flex: 1;
  overflow-y: auto;
  padding: 2px 8px 12px;
}
.empty {
  padding: 40px 18px;
  font-size: var(--step-0);
  line-height: 1.9;
  color: var(--text-3);
  text-align: center;
}
.item {
  position: relative;
  display: flex;
  gap: 10px;
  width: 100%;
  padding: 10px;
  text-align: left;
  border-radius: var(--r-box);
  animation: settle 0.3s var(--ease) both;
  animation-delay: calc(var(--i, 0) * 22ms);
}
@keyframes settle { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: none; } }
.item:hover { background: var(--line-soft); }
.item.is-active { background: var(--panel-2); box-shadow: inset 0 0 0 1px var(--line); }
.item.is-active::before {
  content: '';
  position: absolute;
  left: -2px;
  top: 14px;
  bottom: 14px;
  width: 2px;
  border-radius: 2px;
  background: var(--accent);
}
.item__avatar {
  flex-shrink: 0;
  width: 40px;
  height: 40px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 8px;
  overflow: hidden;
  background: var(--paper);
  color: var(--paper-ink);
  font-size: 17px;
}
.item__avatar img { width: 100%; height: 100%; object-fit: cover; }
.item__main { flex: 1; min-width: 0; }
.item__top { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
.item__name { font-size: var(--step-1); color: var(--text); }
.item__time { flex-shrink: 0; font-size: var(--step--1); color: var(--text-3); }
.item__msg {
  display: block;
  margin-top: 1px;
  font-size: var(--step-0);
  color: var(--text-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.item__status { display: block; margin-top: 2px; font-size: var(--step--1); color: var(--text-3); }
</style>
