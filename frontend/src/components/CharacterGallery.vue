<script setup>
import { PRESETS } from '../presets'

defineProps({
  creating: { type: String, default: '' },
})
const emit = defineEmits(['select'])
</script>

<template>
  <section class="gallery">
    <header class="gallery__head">
      <div>
        <h1 class="gallery__title serif">人物</h1>
        <p class="gallery__sub">挑一个人开始，或者先去跟老朋友说句话。每段关系都是独立的。</p>
      </div>
      <span class="gallery__count num">{{ PRESETS.length }} 位</span>
    </header>

    <div class="grid">
      <button
        v-for="p in PRESETS"
        :key="p.id"
        class="card"
        :disabled="creating === p.id"
        @click="emit('select', p)"
      >
        <span class="card__portrait" :style="{ background: p.tint }">
          <span class="card__initial serif">{{ p.name.slice(0, 1) }}</span>
          <span class="card__tags">
            <span v-for="t in p.tags" :key="t" class="card__tag">{{ t }}</span>
          </span>
        </span>
        <span class="card__body">
          <span class="card__top">
            <span class="card__name serif">{{ p.name }}</span>
            <span class="card__go">{{ creating === p.id ? '创建中…' : '开始聊天 →' }}</span>
          </span>
          <span class="card__tagline">{{ p.tagline }}</span>
        </span>
      </button>
    </div>
  </section>
</template>

<style scoped>
.gallery {
  flex: 1;
  overflow-y: auto;
  padding: 26px clamp(18px, 3vw, 40px) 40px;
}
.gallery__head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 20px;
}
.gallery__title {
  font-size: var(--step-3);
  color: var(--text);
}
.gallery__sub {
  margin-top: 6px;
  font-size: var(--step-0);
  line-height: 1.7;
  color: var(--text-3);
  max-width: 44ch;
}
.gallery__count {
  flex-shrink: 0;
  font-size: var(--step--1);
  color: var(--text-3);
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 14px;
}
.card {
  display: flex;
  flex-direction: column;
  text-align: left;
  border-radius: var(--r-box);
  overflow: hidden;
  background: var(--panel-2);
  box-shadow: inset 0 0 0 1px var(--line);
  transition: transform 0.16s var(--ease), box-shadow 0.16s var(--ease);
}
.card:hover {
  transform: translateY(-2px);
  box-shadow: inset 0 0 0 1px var(--accent), var(--shadow);
}
.card:disabled {
  opacity: 0.7;
  transform: none;
}
.card__portrait {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  height: 118px;
}
.card__initial {
  font-size: 46px;
  color: rgba(255, 255, 255, 0.92);
  text-shadow: 0 2px 12px rgba(0, 0, 0, 0.25);
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
  color: rgba(255, 255, 255, 0.9);
  background: rgba(0, 0, 0, 0.28);
  backdrop-filter: blur(2px);
}
.card__body {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 10px 12px 12px;
}
.card__top {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}
.card__name {
  font-size: var(--step-1);
  color: var(--text);
}
.card__go {
  flex-shrink: 0;
  font-size: 10px;
  color: var(--text-3);
}
.card:hover .card__go {
  color: var(--accent);
}
.card__tagline {
  font-size: var(--step--1);
  line-height: 1.5;
  color: var(--text-2);
}
</style>
