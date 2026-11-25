// 本机存储：统一 companion_ 前缀；旧键（mp_*、aigf-theme）首次读取时自动搬过来，
// 避免改名把用户登出或把主题选择弄丢。
const LEGACY = {
  companion_token: 'mp_token',
  companion_user: 'mp_user',
  companion_admin_token: 'mp_admin_token',
  companion_theme: 'aigf-theme',
}

export function readItem(key) {
  try {
    const value = localStorage.getItem(key)
    if (value !== null) return value
    const legacyKey = LEGACY[key]
    if (!legacyKey) return null
    const legacy = localStorage.getItem(legacyKey)
    if (legacy === null) return null
    localStorage.setItem(key, legacy)
    localStorage.removeItem(legacyKey)
    return legacy
  } catch {
    return null
  }
}

export function writeItem(key, value) {
  try {
    localStorage.setItem(key, value)
  } catch {
    /* 隐私模式/配额满：内存态继续工作 */
  }
}

export function removeItem(key) {
  try {
    localStorage.removeItem(key)
  } catch {
    /* 忽略 */
  }
}
