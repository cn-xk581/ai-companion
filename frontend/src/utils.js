export function uuid() {
  if (globalThis.crypto && typeof globalThis.crypto.randomUUID === 'function') {
    return globalThis.crypto.randomUUID().replace(/-/g, '')
  }
  return Math.random().toString(16).slice(2) + Date.now().toString(16)
}

// 微信风格的会话列表时间：今天显示 HH:mm，昨天显示「昨天」，更早显示 月/日
export function formatListTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  const now = new Date()
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  if (d.toDateString() === now.toDateString()) return `${hh}:${mm}`
  const y = new Date(now)
  y.setDate(now.getDate() - 1)
  if (d.toDateString() === y.toDateString()) return '昨天'
  return `${d.getMonth() + 1}/${d.getDate()}`
}

// 聊天气泡内的时间
export function formatMsgTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  return `${hh}:${mm}`
}

const AVATAR_COLORS = [
  '#07c160', '#576b95', '#ed6a5e', '#f4a259', '#00bcd4',
  '#9c27b0', '#795548', '#ff7043', '#607d8b', '#e91e63',
]

export function avatarColor(seed = '') {
  let h = 0
  for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) >>> 0
  return AVATAR_COLORS[h % AVATAR_COLORS.length]
}

export function truncate(s, n = 40) {
  if (!s) return ''
  return s.length > n ? s.slice(0, n) + '…' : s
}
