<script setup>
import { computed, ref } from 'vue'
import { REVIEW_CATEGORY_LABELS } from '../constants'

const props = defineProps({
  turn: { type: Object, required: true },
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['decide'])

const feedback = ref('')
const interrupt = computed(() => props.turn?.interrupt || {})
const categories = computed(() =>
  (interrupt.value.categories || []).map((c) => REVIEW_CATEGORY_LABELS[c] || c)
)
const draft = computed(() => interrupt.value.draft || '')
const reason = computed(() => interrupt.value.reason || '这条回复需要人工确认')

function decide(decision) {
  emit('decide', { decision, feedback: feedback.value.trim() })
}
</script>

<template>
  <section class="gate">
    <header class="gate__head">
      <div>
        <p class="gate__kicker num">待复核</p>
        <p class="gate__title serif">这条回复先压一下</p>
      </div>
      <span v-if="categories.length" class="gate__tags">
        <span v-for="c in categories" :key="c" class="gate__tag">{{ c }}</span>
      </span>
    </header>

    <p class="gate__reason">{{ reason }}</p>
    <blockquote v-if="draft" class="gate__draft">{{ draft }}</blockquote>

    <label class="gate__field">
      <span class="field-label">复核备注（可选）</span>
      <input v-model="feedback" class="field" placeholder="例：太越界，改成普通关心的说法" />
    </label>

    <div class="gate__actions">
      <button class="btn btn--primary btn--sm" :disabled="busy" @click="decide('approve')">
        通过，发出
      </button>
      <button class="btn btn--quiet btn--sm" :disabled="busy" @click="decide('reject')">
        驳回，换成安全话术
      </button>
    </div>
  </section>
</template>

<style scoped>
.gate {
  margin: 0 clamp(16px, 4vw, 34px) 14px;
  padding: 12px 14px 14px;
  border-radius: var(--r-box);
  background: var(--panel-2);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent) 55%, transparent);
}
.gate__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}
.gate__kicker {
  font-size: var(--step--1);
  color: var(--text-3);
}
.gate__title {
  margin-top: 2px;
  font-size: var(--step-1);
  color: var(--text);
}
.gate__tags {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}
.gate__tag {
  padding: 1px 8px;
  border-radius: 999px;
  font-size: var(--step--1);
  color: var(--accent);
  background: var(--accent-soft);
}
.gate__reason {
  margin-top: 8px;
  font-size: var(--step-0);
  line-height: 1.7;
  color: var(--text-2);
}
.gate__draft {
  margin-top: 8px;
  padding: 8px 10px;
  border-left: 2px solid var(--accent);
  background: var(--raise);
  font-size: var(--step-0);
  line-height: 1.7;
  color: var(--text);
  white-space: pre-wrap;
}
.gate__field {
  display: block;
  margin-top: 10px;
}
.gate__actions {
  display: flex;
  gap: 8px;
  margin-top: 10px;
  flex-wrap: wrap;
}
</style>
