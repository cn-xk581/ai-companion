<script setup>
import { computed, ref } from 'vue'
import { formatDuration } from '../utils'

const props = defineProps({
  events: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
})
const emit = defineEmits(['close'])

const filter = ref('all')

const FILTERS = [
  { id: 'all', label: '全部' },
  { id: 'llm', label: '模型' },
  { id: 'skill', label: 'Skill' },
  { id: 'mcp', label: 'MCP' },
  { id: 'decision', label: '决策' },
]

const KIND_LABELS = { llm: 'LLM', skill: 'Skill', mcp: 'MCP', graph: '图' }

const rows = computed(() => {
  const list = props.events || []
  return list
    .filter((e) => {
      if (filter.value === 'all') return true
      if (filter.value === 'decision') return e.type === 'decision'
      return e.type === 'span' && e.kind === filter.value
    })
    .map((e) => {
      const isDecision = e.type === 'decision'
      return {
        ...e,
        isDecision,
        label: isDecision ? e.action : e.name,
        kindLabel: isDecision ? '决策' : KIND_LABELS[e.kind] || e.kind,
        tone: e.status === 'error' ? 'bad' : isDecision ? 'mute' : 'ok',
        detail: isDecision ? e.payload : e.attrs,
      }
    })
})

const stats = computed(() => {
  const list = props.events || []
  const spans = list.filter((e) => e.type === 'span')
  const slow = spans.reduce((m, e) => Math.max(m, e.duration_ms || 0), 0)
  const errors = spans.filter((e) => e.status === 'error').length
  return { total: list.length, spans: spans.length, slow, errors }
})

function pretty(value) {
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}
</script>

<template>
  <div class="scrim" @click.self="emit('close')">
    <aside class="panel" role="dialog" aria-label="执行追踪">
      <header class="panel__head">
        <div>
          <h2 class="panel__title serif">执行追踪</h2>
          <p class="panel__sub num">
            {{ stats.total }} 条 · 最慢 {{ formatDuration(stats.slow) }}
            <span v-if="stats.errors" class="panel__err">· 失败 {{ stats.errors }}</span>
          </p>
        </div>
        <button class="icon-btn" aria-label="关闭追踪" @click="emit('close')">✕</button>
      </header>

      <nav class="filters">
        <button
          v-for="f in FILTERS"
          :key="f.id"
          class="filter"
          :class="{ 'is-on': filter === f.id }"
          @click="filter = f.id"
        >
          {{ f.label }}
        </button>
      </nav>

      <div class="panel__body">
        <p v-if="loading" class="hint">正在读取事件流…</p>
        <p v-else-if="error" class="hint hint--error">{{ error }}</p>
        <p v-else-if="!rows.length" class="hint">这个过滤条件下没有事件。</p>

        <ol v-else class="tl">
          <li v-for="row in rows" :key="row.id || row.span_id" class="tl__item">
            <span class="tl__dot" :data-tone="row.tone"></span>
            <div class="tl__body">
              <div class="tl__line">
                <span class="tl__kind" :data-kind="row.isDecision ? 'decision' : row.kind">{{ row.kindLabel }}</span>
                <span class="tl__name">{{ row.label }}</span>
                <span v-if="row.duration_ms !== undefined && row.duration_ms !== null" class="tl__ms num">
                  {{ formatDuration(row.duration_ms) }}
                </span>
              </div>
              <details v-if="row.detail" class="tl__detail">
                <summary>参数与结果</summary>
                <pre>{{ pretty(row.detail) }}</pre>
              </details>
              <p v-if="row.error" class="tl__error num">
                {{ row.error.kind }} · {{ row.error.message }}
              </p>
            </div>
          </li>
        </ol>
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
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}

.panel {
  display: flex;
  flex-direction: column;
  width: min(460px, 100%);
  height: 100%;
  background: var(--panel);
  border-left: 1px solid var(--line);
  box-shadow: var(--shadow);
  animation: slide 0.22s var(--ease);
}
@keyframes slide {
  from {
    transform: translateX(22px);
    opacity: 0;
  }
  to {
    transform: none;
    opacity: 1;
  }
}

.panel__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  padding: 16px 18px 10px;
}
.panel__title {
  font-size: var(--step-2);
  font-weight: 500;
}
.panel__sub {
  margin-top: 2px;
  font-size: var(--step--1);
  color: var(--text-3);
}
.panel__err {
  color: var(--bad);
}

.filters {
  display: flex;
  gap: 4px;
  padding: 0 14px 10px;
  border-bottom: 1px solid var(--line);
  flex-wrap: wrap;
}
.filter {
  padding: 4px 10px;
  border-radius: 999px;
  font-size: var(--step--1);
  color: var(--text-2);
  box-shadow: inset 0 0 0 1px var(--line);
}
.filter:hover {
  color: var(--text);
}
.filter.is-on {
  color: var(--accent-ink);
  background: var(--accent);
  box-shadow: none;
}

.panel__body {
  flex: 1;
  overflow-y: auto;
  padding: 14px 18px 30px;
}

.hint {
  padding: 24px 4px;
  font-size: var(--step-0);
  color: var(--text-3);
  text-align: center;
}
.hint--error {
  color: var(--bad);
}

.tl {
  list-style: none;
  position: relative;
  padding-left: 16px;
}
.tl::before {
  content: '';
  position: absolute;
  left: 4px;
  top: 6px;
  bottom: 6px;
  width: 1px;
  background: var(--line);
}

.tl__item {
  position: relative;
  padding: 6px 0 10px;
}
.tl__dot {
  position: absolute;
  left: -15px;
  top: 11px;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--line);
  box-shadow: 0 0 0 2px var(--panel);
}
.tl__dot[data-tone='ok'] {
  background: var(--ok);
}
.tl__dot[data-tone='bad'] {
  background: var(--bad);
}
.tl__dot[data-tone='mute'] {
  background: var(--text-3);
}

.tl__line {
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
}
.tl__kind {
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 10.5px;
  letter-spacing: 0.04em;
  color: var(--text-2);
  background: var(--line-soft);
}
.tl__kind[data-kind='llm'] {
  color: var(--accent);
  background: var(--accent-soft);
}
.tl__name {
  font-size: var(--step-0);
  color: var(--text);
  word-break: break-all;
}
.tl__ms {
  font-size: var(--step--1);
  color: var(--text-3);
  margin-left: auto;
}

.tl__detail {
  margin-top: 4px;
}
.tl__detail summary {
  cursor: pointer;
  font-size: var(--step--1);
  color: var(--text-3);
}
.tl__detail pre {
  margin-top: 6px;
  max-height: 220px;
  overflow: auto;
  padding: 8px 10px;
  border-radius: var(--r-tight);
  background: var(--raise);
  font-size: 11.5px;
  line-height: 1.6;
  color: var(--text-2);
  white-space: pre-wrap;
  word-break: break-all;
}

.tl__error {
  margin-top: 4px;
  font-size: var(--step--1);
  color: var(--bad);
}
</style>
