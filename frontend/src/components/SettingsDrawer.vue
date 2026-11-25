<script setup>
import { computed, onMounted, ref } from 'vue'
import { api, getAdminToken, setAdminToken } from '../api'
import { THEMES, setTheme, theme } from '../store/theme'

const props = defineProps({
  sessionId: { type: String, default: '' },
  detail: { type: Object, default: null },
})
const emit = defineEmits(['close', 'persona', 'reset'])

const tab = ref('appearance')
const adminToken = ref(getAdminToken())
const persona = ref(null)
const memories = ref([])
const skills = ref([])
const skillStats = ref(null)
const health = ref(null)
const loading = ref(false)
const saving = ref(false)
const msg = ref('')
const busy = ref('')

const TABS = [
  { id: 'appearance', label: '外观' },
  { id: 'persona', label: '人设' },
  { id: 'memory', label: '记忆' },
  { id: 'skills', label: '能力' },
]

const hasSession = computed(() => !!props.sessionId)

async function loadAll() {
  loading.value = true
  msg.value = ''
  try {
    const tasks = [api.listSkills(), api.health()]
    if (hasSession.value) tasks.push(api.listMemories())
    const [skillRes, healthRes, memoryRes] = await Promise.all(tasks)
    skills.value = skillRes.skills || []
    skillStats.value = skillRes.stats || null
    health.value = healthRes
    if (memoryRes) memories.value = memoryRes.memories || []
    persona.value = { ...(props.detail?.persona || {}) }
  } catch (e) {
    msg.value = e.message || '读取失败'
  } finally {
    loading.value = false
  }
}

