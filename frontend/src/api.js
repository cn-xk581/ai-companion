import { readItem, writeItem, removeItem } from './storage'

const API_BASE = '/api/v1'

const ACCESS_KEY = 'companion_token'
const REFRESH_KEY = 'companion_refresh'
const USER_KEY = 'companion_user'
const ADMIN_KEY = 'companion_admin_token'

export function getToken() {
  return readItem(ACCESS_KEY) || ''
}
export function setToken(token) {
  writeItem(ACCESS_KEY, token)
}
export function clearToken() {
  removeItem(ACCESS_KEY)
}
export function getRefreshToken() {
  return readItem(REFRESH_KEY) || ''
}
export function setRefreshToken(token) {
  writeItem(REFRESH_KEY, token)
}
export function clearRefreshToken() {
  removeItem(REFRESH_KEY)
}
export function getStoredUser() {
  try {
    return JSON.parse(readItem(USER_KEY) || 'null')
  } catch {
    return null
  }
}
export function setStoredUser(user) {
  writeItem(USER_KEY, JSON.stringify(user || null))
}
export function clearStoredUser() {
  removeItem(USER_KEY)
}
export function getAdminToken() {
  return readItem(ADMIN_KEY) || ''
}
export function setAdminToken(token) {
  if (token) writeItem(ADMIN_KEY, token)
  else removeItem(ADMIN_KEY)
}

let refreshing = null

