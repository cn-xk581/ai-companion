<script setup>
import { computed, onMounted, ref } from 'vue'
import { api, getAdminToken } from '../api'
import { stageLabel, moodLabel, moodEmoji } from '../constants'

const props = defineProps({
  sessionId: { type: String, required: true },
  detail: { type: Object, default: null },
})
const emit = defineEmits(['close', 'persona', 'reset'])

const TABS = [
  { id: 'persona', label: '人设' },
  { id: 'memory', label: '记忆' },
  { id: 'skills', label: '能力' },
]

const tab = ref('persona')
const persona = ref({ ...(props.detail?.persona || {}) })
const memories = ref([])
const skills = ref([])
const stats = ref(null)
const loading = ref(false)
const saving = ref(false)
const busy = ref('')
const msg = ref('')

const stage = computed(() => props.detail?.stage || 'stranger')
const mood = computed(() => props.detail?.mood?.current_mood || 'neutral')

async function load() {
  loading.value = true
  msg.value = ''
  try {
    const [memRes, skillRes] = await Promise.all([api.listMemories(), api.listSkills()])
    memories.value = memRes.memories || []
    skills.value = skillRes.skills || []
    stats.value = skillRes.stats || null
  } catch (e) {
    msg.value = e.message || '读取失败'
  } finally {
    loading.value = false
  }
}

function splitList(value) {
  return (value || '')
    .split(/[,，、]/)
    .map((s) => s.trim())
    .filter(Boolean)
}

async function savePersona() {
  if (!persona.value) return
  saving.value = true
  msg.value = ''
  try {
    const payload = {
      ...persona.value,
      age: persona.value.age === '' || persona.value.age === null ? null : Number(persona.value.age),
    }
    const res = await api.updatePersona(props.sessionId, payload)
    persona.value = { ...res.persona }
    msg.value = '已保存，她下一句就按新人设说话'
    emit('persona', res.persona)
  } catch (e) {
    msg.value = e.message || '保存失败'
  } finally {
    saving.value = false
  }
}

async function removeMemory(memory) {
  try {
    await api.deleteMemory(memory.id)
    memories.value = memories.value.filter((m) => m.id !== memory.id)
  } catch (e) {
    msg.value = e.message || '删除失败'
  }
}

async function toggleSkill(skill) {
  if (!getAdminToken()) {
    msg.value = '能力开关是运维项：先在左下角「设置 → 外观」里填运维令牌'
    return
  }
  busy.value = skill.name
  msg.value = ''
  try {
    const res = await api.setSkill(skill.name, !skill.enabled)
    skill.enabled = res.enabled
    msg.value = `${skill.name} 已${res.enabled ? '上线' : '下线'}（全局生效）`
  } catch (e) {
    msg.value = e.status === 403 ? '运维令牌无效或未配置' : e.message || '操作失败'
  } finally {
    busy.value = ''
  }
}

async function resetSession() {
  if (!confirm('重置这段关系？会清空对话、关系进度和记忆，不可恢复。')) return
  try {
    await api.resetSession(props.sessionId, true)
    emit('reset')
    emit('close')
  } catch (e) {
    msg.value = e.message || '重置失败'
  }
}

onMounted(load)
</script>

