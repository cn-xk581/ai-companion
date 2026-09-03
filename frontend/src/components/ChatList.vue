<script setup>
import { computed, ref } from 'vue'
import Avatar from './Avatar.vue'
import { auth } from '../store/auth'
import { formatListTime, truncate } from '../utils'

const props = defineProps({
  sessions: { type: Array, default: () => [] },
  activeId: { type: String, default: '' },
})
const emit = defineEmits(['select', 'new-chat', 'logout'])

const keyword = ref('')
const filtered = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  if (!kw) return props.sessions
  return props.sessions.filter(
    (s) =>
      (s.name || '').toLowerCase().includes(kw) ||
      (s.last_message || '').toLowerCase().includes(kw)
  )
})
</script>

<template>
  <div class="chat-list">
    <div class="list-header">
      <div class="me">
        <Avatar :name="auth.user?.username || '我'" :size="34" />
        <span class="me-name">{{ auth.user?.username || '我' }}</span>
      </div>
      <div class="header-actions">
        <button class="icon-btn" title="新的聊天" @click="emit('new-chat')">＋</button>
        <button class="icon-btn logout" title="退出登录" @click="emit('logout')">⏻</button>
      </div>
    </div>

    <div class="search-box">
      <input v-model="keyword" placeholder="搜索" />
    </div>

    <div class="list-body">
      <div v-if="filtered.length === 0" class="empty">
        <p v-if="keyword">没有匹配的会话</p>
        <p v-else>还没有会话，点右上角「＋」开始聊天</p>
      </div>
      <div
        v-for="s in filtered"
        :key="s.session_id"
        class="item"
        :class="{ active: s.session_id === activeId }"
        @click="emit('select', s)"
      >
        <Avatar :name="s.name" :src="s.avatar" :size="42" />
        <div class="item-main">
          <div class="item-top">
            <span class="item-name">{{ s.name }}</span>
            <span class="item-time">{{ formatListTime(s.last_time) }}</span>
          </div>
          <div class="item-bottom">
            <span class="item-msg">{{ truncate(s.last_message || '开始聊天吧～', 22) }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chat-list {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--wx-sidebar-bg);
}
.list-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 14px 12px;
}
.me {
  display: flex;
  align-items: center;
  gap: 8px;
}
.me-name {
  font-size: 15px;
  font-weight: 600;
  max-width: 140px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.header-actions {
  display: flex;
  gap: 4px;
}
.icon-btn {
  width: 30px;
  height: 30px;
  border-radius: 6px;
  font-size: 18px;
  color: #333;
  display: flex;
  align-items: center;
  justify-content: center;
}
.icon-btn:hover {
  background: #e6e6e6;
}
.icon-btn.logout {
  font-size: 15px;
}
.search-box {
  padding: 0 12px 10px;
}
.search-box input {
  width: 100%;
  padding: 7px 12px;
  border-radius: 5px;
  border: 1px solid #e2e2e2;
  background: #fff;
  font-size: 13px;
}
.list-body {
  flex: 1;
  overflow-y: auto;
}
.item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 14px;
  cursor: pointer;
  transition: background 0.12s;
}
.item:hover {
  background: #ececec;
}
.item.active {
  background: #dcdcdc;
}
.item-main {
  flex: 1;
  min-width: 0;
  border-bottom: 1px solid #f0f0f0;
  padding-bottom: 10px;
}
.item:last-child .item-main {
  border-bottom: none;
}
.item-top {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
}
.item-name {
  font-size: 15px;
  color: #191919;
  max-width: 150px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.item-time {
  font-size: 12px;
  color: #b2b2b2;
  flex-shrink: 0;
}
.item-msg {
  font-size: 13px;
  color: var(--wx-text-sub);
  display: block;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-top: 2px;
}
.empty {
  text-align: center;
  color: #b2b2b2;
  font-size: 13px;
  padding: 40px 20px;
}
</style>
