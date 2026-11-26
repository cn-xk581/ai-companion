<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'

const props = defineProps({
  characterId: { type: [Number, String], required: true },
})
const emit = defineEmits(['close', 'saved'])

const loading = ref(true)
const saving = ref(false)
const error = ref('')
const changeNote = ref('')
const form = ref({
  name: '',
  nickname: '',
  description: '',
  personality: '',
  scenario: '',
  system_prompt: '',
  post_history_instructions: '',
  greeting: '',
  tags: '',
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    const detail = await api.getCharacter(props.characterId)
    const persona = detail.version?.persona || {}
    form.value = {
      name: persona.name || detail.character.name || '',
      nickname: persona.nickname || detail.character.nickname || '',
      description: persona.description || '',
      personality: persona.personality || '',
      scenario: persona.scenario || '',
      system_prompt: persona.system_prompt || '',
      post_history_instructions: persona.post_history_instructions || '',
      greeting: detail.version?.greeting || persona.first_mes || '',
      tags: (persona.tags || []).join('，'),
    }
  } catch (e) {
    error.value = e.message || '读取角色失败'
  } finally {
    loading.value = false
  }
}

async function save() {
  if (!form.value.name.trim()) {
    error.value = '角色名不能为空'
    return
  }
  saving.value = true
  error.value = ''
  try {
    await api.createCharacterVersion(props.characterId, {
      persona: {
        name: form.value.name.trim(),
        nickname: form.value.nickname.trim(),
        description: form.value.description,
        personality: form.value.personality,
        scenario: form.value.scenario,
        system_prompt: form.value.system_prompt,
        post_history_instructions: form.value.post_history_instructions,
        tags: form.value.tags
          .split(/[,，、]/)
          .map((s) => s.trim())
          .filter(Boolean),
      },
      greeting: form.value.greeting,
      change_note: changeNote.value || '编辑人设',
    })
    emit('saved')
    emit('close')
  } catch (e) {
    error.value = e.message || '保存失败'
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="scrim" @click.self="emit('close')">
    <section class="editor" role="dialog" aria-label="编辑角色">
      <header class="editor__head">
        <div>
          <h2 class="editor__title serif">编辑人设</h2>
          <p class="editor__sub">保存会生成新版本；已开的会话仍用旧版本，需要手动同步。</p>
        </div>
        <button class="icon-btn" aria-label="关闭" @click="emit('close')">✕</button>
      </header>

      <p v-if="error" class="error">{{ error }}</p>
      <p v-if="loading" class="hint">读取中…</p>

      <div v-else class="editor__body">
        <div class="row">
          <label class="field">
            <span>名字</span>
            <input v-model="form.name" type="text" maxlength="64" />
          </label>
          <label class="field">
            <span>昵称（可选，聊天里优先用它）</span>
            <input v-model="form.nickname" type="text" maxlength="64" />
          </label>
        </div>
        <label class="field">
          <span>角色描述</span>
          <textarea v-model="form.description" rows="3" />
        </label>
        <label class="field">
          <span>性格</span>
          <textarea v-model="form.personality" rows="2" />
        </label>
        <label class="field">
          <span>场景</span>
          <textarea v-model="form.scenario" rows="2" />
        </label>
        <label class="field">
          <span>系统提示词</span>
          <textarea v-model="form.system_prompt" rows="3" />
        </label>
        <label class="field">
          <span>历史后指令</span>
          <textarea v-model="form.post_history_instructions" rows="2" />
        </label>
        <label class="field">
          <span>开场白</span>
          <textarea v-model="form.greeting" rows="2" />
        </label>
        <label class="field">
          <span>标签（逗号分隔）</span>
          <input v-model="form.tags" type="text" />
        </label>
        <label class="field">
          <span>版本说明</span>
          <input v-model="changeNote" type="text" maxlength="255" placeholder="这次改了什么" />
        </label>
      </div>

      <footer class="editor__foot">
        <button class="btn btn--quiet" @click="emit('close')">取消</button>
        <button class="btn btn--primary" :disabled="saving || loading" @click="save">
          {{ saving ? '保存中…' : '保存为新版本' }}
        </button>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.scrim {
  position: fixed;
  inset: 0;
  z-index: 130;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--scrim);
}
.editor {
  display: flex;
  flex-direction: column;
  width: min(720px, calc(100vw - 40px));
  max-height: min(86vh, 860px);
  padding: 20px 22px;
  border-radius: var(--r-box);
  background: var(--panel);
  box-shadow: var(--shadow);
  border: 1px solid var(--line);
}
.editor__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}
.editor__title { font-size: var(--step-2); color: var(--text); }
.editor__sub { margin-top: 4px; font-size: var(--step--1); color: var(--text-3); }
.error { margin-bottom: 8px; font-size: var(--step--1); color: var(--bad); }
.hint { padding: 20px 0; color: var(--text-3); }
.editor__body {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding-right: 4px;
}
.row { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.field { display: flex; flex-direction: column; gap: 4px; }
.field > span { font-size: var(--step--1); color: var(--text-3); }
.field input,
.field textarea {
  width: 100%;
  padding: 7px 9px;
  border-radius: var(--r-tight);
  font-size: var(--step-0);
  color: var(--text);
  background: var(--bg-deep);
  box-shadow: inset 0 0 0 1px var(--line);
  resize: vertical;
}
.field input:focus,
.field textarea:focus { outline: none; box-shadow: inset 0 0 0 1px var(--accent); }
.editor__foot {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 14px;
}
</style>
