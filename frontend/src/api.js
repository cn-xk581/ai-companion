import { readItem, writeItem, removeItem } from './storage'

const API_BASE = '/api/v1'

export function getToken() {
  return readItem('companion_token') || ''
}
export function setToken(token) {
  writeItem('companion_token', token)
}
export function clearToken() {
  removeItem('companion_token')
}
export function getAdminToken() {
  return readItem('companion_admin_token') || ''
}
export function setAdminToken(token) {
  if (token) writeItem('companion_admin_token', token)
  else removeItem('companion_admin_token')
}

async function request(path, { method = 'GET', body, token = null, admin = false } = {}) {
  const headers = { 'Content-Type': 'application/json' }
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
  register: (username, password) =>
    request('/auth/register', { method: 'POST', body: { username, password }, token: '' }),
  login: (username, password) =>
    request('/auth/login', { method: 'POST', body: { username, password }, token: '' }),

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

  fetch(`${API_BASE}/chat/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${getToken()}`,
    },
    body: JSON.stringify({ session_id: sessionId, user_input: userInput, client_msg_id: clientMsgId }),
    signal: controller.signal,
  })
    .then(async (res) => {
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
    })
    .catch((e) => {
      if (e.name !== 'AbortError') finishErr(e)
    })

  return controller
}
