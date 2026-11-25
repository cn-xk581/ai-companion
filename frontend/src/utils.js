export function uuid() {
  if (globalThis.crypto && typeof globalThis.crypto.randomUUID === 'function') {
    return globalThis.crypto.randomUUID().replace(/-/g, '')
  }
  return Math.random().toString(16).slice(2) + Date.now().toString(16)
}

export function formatListTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const now = new Date()
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  if (d.toDateString() === now.toDateString()) return `${hh}:${mm}`
  const y = new Date(now)
  y.setDate(now.getDate() - 1)
  if (d.toDateString() === y.toDateString()) return '昨天'
  return `${d.getMonth() + 1}/${d.getDate()}`
}

export function formatDuration(ms) {
  if (ms === null || ms === undefined) return ''
  const n = Number(ms)
  if (Number.isNaN(n)) return ''
  return n < 1000 ? `${Math.round(n)}ms` : `${(n / 1000).toFixed(n < 10000 ? 2 : 1)}s`
}

export function truncate(text, n = 24) {
  if (!text) return ''
  const clean = String(text).replace(/\s+/g, ' ').trim()
  return clean.length > n ? clean.slice(0, n) + '…' : clean
}

const AVATAR_COLORS = [
  '#6b7a8f', '#8a6f63', '#5f7a6a', '#7a6a86',
  '#87695f', '#4f6f7a', '#7d7a5f', '#6a6a7a',
]

export function avatarColor(seed = '') {
  let h = 0
  for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) >>> 0
  return AVATAR_COLORS[h % AVATAR_COLORS.length]
}

export function defaultPersona() {
  return {
    name: '小柔',
    age: 22,
    personality: '温柔、有点小傲娇，会在乎对方但嘴硬，偶尔撒娇',
    speaking_style: '口语化、带点俏皮，喜欢用语气词（哼、嘛、呀），生气时话变少',
    background: '都市白领，喜欢猫、奶茶和深夜电影',
    likes: ['猫', '奶茶', '深夜电影'],
    dislikes: ['被敷衍', '被鸽'],
    boundaries: ['不聊政治', '不提供医疗建议'],
  }
}