async function savePersona() {
  if (!hasSession.value || !persona.value) return
  saving.value = true
  msg.value = ''
  try {
    const payload = {
      ...persona.value,
      age: persona.value.age === '' || persona.value.age === null ? null : Number(persona.value.age),
    }
    const res = await api.updatePersona(props.sessionId, payload)
    persona.value = { ...res.persona }
    msg.value = '已保存，她下一句就会按新人设说话'
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

async function resetSession() {
  if (!hasSession.value) return
  if (!confirm('重置这段关系？会清空对话、关系进度和她的记忆，不可恢复。')) return
  try {
    await api.resetSession(props.sessionId, true)
    msg.value = '已重置'
    emit('reset')
    emit('close')
  } catch (e) {
    msg.value = e.message || '重置失败'
  }
}

async function toggleSkill(skill) {
  if (!adminToken.value) {
    msg.value = '先在「外观」页填入运维令牌'
    tab.value = 'appearance'
    return
  }
  busy.value = skill.name
  msg.value = ''
  try {
    const res = await api.setSkill(skill.name, !skill.enabled)
    skill.enabled = res.enabled
    msg.value = `${skill.name} 已${res.enabled ? '上线' : '下线'}`
  } catch (e) {
    msg.value = e.status === 403 ? '运维令牌无效或未配置' : e.message || '操作失败'
  } finally {
    busy.value = ''
  }
}

function saveToken() {
  setAdminToken(adminToken.value.trim())
  msg.value = adminToken.value.trim() ? '运维令牌已保存在本机' : '已清除运维令牌'
}

function splitList(value) {
  return (value || '')
    .split(/[,，、]/)
    .map((s) => s.trim())
    .filter(Boolean)
}

onMounted(loadAll)
</script>

<template>
  <div class="scrim" @click.self="emit('close')">
    <aside class="panel" role="dialog" aria-label="设置">
      <header class="panel__head">
        <h2 class="panel__title serif">设置</h2>
        <button class="icon-btn" aria-label="关闭设置" @click="emit('close')">✕</button>
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
        <p v-if="loading" class="hint">读取中…</p>

        <!-- 外观 -->
        <template v-if="tab === 'appearance'">
          <p class="lead">同一段关系，两种光线。选择只保存在这台设备。</p>
          <div class="schemes">
            <button
              v-for="t in THEMES"
              :key="t.id"
              class="scheme"
              :class="{ 'is-on': theme.id === t.id }"
              :aria-pressed="theme.id === t.id"
              @click="setTheme(t.id)"
            >
              <span class="swatches" aria-hidden="true">
                <i v-for="c in t.swatches" :key="c" :style="{ background: c }"></i>
              </span>
              <span class="scheme__text">
                <span class="scheme__name serif">{{ t.name }}</span>
                <span class="scheme__sub">{{ t.sub }}</span>
                <span class="scheme__note">{{ t.note }}</span>
              </span>
              <span class="scheme__check">{{ theme.id === t.id ? '✓' : '' }}</span>
            </button>
          </div>

          <div class="token">
            <span class="field-label">运维令牌（能力上下线用）</span>
            <div class="token__row">
              <input v-model="adminToken" class="field" type="password" placeholder="X-Admin-Token" />
              <button class="btn btn--quiet btn--sm" @click="saveToken">保存</button>
            </div>
            <p class="token__note">只存在浏览器本机；服务端需配置同名 ADMIN_TOKEN。</p>
          </div>
        </template>

        <!-- 人设 -->
        <template v-else-if="tab === 'persona'">
          <template v-if="hasSession && persona">
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
            <button class="btn btn--danger btn--full reset" @click="resetSession">重置关系与记忆</button>
            <p class="foot-note">改完下一句就生效；重置会清空对话、关系进度和长期记忆。</p>
          </template>
          <p v-else class="hint">先打开一段对话，再改她的人设。</p>
        </template>

        <!-- 记忆 -->
        <template v-else-if="tab === 'memory'">
          <p v-if="!memories.length" class="hint">她还没记住什么。聊得越多，这里越厚。</p>
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

        <!-- 能力 -->
        <template v-else>
          <div v-if="skillStats" class="skill-head num">
            共 {{ skillStats.total }} 个 · 在线 {{ skillStats.enabled }} · rev {{ skillStats.revision }}
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
          <p v-if="health" class="about num">
            检查点 {{ health.checkpoint.backend }} · MCP {{ health.mcp.tools }} 个工具 ·
            同参合并 {{ health.singleflight.enabled ? '开' : '关' }}
          </p>
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
@keyframes fade {
  from { opacity: 0; }
  to { opacity: 1; }
}
.panel {
  display: flex;
  flex-direction: column;
  width: min(400px, 100%);
  height: 100%;
  background: var(--panel);
  border-left: 1px solid var(--line);
  box-shadow: var(--shadow);
  animation: slide 0.22s var(--ease);
}
@keyframes slide {
  from { transform: translateX(22px); opacity: 0; }
  to { transform: none; opacity: 1; }
}
.panel__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 18px 12px;
}
.panel__title {
  font-size: var(--step-2);
  font-weight: 500;
}
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
.tab.is-on {
  color: var(--text);
  border-bottom-color: var(--accent);
}
.panel__body {
  flex: 1;
  overflow-y: auto;
  padding: 16px 18px 26px;
}
.lead {
  margin-bottom: 14px;
  font-size: var(--step-0);
  line-height: 1.7;
  color: var(--text-2);
}
.schemes {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.scheme {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  width: 100%;
  padding: 12px;
  text-align: left;
  border-radius: var(--r-box);
  background: var(--raise);
  box-shadow: inset 0 0 0 1px var(--line);
}
.scheme:hover { background: var(--panel-2); }
.scheme.is-on {
  box-shadow: inset 0 0 0 1px var(--accent);
  background: var(--panel-2);
}
.swatches {
  flex-shrink: 0;
  display: grid;
  grid-template-columns: repeat(2, 18px);
  gap: 2px;
  padding: 3px;
  border-radius: var(--r-tight);
  background: var(--bg-deep);
  box-shadow: inset 0 0 0 1px var(--line);
}
.swatches i {
  display: block;
  width: 18px;
  height: 18px;
  border-radius: 2px;
}
.scheme__text {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.scheme__name { font-size: var(--step-1); color: var(--text); }
.scheme__sub { font-size: var(--step--1); color: var(--text-2); }
.scheme__note {
  margin-top: 4px;
  font-size: var(--step--1);
  line-height: 1.6;
  color: var(--text-3);
}
.scheme__check {
  flex-shrink: 0;
  width: 14px;
  color: var(--accent);
  text-align: right;
}
.token {
  margin-top: 20px;
  padding-top: 16px;
  border-top: 1px solid var(--line);
}
.token__row { display: flex; gap: 8px; }
.token__note {
  margin-top: 6px;
  font-size: var(--step--1);
  line-height: 1.7;
  color: var(--text-3);
}
.row { display: block; margin-bottom: 12px; }
.full { width: 100%; margin-top: 4px; }
.reset { margin-top: 10px; }
.foot-note {
  margin-top: 10px;
  font-size: var(--step--1);
  line-height: 1.7;
  color: var(--text-3);
}
.hint {
  padding: 20px 4px;
  font-size: var(--step-0);
  color: var(--text-3);
  text-align: center;
  line-height: 1.8;
}
.msg {
  margin-bottom: 10px;
  font-size: var(--step-0);
  color: var(--accent);
}
.mem-list { list-style: none; }
.mem {
  padding: 10px 0;
  border-bottom: 1px solid var(--line-soft);
}
.mem__text { font-size: var(--step-0); line-height: 1.7; color: var(--text); }
.mem__meta {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 4px;
  font-size: var(--step--1);
  color: var(--text-3);
}
.mem__del {
  margin-left: auto;
  padding: 2px 6px;
  border-radius: var(--r-tight);
  font-size: var(--step--1);
  color: var(--text-3);
}
.mem__del:hover { color: var(--bad); background: color-mix(in srgb, var(--bad) 10%, transparent); }
.skill-head {
  margin-bottom: 8px;
  font-size: var(--step--1);
  color: var(--text-3);
}
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
.skill__desc {
  margin-top: 2px;
  font-size: var(--step--1);
  line-height: 1.6;
  color: var(--text-2);
}
.skill__meta {
  margin-top: 3px;
  font-size: var(--step--1);
  color: var(--text-3);
}
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
.about {
  margin-top: 14px;
  font-size: var(--step--1);
  line-height: 1.9;
  color: var(--text-3);
}
</style>
