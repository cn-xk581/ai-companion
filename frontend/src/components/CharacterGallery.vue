<script setup>
import { onMounted, ref } from 'vue'
import { api, downloadCharacterFile, importCharacter } from '../api'
import { PRESETS } from '../presets'
import CharacterEditor from './CharacterEditor.vue'

const props = defineProps({
  creating: { type: String, default: '' },
})
const emit = defineEmits(['start', 'start-preset'])

const mine = ref([])
const loading = ref(false)
const error = ref('')
const busy = ref('')
const fileInput = ref(null)
const editing = ref(null)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await api.myCharacters()
    mine.value = res.characters || []
  } catch (e) {
    error.value = e.message || '读取角色失败'
  } finally {
    loading.value = false
  }
}

function pick() {
  fileInput.value?.click()
}

async function onFile(ev) {
  const file = ev.target.files?.[0]
  ev.target.value = ''
  if (!file) return
  error.value = ''
  busy.value = 'import'
  try {
    await importCharacter(file)
    await load()
  } catch (e) {
    error.value = e.message || '导入失败'
  } finally {
    busy.value = ''
  }
}

function start(c) {
  if (props.creating || busy.value) return
  emit('start', c)
}

async function download(c, format) {
  error.value = ''
  busy.value = `${c.character_id}-${format}`
  try {
    await downloadCharacterFile(c.character_id, format)
  } catch (e) {
    error.value = e.message || '导出失败'
  } finally {
    busy.value = ''
  }
}

async function remove(c) {
  if (!confirm(`删除「${c.name}」？已开的会话不受影响，但之后不能再新建。`)) return
  error.value = ''
  busy.value = `${c.character_id}-del`
  try {
    await api.deleteCharacter(c.character_id)
    await load()
  } catch (e) {
    error.value = e.message || '删除失败'
  } finally {
    busy.value = ''
  }
}

function edit(c) {
  editing.value = c.character_id
}

async function onEdited() {
  editing.value = null
  await load()
}

function tagList(c) {
  return (c.tags || []).slice(0, 4)
}

onMounted(load)
</script>

<template>
  <section class="gallery">
    <header class="gallery__head">
      <div>
        <h1 class="gallery__title serif">人物</h1>
        <p class="gallery__sub">
          导入酒馆角色卡（PNG / JSON）开始一段新关系。每个角色独立版本，改动不会影响已有会话。
        </p>
      </div>
      <div class="gallery__actions">
        <button class="btn btn--primary btn--sm" :disabled="busy === 'import'" @click="pick">
          {{ busy === 'import' ? '导入中…' : '导入角色卡' }}
        </button>
        <input ref="fileInput" type="file" accept=".png,.json,image/png,application/json" hidden @change="onFile" />
      </div>
    </header>

    <p v-if="error" class="error">{{ error }}</p>

    <h2 class="section-title">
      我的角色
      <span class="section-count num">{{ mine.length }}</span>
    </h2>
    <p v-if="loading" class="hint">读取中…</p>
    <p v-else-if="!mine.length" class="hint">
      还没有导入角色。点右上角「导入角色卡」，支持 SillyTavern 的 PNG 卡和 V1/V2/V3 JSON。
    </p>
    <div v-else class="grid">
      <article v-for="c in mine" :key="c.character_id" class="card">
        <span class="card__portrait">
          <img v-if="c.cover_url" :src="c.cover_url" alt="" />
          <span v-else class="card__initial serif">{{ (c.name || '人').slice(0, 1) }}</span>
          <span class="card__tags">
            <span v-for="t in tagList(c)" :key="t" class="card__tag">{{ t }}</span>
          </span>
        </span>
        <div class="card__body">
          <div class="card__top">
            <span class="card__name serif" :title="c.name">{{ c.name }}</span>
            <span class="card__meta num">{{ c.card_spec === 'chara_card_v3' ? 'V3' : 'V2' }}</span>
          </div>
          <p class="card__summary">{{ c.summary || '（无简介）' }}</p>
          <div class="card__actions">
            <button class="mini mini--primary" :disabled="!!creating" @click="start(c)">
              {{ creating === 'c' + c.character_id ? '开启中…' : '开始聊天' }}
            </button>
            <button class="mini" :disabled="busy !== ''" @click="edit(c)">编辑</button>
            <button class="mini" :disabled="busy !== ''" @click="download(c, 'png')">PNG</button>
            <button class="mini" :disabled="busy !== ''" @click="download(c, 'json')">JSON</button>
            <button class="mini mini--danger" :disabled="busy !== ''" @click="remove(c)">删除</button>
          </div>
        </div>
      </article>
    </div>

    <h2 class="section-title section-title--sub">
      示例角色
      <span class="section-count num">{{ PRESETS.length }}</span>
    </h2>
    <p class="hint hint--small">先用这些人设试试；点开就是一段新会话。</p>
    <div class="grid grid--preset">
      <button
        v-for="p in PRESETS"
        :key="p.id"
        class="preset"
        :disabled="!!creating"
        @click="emit('start-preset', p)"
      >
        <span class="preset__portrait" :style="{ background: p.tint }">
          <span class="preset__initial serif">{{ p.name.slice(0, 1) }}</span>
        </span>
        <span class="preset__text">
          <span class="preset__name serif">{{ p.name }}</span>
          <span class="preset__tagline">{{ p.tagline }}</span>
        </span>
      </button>
    </div>
    <CharacterEditor
    v-if="editing"
    :character-id="editing"
    @close="editing = null"
    @saved="onEdited"
  />
  </section>
