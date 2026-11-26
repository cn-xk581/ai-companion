<script setup>
import { computed, ref } from 'vue'
import { api, uploadAvatar } from '../api'
import { setUser } from '../store/auth'

const props = defineProps({
  user: { type: Object, default: null },
})
const emit = defineEmits(['close', 'logout'])

const fileInput = ref(null)
const busy = ref(false)
const error = ref('')

const nickname = computed(() => props.user?.nickname || props.user?.username || '我')
const initial = computed(() => nickname.value.slice(0, 1))
const avatar = computed(() => props.user?.avatar || '')
const userId = computed(() => props.user?.user_id || '')

function pickFile() {
  fileInput.value?.click()
}

async function onFile(ev) {
  const file = ev.target.files?.[0]
  ev.target.value = ''
  if (!file) return
  error.value = ''
  busy.value = true
  try {
    const user = await uploadAvatar(file)
    setUser({ ...(props.user || {}), ...user })
  } catch (e) {
    error.value = e.message || '上传失败'
  } finally {
    busy.value = false
  }
}

async function saveNickname() {
  const name = prompt('新的昵称', nickname.value)
  if (!name) return
  error.value = ''
  busy.value = true
  try {
    const user = await api.updateMe(name.trim())
    setUser({ ...(props.user || {}), ...user })
  } catch (e) {
    error.value = e.message || '保存失败'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="scrim" @click.self="emit('close')">
    <section class="card" role="dialog" aria-label="我的信息">
      <button class="x" aria-label="关闭" @click="emit('close')">✕</button>

      <button class="avatar" :disabled="busy" title="点击上传头像（jpg/png）" @click="pickFile">
        <img v-if="avatar" :src="avatar" alt="" />
        <span v-else class="serif">{{ initial }}</span>
        <span class="avatar__hint">{{ busy ? '处理中…' : '换头像' }}</span>
      </button>
      <input ref="fileInput" type="file" accept="image/jpeg,image/png" hidden @change="onFile" />

      <h2 class="name serif" title="点击修改昵称" @click="saveNickname">{{ nickname }}</h2>
      <p class="id num">@{{ props.user?.username || '' }}<template v-if="userId"> · ID {{ userId }}</template></p>
      <p v-if="error" class="err">{{ error }}</p>
      <p class="note">
        资料仅自己可见。头像会压缩成 512×512 的 webp，存在你的服务器上。
      </p>
      <div class="actions">
        <button class="btn btn--quiet btn--full" :disabled="busy" @click="saveNickname">修改昵称</button>
        <button class="btn btn--danger btn--full" @click="emit('logout')">退出登录</button>
      </div>
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
  width: min(360px, calc(100vw - 40px));
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
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 76px;
  height: 76px;
  border-radius: 20px;
  overflow: hidden;
  background: var(--paper);
  color: var(--paper-ink);
  font-size: 30px;
}
.avatar img { width: 100%; height: 100%; object-fit: cover; }
.avatar__hint {
  position: absolute;
  inset: auto 0 0 0;
  padding: 3px 0;
  font-size: 10px;
  color: #fff;
  background: rgba(0, 0, 0, 0.45);
  opacity: 0;
  transition: opacity 0.15s;
}
.avatar:hover .avatar__hint { opacity: 1; }
.name {
  margin-top: 12px;
  font-size: var(--step-2);
  color: var(--text);
  cursor: pointer;
}
.id { margin-top: 4px; font-size: var(--step--1); color: var(--text-3); }
.err { margin-top: 8px; font-size: var(--step--1); color: var(--bad); }
.note {
  margin: 14px 0 16px;
  font-size: var(--step--1);
  line-height: 1.8;
  color: var(--text-3);
}
.actions { display: flex; flex-direction: column; gap: 8px; }
.full { width: 100%; }
</style>
