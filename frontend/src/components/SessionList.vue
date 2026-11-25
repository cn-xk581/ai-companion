<script setup>
import { computed } from 'vue'
import { moodEmoji, stagePhrase } from '../constants'
import { formatListTime, truncate } from '../utils'

const props = defineProps({
  sessions: { type: Array, default: () => [] },
  activeId: { type: String, default: '' },
  user: { type: String, default: '我' },
})
const emit = defineEmits(['select', 'new', 'settings', 'logout'])

const rows = computed(() =>
  props.sessions.map((s) => ({
    ...s,
    status: `${stagePhrase(s.stage)}${s.mood ? ' · ' + moodEmoji(s.mood) : ''}`,
    preview: truncate(s.last_message || '还没开始说话', 22),
  }))
)
</script>

<template>
  <div class="rail">
    <header class="rail__top">
      <div class="me">
        <span class="me__mark serif">{{ (user || '我').slice(0, 1) }}</span>
        <span class="me__name">{{ user }}</span>
      </div>
      <button class="btn btn--quiet btn--sm" @click="emit('new')">＋ 新对话</button>
    </header>

    <div class="rail__list">
      <p v-if="!rows.length" class="rail__empty">这里还很安静。点「新对话」，说第一句话。</p>
      <button
        v-for="(s, i) in rows"
        :key="s.session_id"
        class="item"
        :class="{ 'is-active': s.session_id === activeId }"
        :style="{ '--i': i }"
        @click="emit('select', s.session_id)"
      >
        <span class="item__avatar" :style="{ background: s.avatar ? 'transparent' : undefined }">
          <img v-if="s.avatar" :src="s.avatar" alt="" />
          <span v-else class="serif">{{ (s.name || '柔').slice(0, 1) }}</span>
        </span>
        <span class="item__main">
          <span class="item__top">
            <span class="item__name">{{ s.name }}</span>
            <span class="item__time num">{{ formatListTime(s.last_time) }}</span>
          </span>
          <span class="item__bottom">
            <span class="item__msg">{{ s.preview }}</span>
          </span>
          <span class="item__status">{{ s.status }}</span>
        </span>
      </button>
    </div>

    <footer class="rail__bottom">
      <button class="rail__btn" @click="emit('settings')">
        <span class="glyph" aria-hidden="true">⚙</span>设置
      </button>
      <button class="rail__btn" @click="emit('logout')">
        <span class="glyph" aria-hidden="true">⏻</span>退出
      </button>
    </footer>
  </div>
</template>

<style scoped>
.rail {
  display: flex;
  flex-direction: column;
  height: 100%;
}
.rail__top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 16px 14px 12px;
}
.me {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.me__mark {
  width: 28px;
  height: 28px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 7px;
  font-size: 15px;
  color: var(--paper-ink);
  background: var(--paper);
}
.me__name {
  font-size: var(--step-0);
  color: var(--text);
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rail__list {
  flex: 1;
  overflow-y: auto;
  padding: 2px 8px 10px;
}
.rail__empty {
  padding: 26px 16px;
  font-size: var(--step-0);
  line-height: 1.8;
  color: var(--text-3);
  text-align: center;
}
.item {
  position: relative;
  display: flex;
  gap: 10px;
  width: 100%;
  padding: 10px 10px 10px 12px;
  border-radius: var(--r-box);
  text-align: left;
  animation: settle 0.32s var(--ease) both;
  animation-delay: calc(var(--i, 0) * 24ms);
}
@keyframes settle {
  from {
    opacity: 0;
    transform: translateY(5px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}
.item:hover {
  background: var(--line-soft);
}
.item.is-active {
  background: var(--panel-2);
  box-shadow: inset 0 0 0 1px var(--line);
}
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
.item__avatar img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.item__main {
  flex: 1;
  min-width: 0;
}
.item__top {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}
.item__name {
  font-size: var(--step-1);
  color: var(--text);
}
.item__time {
  flex-shrink: 0;
  font-size: var(--step--1);
  color: var(--text-3);
}
.item__bottom {
  display: block;
  margin-top: 1px;
}
.item__msg {
  font-size: var(--step-0);
  color: var(--text-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  display: block;
}
.item__status {
  display: block;
  margin-top: 2px;
  font-size: var(--step--1);
  color: var(--text-3);
}
.rail__bottom {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 8px 10px;
  border-top: 1px solid var(--line);
}
.rail__btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 30px;
  padding: 0 10px;
  border-radius: var(--r-tight);
  font-size: var(--step-0);
  color: var(--text-2);
  transition: color 0.14s, background 0.14s;
}
.rail__btn:hover {
  color: var(--text);
  background: var(--line-soft);
}
.glyph {
  font-size: 14px;
  line-height: 1;
  opacity: 0.85;
}
</style>
