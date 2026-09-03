const API_BASE = '/api/v1'

export function getToken() {
  return localStorage.getItem('aigf_token') || ''
}

export function setToken(t) {
  localStorage.setItem('aigf_token', t)
}

export function clearToken() {
  localStorage.removeItem('aigf_token')
}

async function request(path, { method = 'GET', body, token = null, raw = false } = {}) {
  const headers = { 'Content-Type': 'application/json' }
  const t = token === null ? getToken() : token
  if (t) headers['Authorization'] = `Bearer ${t}`

  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  })

  if (!res.ok) {
    let detail = res.statusText
    try {
      const j = await res.json()
      detail = j.detail || j.message || detail
    } catch { /* ignore */ }
    const err = new Error(detail)
    err.status = res.status
    throw err
  }
  if (raw || res.status === 204) return null
  return res.json()
}

export const api = {
  register: (username, password) =>
    request('/auth/register', { method: 'POST', body: { username, password }, token: '' }),
  login: (username, password) =>
    request('/auth/login', { method: 'POST', body: { username, password }, token: '' }),
  me: () => request('/auth/me'),

  listSessions: () => request('/session/list'),
  createSession: (sessionId) =>
    request('/session/create', { method: 'POST', body: { session_id: sessionId || null } }),
  getSessionDetail: (sessionId) =>
    request(`/session/detail?session_id=${encodeURIComponent(sessionId)}`),
  getHistory: (sessionId, limit = 200) =>
    request(`/session/history?session_id=${encodeURIComponent(sessionId)}&limit=${limit}`),

  chat: (sessionId, userInput, clientMsgId) =>
    request('/chat', { method: 'POST', body: { session_id: sessionId, user_input: userInput, client_msg_id: clientMsgId || null } }),

  resetSession: (sessionId) =>
    request('/session/reset', { method: 'POST', body: { session_id: sessionId } }),

  listMemories: (sessionId) =>
    request(`/memory/list?session_id=${encodeURIComponent(sessionId)}`),
  deleteMemory: (sessionId, memoryId) =>
    request('/memory/delete', { method: 'POST', body: { session_id: sessionId, memory_id: memoryId } }),

  updatePersona: (sessionId, persona) =>
    request('/persona/update', { method: 'PUT', body: { session_id: sessionId, persona } }),
}

// SSE 流式聊天：返回 AbortController 便于取消
export function streamChat(sessionId, userInput, { onToken, onDone, onError, clientMsgId } = {}) {
  const controller = new AbortController()
  let settled = false

  // 保证 done/error 只触发一次（连接提前关闭时也必然触发一个终结回调）
  const finishOk = (msg) => {
    if (!settled) {
      settled = true
      onDone(msg)
    }
  }
  const finishErr = (err) => {
    if (!settled) {
      settled = true
      onError(err)
    }
  }

  fetch(`${API_BASE}/chat/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${getToken()}`,
    },
    body: JSON.stringify({ session_id: sessionId, user_input: userInput, client_msg_id: clientMsgId || null }),
    signal: controller.signal,
  })
    .then(async (res) => {
      if (!res.ok) {
        let detail = res.statusText
        try {
          const j = await res.json()
          detail = j.detail || j.message || detail
        } catch { /* ignore */ }
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
            if (msg.type === 'token') onToken(msg.content)
            else if (msg.type === 'done') finishOk(msg)
            else if (msg.type === 'error') finishErr(new Error(msg.message || '未知错误'))
          }
        }
      }
      buf += decoder.decode() // 解码器 final flush
      // 服务端在 done/error 之前关闭了连接（被用户 abort 除外）
      if (!settled && !controller.signal.aborted) {
        finishErr(new Error('回复中断，连接提前关闭，请重试'))
      }
    })
    .catch((e) => {
      if (e.name !== 'AbortError') finishErr(e)
    })

  return controller
}
