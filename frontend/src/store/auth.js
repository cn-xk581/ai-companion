import { reactive } from 'vue'
import { api, setToken, clearToken } from '../api'

export const auth = reactive({
  token: localStorage.getItem('aigf_token') || '',
  user: null,
})

try {
  auth.user = JSON.parse(localStorage.getItem('aigf_user') || 'null')
} catch {
  auth.user = null
}

export const isAuthed = () => !!auth.token

function persist(res) {
  auth.token = res.access_token
  auth.user = { user_id: res.user_id, username: res.username }
  setToken(res.access_token)
  localStorage.setItem('aigf_user', JSON.stringify(auth.user))
}

export async function login(username, password) {
  const res = await api.login(username, password)
  persist(res)
}

export async function register(username, password) {
  const res = await api.register(username, password)
  persist(res)
}

export function logout() {
  auth.token = ''
  auth.user = null
  clearToken()
  localStorage.removeItem('aigf_user')
}
