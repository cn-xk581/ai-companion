<script setup>
import { computed, onMounted, ref } from 'vue'
import { api, getAdminToken } from '../api'
import { stageLabel, moodLabel, moodEmoji } from '../constants'

const props = defineProps({
  sessionId: { type: String, required: true },
  detail: { type: Object, default: null },
})
const emit = defineEmits(['close', 'persona', 'reset', 'synced'])

const TABS = [
  { id: 'persona', label: '人设' },
  { id: 'memory', label: '记忆' },
  { id: 'skills', label: '能力' },
]

const tab = ref('persona')
const persona = ref({ ...(props.detail?.persona || {}) })
const memoryData = ref({ facts: [], episodes: [], summary: '', relation: null })
const skills = ref([])
const stats = ref(null)
const loading = ref(false)
const saving = ref(false)
const busy = ref('')
const msg = ref('')
const memQuery = ref('')
const relationForm = ref({ nickname: '', override: '' })

const stage = computed(() => props.detail?.stage || 'stranger')
const mood = computed(() => props.detail?.mood?.current_mood || 'neutral')

async function load() {
  loading.value = true
  msg.value = ''
  try {
    const [memRes, skillRes] = await Promise.all([
      api.getSessionMemory(props.sessionId),
      api.listSkills(),
    ])
    memoryData.value = {
      facts: memRes.facts || [],
      episodes: memRes.episodes || [],
      summary: memRes.summary || '',
      relation: memRes.relation || null,
    }
    if (memRes.relation) {
      relationForm.value = {
        nickname: memRes.relation.user_nickname || '',
        override: memRes.relation.user_persona_override || '',
      }
    }
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

async function editFact(fact) {
  const next = prompt('修改这条记忆', fact.content)
  if (next === null || !next.trim()) return
  try {
    await api.updateMemoryFact(fact.id, { value: next.trim() })
    fact.content = next.trim()
  } catch (e) {
    msg.value = e.message || '保存失败'
  }
}

async function toggleFact(fact) {
  try {
    await api.updateMemoryFact(fact.id, { pinned: !fact.pinned })
    fact.pinned = !fact.pinned
  } catch (e) {
    msg.value = e.message || '操作失败'
  }
}

async function deleteFact(fact) {
  try {
    await api.deleteMemoryFact(fact.id)
    memoryData.value.facts = memoryData.value.facts.filter((f) => f.id !== fact.id)
  } catch (e) {
    msg.value = e.message || '删除失败'
  }
}

async function toggleEpisode(episode) {
  try {
    await api.updateMemoryEpisode(episode.id, { pinned: !episode.pinned })
    episode.pinned = !episode.pinned
  } catch (e) {
    msg.value = e.message || '操作失败'
  }
}

async function deleteEpisode(episode) {
  try {
    await api.deleteMemoryEpisode(episode.id)
    memoryData.value.episodes = memoryData.value.episodes.filter((x) => x.id !== episode.id)
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

async function resetAll() {
  if (!confirm('彻底重置？会清空这段对话、与这个角色的共同记忆和事实档案，不可恢复。')) return
  try {
    await api.resetSession(props.sessionId, true, 'all')
    emit('reset')
    emit('close')
  } catch (e) {
    msg.value = e.message || '重置失败'
  }
}

async function resetSession() {
  if (!confirm('重置这段关系？会清空对话、关系进度和记忆，不可恢复。')) return
  try {
    await api.resetSession(props.sessionId, true, 'session')
    emit('reset')
    emit('close')
  } catch (e) {
    msg.value = e.message || '重置失败'
  }
}

async function syncCharacter() {
  if (!props.detail?.character?.can_sync) return
  busy.value = 'sync'
  msg.value = ''
  try {
    await api.syncSessionCharacter(props.sessionId)
    emit('synced')
  } catch (e) {
    msg.value = e.message || '同步失败'
  } finally {
    busy.value = ''
  }
}

let memTimer = null
function onMemSearch() {
  if (memTimer) clearTimeout(memTimer)
  memTimer = setTimeout(load, 300)
}

async function saveRelation() {
  try {
    await api.updateSessionRelation(props.sessionId, {
      user_nickname: relationForm.value.nickname,
      user_persona_override: relationForm.value.override,
    })
    msg.value = '关系设定已保存'
  } catch (e) {
    msg.value = e.message || '保存失败'
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

        <div v-if="detail?.character" class="ver">
          <div class="ver__text">
            角色版本 <b class="num">v{{ detail.character.version_no ?? '?' }}</b>
            <template v-if="detail.character.can_sync">
              · 最新 <b class="num">v{{ detail.character.latest_version_no }}</b>
            </template>
          </div>
          <button
            class="btn btn--quiet btn--sm"
            :disabled="!detail.character.can_sync || busy === 'sync'"
            @click="syncCharacter"
          >
            {{ busy === 'sync' ? '同步中…' : detail.character.can_sync ? '同步到最新版本' : '已是最新' }}
          </button>
        </div>

        <!-- 人设：只影响当前会话 -->
        <template v-if="tab === 'persona'">
          <div class="rel-box">
            <p class="field-label">TA 对你的称呼</p>
            <input v-model="relationForm.nickname" class="field" maxlength="32" placeholder="如：笨蛋、阿瑾" />
            <p class="field-label">你在这个角色面前的身份</p>
            <textarea v-model="relationForm.override" class="field" rows="2" maxlength="500" placeholder="如：我是你的青梅竹马 / 转校生" />
            <button class="btn btn--quiet btn--full" @click="saveRelation">保存关系设定</button>
          </div>
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
          <button class="btn btn--quiet btn--full" @click="resetSession">清空对话（保留记忆）</button>
          <button class="btn btn--danger btn--full reset" @click="resetAll">彻底重置（含羁绊档案）</button>
        </template>

        <!-- 记忆：关系档案 + 共同经历 + 会话摘要 -->
        <template v-else-if="tab === 'memory'">
          <p v-if="loading" class="hint">读取中…</p>
          <template v-else>
            <div class="search">
              <input v-model="memQuery" class="field" type="search" placeholder="搜索记忆…" @input="onMemSearch" />
            </div>

            <div v-if="memoryData.relation" class="ver">
              <div class="ver__text">
                好感 <b class="num">{{ memoryData.relation.affinity_score }}</b>
                · {{ memoryData.relation.relationship_stage }}
                · {{ memoryData.relation.total_turns }} 轮
                <template v-if="memoryData.relation.user_nickname">
                  · 称呼「{{ memoryData.relation.user_nickname }}」
                </template>
              </div>
            </div>

            <h3 class="mem-title">关于你</h3>
            <p v-if="!memoryData.facts.length" class="hint">还没有稳定的事实，聊到生日、忌口、称呼时会记下来。</p>
            <ul v-else class="mem-list">
              <li v-for="f in memoryData.facts" :key="f.id" class="mem">
                <p class="mem__text">{{ f.content }}</p>
                <p class="mem__meta">
                  <span>{{ f.category }}</span>
                  <span class="num">置信 {{ Math.round(f.confidence * 100) }}%</span>
                  <button class="mem__del" @click="editFact(f)">修改</button>
                  <button class="mem__del" @click="toggleFact(f)">{{ f.pinned ? '取消置顶' : '置顶' }}</button>
                  <button class="mem__del" @click="deleteFact(f)">删掉</button>
                </p>
              </li>
            </ul>

            <h3 class="mem-title">和 TA 的共同记忆</h3>
            <p v-if="!memoryData.episodes.length" class="hint">还没有值得回味的片段。</p>
            <ul v-else class="mem-list">
              <li v-for="e in memoryData.episodes" :key="e.id" class="mem">
                <p class="mem__text">{{ e.summary }}</p>
                <p class="mem__meta">
                  <span>{{ e.emotion_tag || e.kind }}</span>
                  <span class="num">重要度 {{ e.importance }}/10</span>
                  <button class="mem__del" @click="toggleEpisode(e)">{{ e.pinned ? '取消置顶' : '置顶' }}</button>
                  <button class="mem__del" @click="deleteEpisode(e)">删掉</button>
                </p>
              </li>
            </ul>

            <h3 class="mem-title">这段对话</h3>
            <p class="hint">{{ memoryData.summary || '等这段对话再长一点，会自动生成摘要。' }}</p>
          </template>
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
.rel-box {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 16px;
  padding: 10px 12px;
  border-radius: var(--r-tight);
  background: var(--panel-2);
  box-shadow: inset 0 0 0 1px var(--line);
}
.rel-box .btn { margin-top: 4px; }

.mem-title {
  margin: 18px 0 8px;
  font-size: var(--step-0);
  font-weight: 500;
  color: var(--text);
  letter-spacing: 0.02em;
}

.ver {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 14px;
  padding: 9px 12px;
  border-radius: var(--r-tight);
  background: var(--panel-2);
  box-shadow: inset 0 0 0 1px var(--line);
  font-size: var(--step--1);
  color: var(--text-2);
}
.ver__text b { color: var(--text); font-weight: 500; }

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
