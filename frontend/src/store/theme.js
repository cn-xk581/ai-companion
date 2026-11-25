import { reactive } from 'vue'
import { readItem, writeItem } from '../storage'

const KEY = 'companion_theme'

// 两套配色，各自是一个完整的语义 token 集（定义在 style.css 的 [data-theme] 里）
export const THEMES = [
  {
    id: 'night',
    name: '夜灯',
    sub: '丝绒墨蓝',
    note: '深夜的默认。只有琥珀色亮着，表示她在。',
    swatches: ['#10141c', '#1b232f', '#e9e4d7', '#e3b25c'],
  },
  {
    id: 'day',
    name: '信纸',
    sub: '冷灰墨绿',
    note: '白天的默认。冷灰纸底配墨绿，光线足时更舒服。',
    swatches: ['#e3e6de', '#f8f9f3', '#fbfaf5', '#2f5d4e'],
  },
]

const VALID = THEMES.map((t) => t.id)

function systemPreference() {
  try {
    if (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches) {
      return 'day'
    }
  } catch {
    /* 老浏览器忽略 */
  }
  return 'night'
}

function stored() {
  try {
    const v = readItem(KEY)
    return VALID.includes(v) ? v : ''
  } catch {
    return ''
  }
}

export const theme = reactive({
  id: 'night',
  // 用户手动选过就不再被系统偏好改回去
  pinned: false,
})

function paint(id) {
  document.documentElement.dataset.theme = id
  const meta = document.querySelector('meta[name="theme-color"]')
  if (meta) meta.setAttribute('content', id === 'day' ? '#e3e6de' : '#10141c')
}

export function setTheme(id) {
  if (!VALID.includes(id)) return
  theme.id = id
  theme.pinned = true
  paint(id)
  try {
    writeItem(KEY, id)
  } catch {
    /* 隐私模式下忽略 */
  }
}

// 首屏由 index.html 里的内联脚本先涂一次，这里只做状态同步，避免闪白
export function initTheme() {
  const saved = stored()
  theme.id = saved || document.documentElement.dataset.theme || systemPreference()
  theme.pinned = !!saved
  paint(theme.id)
}

export function toggleTheme() {
  setTheme(theme.id === 'night' ? 'day' : 'night')
}
