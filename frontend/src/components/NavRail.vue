<script setup>
import { computed } from 'vue'

const props = defineProps({
  active: { type: String, default: 'chat' },
  user: { type: Object, default: null },
})
const emit = defineEmits(['view', 'settings', 'user'])

const initial = computed(() => (props.user?.nickname || props.user?.username || '我').slice(0, 1).toUpperCase())
</script>

<template>
  <nav class="rail" aria-label="主导航">
    <button class="avatar" title="查看我的信息" aria-label="我的信息" @click="emit('user')">
      <span class="serif">{{ initial }}</span>
    </button>

    <div class="nav">
      <button
        class="nav__btn"
        :class="{ 'is-on': active === 'chat' }"
        title="聊天"
        aria-label="聊天"
        @click="emit('view', 'chat')"
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M4.5 5.5h15v10.5h-9l-4 3.5v-3.5h-2z" />
        </svg>
        <span class="nav__label">聊天</span>
      </button>
      <button
        class="nav__btn"
        :class="{ 'is-on': active === 'characters' }"
        title="人物列表"
        aria-label="人物列表"
        @click="emit('view', 'characters')"
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="12" cy="8.5" r="3.4" />
          <path d="M5.5 19.5c0-3.4 2.9-5.6 6.5-5.6s6.5 2.2 6.5 5.6" />
        </svg>
        <span class="nav__label">人物</span>
      </button>
    </div>

    <button class="nav__btn nav__btn--bottom" title="全局设置" aria-label="全局设置" @click="emit('settings')">
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <circle cx="12" cy="12" r="3" />
        <path d="M12 3.5v3M12 17.5v3M3.5 12h3M17.5 12h3M6 6l2.1 2.1M15.9 15.9L18 18M18 6l-2.1 2.1M8.1 15.9L6 18" />
      </svg>
      <span class="nav__label">设置</span>
    </button>
  </nav>
</template>

<style scoped>
.rail {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  width: var(--rail-w-sm);
  padding: 14px 0 12px;
  background: var(--bg-deep);
  border-right: 1px solid var(--line);
}
.avatar {
  width: 42px;
  height: 42px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 12px;
  background: var(--paper);
  color: var(--paper-ink);
  font-size: 18px;
  transition: transform 0.15s var(--ease);
}
.avatar:hover {
  transform: translateY(-1px);
}
.nav {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 6px;
  flex: 1;
}
.nav__btn {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  width: 52px;
  padding: 8px 0 6px;
  border-radius: var(--r-tight);
  color: var(--text-3);
  transition: color 0.14s, background 0.14s;
}
.nav__btn:hover {
  color: var(--text);
  background: var(--line-soft);
}
.nav__btn.is-on {
  color: var(--accent);
  background: var(--accent-soft);
}
.nav__btn--bottom {
  margin-top: auto;
}
.nav__btn svg {
  width: 20px;
  height: 20px;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.6;
  stroke-linejoin: round;
  stroke-linecap: round;
}
.nav__label {
  font-size: 10px;
  letter-spacing: 0.02em;
}
@media (max-width: 900px) {
  .rail {
    width: 56px;
  }
  .nav__label {
    display: none;
  }
}
</style>