<template>
  <div class="scrim" @click.self="emit('close')">
    <aside class="panel" role="dialog" aria-label="会话设置">
      <header class="panel__head">
        <div class="who">
          <p class="who__name serif">{{ detail?.persona?.name || '小柔' }}</p>
          <p class="who__meta">
            <span>{{ stageLabel(stage) }}</span>
            <i class="dot" aria-hidden="true"></i>
            <span>{{ moodEmoji(mood) }} {{ moodLabel(mood) }}</span>
          </p>
        </div>
        <button class="icon-btn" aria-label="关闭" @click="emit('close')">✕</button>
      </header>

      <nav class="tabs">
        <button
          v-for="t in TABS"
          :key="t.id"
          class="tab"
          :class="{ 'is-on': tab === t.id }"
          @click="tab = t.id"
        >
          {{ t.label }}
        </button>
      </nav>

      <div class="panel__body">
        <p v-if="msg" class="msg">{{ msg }}</p>

        <!-- 人设：只影响当前会话 -->
        <template v-if="tab === 'persona'">
          <p class="lead">这些只对当前这段关系生效，改完下一句就按新人设说话。</p>
          <label class="row">
            <span class="field-label">名字</span>
            <input v-model="persona.name" class="field" maxlength="12" />
          </label>
          <label class="row">
            <span class="field-label">年龄</span>
            <input v-model.number="persona.age" class="field num" type="number" min="18" max="99" />
          </label>
          <label class="row">
            <span class="field-label">性格</span>
            <textarea v-model="persona.personality" class="field" rows="2" maxlength="300"></textarea>
          </label>
          <label class="row">
            <span class="field-label">说话风格</span>
            <textarea v-model="persona.speaking_style" class="field" rows="2" maxlength="300"></textarea>
          </label>
          <label class="row">
            <span class="field-label">背景</span>
            <textarea v-model="persona.background" class="field" rows="2" maxlength="400"></textarea>
          </label>
          <label class="row">
            <span class="field-label">喜欢（顿号或逗号分隔）</span>
            <input
              class="field"
              :value="(persona.likes || []).join('、')"
              @input="persona.likes = splitList($event.target.value)"
            />
          </label>
          <label class="row">
            <span class="field-label">不喜欢</span>
            <input
              class="field"
              :value="(persona.dislikes || []).join('、')"
              @input="persona.dislikes = splitList($event.target.value)"
            />
          </label>
          <label class="row">
            <span class="field-label">禁区（她会避开的话题）</span>
            <input
              class="field"
              :value="(persona.boundaries || []).join('、')"
              @input="persona.boundaries = splitList($event.target.value)"
            />
          </label>
          <button class="btn btn--primary btn--full" :disabled="saving" @click="savePersona">
            {{ saving ? '保存中…' : '保存人设' }}
          </button>
          <button class="btn btn--danger btn--full reset" @click="resetSession">重置这段关系与记忆</button>
        </template>

        <!-- 记忆：跨会话积累，这里只是查看/删除 -->
        <template v-else-if="tab === 'memory'">
          <p class="lead">她记住的关于你的事，跨会话共享；删掉就不会再被提起。</p>
          <p v-if="loading" class="hint">读取中…</p>
          <p v-else-if="!memories.length" class="hint">她还没记住什么。聊得越多，这里越厚。</p>
          <ul v-else class="mem-list">
            <li v-for="m in memories" :key="m.id" class="mem">
              <p class="mem__text">{{ m.content }}</p>
              <p class="mem__meta">
                <span>{{ m.category }}</span>
                <span class="num">重要度 {{ Math.round(m.importance * 100) }}%</span>
                <button class="mem__del" @click="removeMemory(m)">删掉</button>
              </p>
            </li>
          </ul>
        </template>

        <!-- 能力：全局开关，说明清楚影响范围 -->
        <template v-else>
          <p class="lead">
            她当前可用的能力。开关是**全局**的（会影响所有会话），属于运维操作。
          </p>
          <div v-if="stats" class="skill-head num">
            共 {{ stats.total }} 个 · 在线 {{ stats.enabled }} · rev {{ stats.revision }}
          </div>
          <ul class="skill-list">
            <li v-for="s in skills" :key="s.name" class="skill">
              <div class="skill__main">
                <p class="skill__name">{{ s.name }}</p>
                <p class="skill__desc">{{ s.description }}</p>
                <p class="skill__meta num">
                  v{{ s.version }} · {{ s.tags.join('/') }}
                  <template v-if="s.mcp_tools.length"> · 依赖 {{ s.mcp_tools.join(', ') }}</template>
                </p>
              </div>
              <button
                class="switch"
                :class="{ 'is-on': s.enabled }"
                :disabled="busy === s.name"
                @click="toggleSkill(s)"
              >
                {{ s.enabled ? '在线' : '下线' }}
              </button>
            </li>
          </ul>
        </template>
      </div>
    </aside>
  </div>
