import { reactive } from 'vue'
import { api, setToken, clearToken } from '../api'
import { readItem, writeItem, removeItem } from '../storage'

const TOKEN_KEY = 'companion_token'
const USER_KEY = 'companion_user'

export const auth = reactive({
  token: readItem(TOKEN_KEY) || '',
  user: null,
})

try {
  auth.user = JSON.parse(readItem(USER_KEY) || 'null')
} catch {
  auth.user = null
}

export const isAuthed = () => !!auth.token

function persist(res) {
  auth.token = res.access_token
  auth.user = { user_id: res.user_id, username: res.username }
  setToken(res.access_token)
  writeItem(USER_KEY, JSON.stringify(auth.user))
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
  removeItem(USER_KEY)
}
