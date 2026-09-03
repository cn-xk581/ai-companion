<script setup>
import { computed } from 'vue'
import { avatarColor } from '../utils'

const props = defineProps({
  name: { type: String, default: '' },
  src: { type: String, default: '' },
  size: { type: Number, default: 40 },
})

const bg = computed(() => (props.src ? 'transparent' : avatarColor(props.name || '?')))
const initial = computed(() => (props.name ? props.name.trim().charAt(0) : '?'))
</script>

<template>
  <span
    class="avatar"
    :style="{
      width: size + 'px',
      height: size + 'px',
      background: bg,
      fontSize: Math.round(size * 0.42) + 'px',
    }"
  >
    <img v-if="src" :src="src" alt="" />
    <span v-else>{{ initial }}</span>
  </span>
</template>
