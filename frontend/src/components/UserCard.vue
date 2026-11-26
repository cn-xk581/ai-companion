<script setup>
const props = defineProps({
  user: { type: Object, default: null },
})
const emit = defineEmits(['close', 'logout'])

const username = props.user?.username || '我'
const userId = props.user?.user_id || ''
</script>

<template>
  <div class="scrim" @click.self="emit('close')">
    <section class="card" role="dialog" aria-label="我的信息">
      <button class="x" aria-label="关闭" @click="emit('close')">✕</button>
      <span class="avatar serif">{{ username.slice(0, 1) }}</span>
      <h2 class="name serif">{{ username }}</h2>
      <p v-if="userId" class="id num">ID {{ userId.slice(0, 12) }}</p>
      <p class="note">
        数据只存在你自己的服务器上。会话、人设、记忆都可以在对应面板里修改或删除。
      </p>
      <button class="btn btn--danger btn--full" @click="emit('logout')">退出登录</button>
    </section>
  </div>
</template>

<style scoped>
.scrim {
  position: fixed;
  inset: 0;
  z-index: 120;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--scrim);
  animation: fade 0.15s var(--ease);
}
@keyframes fade { from { opacity: 0; } to { opacity: 1; } }
.card {
  position: relative;
  width: min(340px, calc(100vw - 40px));
  padding: 26px 24px 22px;
  border-radius: var(--r-box);
  text-align: center;
  background: var(--panel);
  border: 1px solid var(--line);
  box-shadow: var(--shadow);
  animation: pop 0.18s var(--ease);
}
@keyframes pop { from { transform: translateY(6px); opacity: 0; } to { transform: none; opacity: 1; } }
.x {
  position: absolute;
  top: 10px;
  right: 12px;
  font-size: 14px;
  color: var(--text-3);
}
.x:hover { color: var(--text); }
.avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 62px;
  height: 62px;
  border-radius: 16px;
  background: var(--paper);
  color: var(--paper-ink);
  font-size: 26px;
}
.name {
  margin-top: 12px;
  font-size: var(--step-2);
  color: var(--text);
}
.id {
  margin-top: 4px;
  font-size: var(--step--1);
  color: var(--text-3);
}
.note {
  margin: 14px 0 16px;
  font-size: var(--step--1);
  line-height: 1.8;
  color: var(--text-3);
}
.full { width: 100%; }
</style>