</template>

<style scoped>
.scrim {
  position: fixed;
  inset: 0;
  z-index: 100;
  display: flex;
  justify-content: flex-end;
  background: var(--scrim);
  animation: fade 0.16s var(--ease);
}
@keyframes fade { from { opacity: 0; } to { opacity: 1; } }
.panel {
  display: flex;
  flex-direction: column;
  width: min(420px, 100%);
  height: 100%;
  background: var(--panel);
  border-left: 1px solid var(--line);
  box-shadow: var(--shadow);
  animation: slide 0.22s var(--ease);
}
@keyframes slide { from { transform: translateX(22px); opacity: 0; } to { transform: none; opacity: 1; } }
.panel__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  padding: 16px 18px 12px;
}
.who__name { font-size: var(--step-2); font-weight: 500; color: var(--text); }
.who__meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 3px;
  font-size: var(--step--1);
  color: var(--text-2);
}
.dot { width: 1px; height: 10px; background: var(--line); }
.tabs {
  display: flex;
  gap: 14px;
  padding: 0 18px;
  border-bottom: 1px solid var(--line);
}
.tab {
  padding: 8px 0 10px;
  margin-bottom: -1px;
  font-size: var(--step-0);
  color: var(--text-2);
  border-bottom: 1px solid transparent;
}
.tab:hover { color: var(--text); }
.tab.is-on { color: var(--text); border-bottom-color: var(--accent); }
.panel__body { flex: 1; overflow-y: auto; padding: 16px 18px 26px; }
.lead { margin-bottom: 14px; font-size: var(--step-0); line-height: 1.7; color: var(--text-2); }
.row { display: block; margin-bottom: 12px; }
.full { width: 100%; margin-top: 4px; }
.reset { margin-top: 10px; }
.msg { margin-bottom: 10px; font-size: var(--step-0); color: var(--accent); }
.hint { padding: 20px 4px; font-size: var(--step-0); color: var(--text-3); text-align: center; line-height: 1.8; }
.mem-list { list-style: none; }
.mem { padding: 10px 0; border-bottom: 1px solid var(--line-soft); }
.mem__text { font-size: var(--step-0); line-height: 1.7; color: var(--text); }
.mem__meta {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 4px;
  font-size: var(--step--1);
  color: var(--text-3);
}
.mem__del { margin-left: auto; padding: 2px 6px; border-radius: var(--r-tight); font-size: var(--step--1); color: var(--text-3); }
.mem__del:hover { color: var(--bad); background: color-mix(in srgb, var(--bad) 10%, transparent); }
.skill-head { margin-bottom: 8px; font-size: var(--step--1); color: var(--text-3); }
.skill-list { list-style: none; }
.skill {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 10px 0;
  border-bottom: 1px solid var(--line-soft);
}
.skill__main { flex: 1; min-width: 0; }
.skill__name { font-size: var(--step-0); color: var(--text); word-break: break-all; }
.skill__desc { margin-top: 2px; font-size: var(--step--1); line-height: 1.6; color: var(--text-2); }
.skill__meta { margin-top: 3px; font-size: var(--step--1); color: var(--text-3); }
.switch {
  flex-shrink: 0;
  height: 26px;
  padding: 0 10px;
  border-radius: 999px;
  font-size: var(--step--1);
  color: var(--text-3);
  box-shadow: inset 0 0 0 1px var(--line);
}
.switch.is-on {
  color: var(--accent);
  background: var(--accent-soft);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent) 45%, transparent);
}
</style>
