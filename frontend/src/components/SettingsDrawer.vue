<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'
import { PERSONA_FIELDS } from '../constants'

const props = defineProps({
  session: { type: Object, required: true },
})
const emit = defineEmits(['close', 'changed'])

const tab = ref('persona')
const persona = ref(null)
const memories = ref([])
const loading = ref(false)
const saving = ref(false)
const msg = ref('')

async function loadPersona() {
  try {
    const res = await api.getSessionDetail(props.session.session_id)
    persona.value = { ...res.persona }
  } catch (e) {
    msg.value = e.message || '加载人设失败'
  }
}

async function loadMemories() {
  try {
    const res = await api.listMemories(props.session.session_id)
    memories.value = res.memories || []
  } catch (e) {
    msg.value = e.message || '加载记忆失败'
  }
}

async function savePersona() {
  saving.value = true
  msg.value = ''
  try {
    const p = { ...persona.value }
    if (p.age === '' || p.age === null) p.age = null
    else p.age = Number(p.age)
    const res = await api.updatePersona(props.session.session_id, p)
    persona.value = { ...res.persona }
    msg.value = '已保存 ✓'
    emit('changed', { type: 'persona', persona: res.persona })
  } catch (e) {
    msg.value = e.message || '保存失败'
  } finally {
    saving.value = false
  }
}

async function removeMemory(id) {
  try {
    await api.deleteMemory(props.session.session_id, id)
    memories.value = memories.value.filter((m) => m.id !== id)
  } catch (e) {
    msg.value = e.message || '删除失败'
  }
}

async function resetSession() {
  if (!confirm('确定要重置这段关系吗？将清空记忆、关系和聊天记录，且不可恢复。')) return
  try {
    await api.resetSession(props.session.session_id)
    emit('changed', { type: 'reset' })
    emit('close')
  } catch (e) {
    msg.value = e.message || '重置失败'
  }
}

onMounted(async () => {
  loading.value = true
  msg.value = ''
  try {
    // 新会话（还没发过首条消息）在后端不存在，先幂等创建，否则详情/记忆接口会 404
    await api.createSession(props.session.session_id)
    await Promise.all([loadPersona(), loadMemories()])
  } catch (e) {
    msg.value = e.message || '加载失败'
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="mask" @click.self="emit('close')">
    <aside class="drawer">
      <header class="drawer-header">
        <span>设置</span>
        <button class="close" @click="emit('close')">✕</button>
      </header>

      <div class="tabs">
        <button :class="{ active: tab === 'persona' }" @click="tab = 'persona'">人设</button>
        <button :class="{ active: tab === 'memory' }" @click="tab = 'memory'">记忆</button>
      </div>

      <div class="drawer-body">
        <p v-if="loading" class="hint">加载中…</p>
        <p v-if="msg" class="msg">{{ msg }}</p>

        <template v-if="!loading && tab === 'persona' && persona">
          <label v-for="f in PERSONA_FIELDS" :key="f.key" class="form-field">
            <span class="label">{{ f.label }}</span>
            <textarea
              v-if="f.type === 'textarea'"
              v-model="persona[f.key]"
              rows="3"
              class="control"
            ></textarea>
            <input v-else v-model="persona[f.key]" :type="f.type" class="control" />
          </label>
          <button class="btn btn-primary full" :disabled="saving" @click="savePersona">
            {{ saving ? '保存中…' : '保存人设' }}
          </button>
        </template>

        <template v-if="!loading && tab === 'memory'">
          <div v-if="memories.length === 0" class="hint">还没有长期记忆。聊得越多，她记得越牢。</div>
          <div v-for="m in memories" :key="m.id" class="mem">
            <div class="mem-main">
              <p class="mem-content">{{ m.content }}</p>
              <span class="mem-meta">{{ m.category }} · 重要度 {{ Math.round(m.importance * 100) }}%</span>
            </div>
            <button class="mem-del" @click="removeMemory(m.id)">删除</button>
          </div>
        </template>
      </div>

      <footer class="drawer-footer">
        <button class="btn btn-danger full" @click="resetSession">重置关系与记忆</button>
      </footer>
    </aside>
  </div>
</template>

<style scoped>
.mask {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.3);
  z-index: 100;
  display: flex;
  justify-content: flex-end;
}
.drawer {
  width: 340px;
  height: 100%;
  background: #fff;
  display: flex;
  flex-direction: column;
  box-shadow: -8px 0 30px rgba(0, 0, 0, 0.15);
  animation: slidein 0.22s ease;
}
@keyframes slidein {
  from { transform: translateX(30px); opacity: 0; }
  to { transform: translateX(0); opacity: 1; }
}
.drawer-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 18px;
  font-size: 16px;
  font-weight: 600;
  border-bottom: 1px solid var(--wx-border);
}
.close {
  font-size: 16px;
  color: #888;
  width: 30px;
  height: 30px;
  border-radius: 6px;
}
.close:hover {
  background: #f2f2f2;
}
.tabs {
  display: flex;
  border-bottom: 1px solid var(--wx-border);
}
.tabs button {
  flex: 1;
  padding: 12px 0;
  font-size: 14px;
  color: var(--wx-text-sub);
  position: relative;
}
.tabs button.active {
  color: var(--wx-green);
  font-weight: 600;
}
.tabs button.active::after {
  content: '';
  position: absolute;
  left: 32%;
  right: 32%;
  bottom: -1px;
  height: 2px;
  background: var(--wx-green);
}
.drawer-body {
  flex: 1;
  overflow-y: auto;
  padding: 16px 18px;
}
.hint {
  color: #b2b2b2;
  font-size: 13px;
  text-align: center;
  padding: 20px 0;
}
.msg {
  color: var(--wx-green-dark);
  font-size: 13px;
  margin-bottom: 8px;
}
.form-field {
  display: block;
  margin-bottom: 12px;
}
.label {
  font-size: 13px;
  color: var(--wx-text-sub);
  display: block;
  margin-bottom: 5px;
}
.control {
  width: 100%;
  border: 1px solid var(--wx-border);
  border-radius: 6px;
  padding: 8px 10px;
  font-size: 14px;
  resize: vertical;
}
.control:focus {
  border-color: var(--wx-green);
}
.full {
  width: 100%;
  margin-top: 6px;
}
.mem {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px 0;
  border-bottom: 1px solid #f2f2f2;
}
.mem-main {
  flex: 1;
  min-width: 0;
}
.mem-content {
  font-size: 14px;
  line-height: 1.5;
  word-break: break-word;
}
.mem-meta {
  font-size: 12px;
  color: #b2b2b2;
  margin-top: 4px;
  display: inline-block;
}
.mem-del {
  color: #fa5151;
  font-size: 13px;
  flex-shrink: 0;
  padding: 4px 6px;
  border-radius: 4px;
}
.mem-del:hover {
  background: #fff0f0;
}
.drawer-footer {
  padding: 14px 18px;
  border-top: 1px solid var(--wx-border);
}
</style>
