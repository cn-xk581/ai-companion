<script setup>
import { onMounted, ref } from 'vue'
import { api, getAdminToken, setAdminToken } from '../api'
import { THEMES, setTheme, theme } from '../store/theme'

const emit = defineEmits(['close'])

const TABS = [
  { id: 'appearance', label: '外观' },
  { id: 'about', label: '关于' },
]

const tab = ref('appearance')
const adminToken = ref(getAdminToken())
const health = ref(null)
const msg = ref('')
const loading = ref(false)

async function loadHealth() {
  loading.value = true
  try {
    health.value = await api.health()
  } catch (e) {
    msg.value = e.message || '读取失败'
  } finally {
    loading.value = false
  }
}

function saveToken() {
  setAdminToken(adminToken.value.trim())
  msg.value = adminToken.value.trim() ? '运维令牌已保存在本机' : '已清除运维令牌'
}

onMounted(loadHealth)
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
            <p class="token__note">
              只存在浏览器本机；服务端需配置同名 ADMIN_TOKEN。能力开关在会话上方的「会话设置 → 能力」里。
            </p>
          </div>
        </template>

        <template v-else>
          <p class="lead">运行状态。人设 / 记忆 / 能力请点会话上方的「会话设置」。</p>
          <p v-if="loading" class="hint">读取中…</p>
          <dl v-else-if="health" class="about">
            <div><dt>应用</dt><dd>{{ health.app }}</dd></div>
            <div><dt>环境</dt><dd class="num">{{ health.environment }}</dd></div>
            <div><dt>检查点</dt><dd class="num">{{ health.checkpoint?.backend }}</dd></div>
            <div>
              <dt>模型</dt>
              <dd class="num">
                {{ health.llm?.model || '未配置' }}
                <template v-if="health.llm?.host"> · {{ health.llm.host }}</template>
              </dd>
            </div>
            <div><dt>Skill</dt><dd class="num">{{ health.skills?.total }} 个 / 在线 {{ health.skills?.enabled }}</dd></div>
            <div><dt>MCP</dt><dd class="num">{{ health.mcp?.tools }} 个工具</dd></div>
          </dl>
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
  width: min(400px, 100%);
  height: 100%;
  background: var(--panel);
  border-left: 1px solid var(--line);
  box-shadow: var(--shadow);
  animation: slide 0.22s var(--ease);
}
@keyframes slide { from { transform: translateX(22px); opacity: 0; } to { transform: none; opacity: 1; } }
.panel__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 18px 12px;
}
.panel__title { font-size: var(--step-2); font-weight: 500; }
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
.schemes { display: flex; flex-direction: column; gap: 10px; }
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
.scheme.is-on { box-shadow: inset 0 0 0 1px var(--accent); background: var(--panel-2); }
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
.swatches i { display: block; width: 18px; height: 18px; border-radius: 2px; }
.scheme__text { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
.scheme__name { font-size: var(--step-1); color: var(--text); }
.scheme__sub { font-size: var(--step--1); color: var(--text-2); }
.scheme__note { margin-top: 4px; font-size: var(--step--1); line-height: 1.6; color: var(--text-3); }
.scheme__check { flex-shrink: 0; width: 14px; color: var(--accent); text-align: right; }
.token { margin-top: 20px; padding-top: 16px; border-top: 1px solid var(--line); }
.token__row { display: flex; gap: 8px; }
.token__note { margin-top: 6px; font-size: var(--step--1); line-height: 1.7; color: var(--text-3); }
.msg { margin-bottom: 10px; font-size: var(--step-0); color: var(--accent); }
.hint { padding: 20px 4px; font-size: var(--step-0); color: var(--text-3); text-align: center; }
.about { display: flex; flex-direction: column; gap: 10px; }
.about > div { display: flex; justify-content: space-between; gap: 12px; padding-bottom: 8px; border-bottom: 1px solid var(--line-soft); }
.about dt { font-size: var(--step-0); color: var(--text-3); }
.about dd { margin: 0; font-size: var(--step-0); color: var(--text); }
</style>