</template>

<style scoped>
.gallery {
  flex: 1;
  overflow-y: auto;
  padding: 26px clamp(18px, 3vw, 40px) 44px;
}
.gallery__head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 18px;
}
.gallery__title { font-size: var(--step-3); color: var(--text); }
.gallery__sub {
  margin-top: 6px;
  max-width: 50ch;
  font-size: var(--step-0);
  line-height: 1.7;
  color: var(--text-3);
}
.gallery__actions { flex-shrink: 0; }
.error {
  margin-bottom: 12px;
  font-size: var(--step-0);
  color: var(--bad);
}
.section-title {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin: 6px 0 10px;
  font-size: var(--step-1);
  font-weight: 500;
  color: var(--text);
}
.section-title--sub { margin-top: 30px; }
.section-count { font-size: var(--step--1); color: var(--text-3); }
.hint {
  padding: 18px 2px;
  font-size: var(--step-0);
  line-height: 1.8;
  color: var(--text-3);
}
.hint--small { padding: 4px 2px 10px; font-size: var(--step--1); }
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(230px, 1fr));
  gap: 14px;
}
.card {
  display: flex;
  flex-direction: column;
  border-radius: var(--r-box);
  overflow: hidden;
  background: var(--panel-2);
  box-shadow: inset 0 0 0 1px var(--line);
}
.card__portrait {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  height: 132px;
  background: var(--bg-deep);
}
.card__portrait img { width: 100%; height: 100%; object-fit: cover; }
.card__initial {
  font-size: 44px;
  color: var(--paper-ink);
  opacity: 0.9;
}
.card__tags {
  position: absolute;
  left: 8px;
  bottom: 8px;
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}
.card__tag {
  padding: 1px 7px;
  border-radius: 999px;
  font-size: 10px;
  color: rgba(255, 255, 255, 0.92);
  background: rgba(0, 0, 0, 0.35);
}
.card__body { padding: 10px 12px 12px; }
.card__top { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
.card__name {
  font-size: var(--step-1);
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.card__meta { flex-shrink: 0; font-size: 10px; color: var(--text-3); }
.card__summary {
  margin-top: 3px;
  font-size: var(--step--1);
  line-height: 1.6;
  color: var(--text-2);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  min-height: 2.9em;
}
.card__actions { display: flex; gap: 6px; margin-top: 8px; flex-wrap: wrap; }
.mini {
  height: 24px;
  padding: 0 9px;
  border-radius: var(--r-tight);
  font-size: var(--step--1);
  color: var(--text-2);
  box-shadow: inset 0 0 0 1px var(--line);
}
.mini:hover { color: var(--text); background: var(--line-soft); }
.mini--primary {
  color: var(--accent);
  background: var(--accent-soft);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent) 45%, transparent);
}
.mini--danger:hover { color: var(--bad); }
.mini:disabled { opacity: 0.5; }

.grid--preset { grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); }
.preset {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px;
  text-align: left;
  border-radius: var(--r-box);
  background: var(--panel-2);
  box-shadow: inset 0 0 0 1px var(--line);
}
.preset:hover { box-shadow: inset 0 0 0 1px var(--accent); }
.preset:disabled { opacity: 0.6; }
.preset__portrait {
  flex-shrink: 0;
  width: 38px;
  height: 38px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 8px;
}
.preset__initial { font-size: 18px; color: rgba(255, 255, 255, 0.92); }
.preset__text { min-width: 0; display: flex; flex-direction: column; gap: 1px; }
.preset__name { font-size: var(--step-0); color: var(--text); }
.preset__tagline {
  font-size: var(--step--1);
  color: var(--text-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