async function refreshTokens() {
  const refresh = getRefreshToken()
  if (!refresh) return false
  if (refreshing) return refreshing
  refreshing = (async () => {
    try {
      const res = await fetch(`${API_BASE}/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: refresh }),
      })
      if (!res.ok) return false
      const data = await res.json()
      setToken(data.access_token)
      setRefreshToken(data.refresh_token)
      if (data.user) setStoredUser(data.user)
      return true
    } catch {
      return false
    } finally {
      refreshing = null
    }
  })()
  return refreshing
}

async function request(path, { method = 'GET', body, token = null, admin = false } = {}, retried = false) {
  const headers = { 'Content-Type': 'application/json', 'X-Client-Platform': 'web' }
  const t = token === null ? getToken() : token
  if (t) headers.Authorization = `Bearer ${t}`
  if (admin) headers['X-Admin-Token'] = getAdminToken()

  let res
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch (e) {
    const err = new Error('连不上服务：' + (e.message || '网络错误'))
    err.status = 0
    throw err
  }

  // access token 过期：用 refresh 换一次再重试
  if (res.status === 401 && !retried && token === null && path !== '/auth/refresh') {
    if (await refreshTokens()) return request(path, { method, body, token, admin }, true)
  }

  if (!res.ok) {
    let detail = res.statusText
    try {
      const j = await res.json()
      detail = typeof j.detail === 'string' ? j.detail : j.detail?.message || j.message || detail
    } catch {
      /* 非 JSON 错误体 */
    }
    const err = new Error(detail || `请求失败（${res.status}）`)
    err.status = res.status
    throw err
  }
  if (res.status === 204) return null
  return res.json()
}

export const api = {
  register: (username, password, nickname = '') =>
    request('/auth/register', {
      method: 'POST',
      body: { username, password, nickname },
      token: '',
    }),
  login: (username, password, deviceId = '') =>
    request('/auth/login', {
      method: 'POST',
      body: { username, password, device_id: deviceId, platform: 'web' },
      token: '',
    }),
  logout: (allDevices = false) =>
    request('/auth/logout', {
      method: 'POST',
      body: { refresh_token: getRefreshToken(), all_devices: allDevices },
    }),
  me: () => request('/auth/me'),
  updateMe: (nickname) => request('/users/me', { method: 'PUT', body: { nickname } }),
  updateProfile: (profile) => request('/users/me/profile', { method: 'PUT', body: profile }),
  deleteAvatar: () => request('/users/me/avatar', { method: 'DELETE' }),

  listSessions: () => request('/sessions'),
  createSession: (sessionId) =>
    request('/sessions', { method: 'POST', body: { session_id: sessionId || null } }),
  getSession: (id) => request(`/sessions/${encodeURIComponent(id)}`),
  getHistory: (id, limit = 100) =>
    request(`/sessions/${encodeURIComponent(id)}/history?limit=${limit}`),
  updatePersona: (id, persona) =>
    request(`/sessions/${encodeURIComponent(id)}/persona`, { method: 'PUT', body: persona }),
  resetSession: (id, clearMemories = true) =>
    request(`/sessions/${encodeURIComponent(id)}/reset`, {
      method: 'POST',
      body: { clear_memories: clearMemories },
    }),

  myCharacters: () => request('/characters/mine'),
  searchCharacters: (q, limit = 20) =>
    request(`/characters/search?q=${encodeURIComponent(q)}&limit=${limit}`),
  getCharacter: (id) => request(`/characters/${id}`),
  deleteCharacter: (id) => request(`/characters/${id}`, { method: 'DELETE' }),
  updateCharacter: (id, body) => request(`/characters/${id}`, { method: 'PUT', body }),
  createCharacterVersion: (id, body) =>
    request(`/characters/${id}/versions`, { method: 'POST', body }),
  createCharacterSession: (id) => request(`/characters/${id}/sessions`, { method: 'POST' }),
  syncSessionCharacter: (sessionId) =>
    request(`/sessions/${encodeURIComponent(sessionId)}/sync-character`, { method: 'POST' }),

  listMemories: () => request('/memories'),
  deleteMemory: (memoryId) => request(`/memories/${encodeURIComponent(memoryId)}`, { method: 'DELETE' }),

  getTurn: (turnId) => request(`/turns/${encodeURIComponent(turnId)}`),
  resumeTurn: (turnId, decision, feedback = '') =>
    request(`/turns/${encodeURIComponent(turnId)}/resume`, {
      method: 'POST',
      body: { decision, feedback },
    }),
  traceTurn: (turnId, limit = 500) =>
    request(`/turns/${encodeURIComponent(turnId)}/trace?limit=${limit}`),

  listSkills: () => request('/skills'),
  setSkill: (name, enabled) =>
    request(`/skills/${encodeURIComponent(name)}/${enabled ? 'enable' : 'disable'}`, {
      method: 'POST',
      admin: true,
    }),
  health: () => request('/health'),
}

/** 上传头像（multipart，服务端转 webp） */
export async function uploadAvatar(file) {
  const form = new FormData()
  form.append('file', file)
  const res = await fetch(`${API_BASE}/users/me/avatar`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${getToken()}`,
      'X-Client-Platform': 'web',
    },
    body: form,
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const j = await res.json()
      detail = j.detail || detail
    } catch {
      /* ignore */
    }
    const err = new Error(detail || '上传失败')
    err.status = res.status
    throw err
  }
  const user = await res.json()
  setStoredUser({ ...(getStoredUser() || {}), ...user })
  return user
}

/** SSE 流式对话：返回 AbortController。事件：meta / token / done / error */
export function streamChat({ sessionId, userInput, clientMsgId, onMeta, onToken, onDone, onError }) {
  const controller = new AbortController()
  let settled = false
  const finishOk = (payload) => {
    if (!settled) {
      settled = true
      onDone?.(payload)
    }
  }
  const finishErr = (err) => {
    if (!settled) {
      settled = true
      onError?.(err)
    }
  }

  const send = async (isRetry = false) => {
    let res
    try {
      res = await fetch(`${API_BASE}/chat/stream`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${getToken()}`,
        },
        body: JSON.stringify({
          session_id: sessionId,
          user_input: userInput,
          client_msg_id: clientMsgId,
        }),
        signal: controller.signal,
      })
    } catch (e) {
      if (e.name !== 'AbortError') finishErr(e)
      return
    }
    if (res.status === 401 && !isRetry) {
      if (await refreshTokens()) return send(true)
    }
    if (!res.ok) {
      let detail = res.statusText
      try {
        const j = await res.json()
        detail = j.detail || detail
      } catch {
        /* ignore */
      }
      finishErr(new Error(detail))
      return
    }
    if (!res.body) {
      finishErr(new Error('当前浏览器不支持流式响应'))
      return
    }
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      let idx
      while ((idx = buf.indexOf('\n\n')) !== -1) {
        const frame = buf.slice(0, idx)
        buf = buf.slice(idx + 2)
        for (const line of frame.split('\n')) {
          if (!line.startsWith('data: ')) continue
          let msg
          try {
            msg = JSON.parse(line.slice(6))
          } catch {
            continue
          }
          if (msg.type === 'meta') onMeta?.(msg)
          else if (msg.type === 'token') onToken?.(msg.content || '')
          else if (msg.type === 'done') finishOk(msg)
          else if (msg.type === 'error') finishErr(new Error(msg.message || '未知错误'))
        }
      }
    }
    if (!settled && !controller.signal.aborted) {
      finishErr(new Error('连接提前关闭，请重试'))
    }
  }

  send()
  return controller
}
/** 导入角色卡（PNG / JSON，multipart） */
export async function importCharacter(file) {
  const form = new FormData()
  form.append('file', file)
  const res = await fetch(`${API_BASE}/characters/import`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${getToken()}` },
    body: form,
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const j = await res.json()
      detail = j.detail || detail
    } catch {
      /* ignore */
    }
    const err = new Error(detail || '导入失败')
    err.status = res.status
    throw err
  }
  return res.json()
}

/** 下载导出文件（json / png） */
export async function downloadCharacterFile(characterId, format) {
  const res = await fetch(`${API_BASE}/characters/${characterId}/export.${format}`, {
    headers: { Authorization: `Bearer ${getToken()}` },
  })
  if (!res.ok) throw new Error('导出失败')
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `character-${characterId}.${format}`
  document.body.appendChild(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
