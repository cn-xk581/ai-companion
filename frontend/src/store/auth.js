import { reactive } from 'vue'
import {
  api,
  clearRefreshToken,
  clearStoredUser,
  clearToken,
  getStoredUser,
  setRefreshToken,
  setStoredUser,
  setToken,
} from '../api'
import { readItem, writeItem, removeItem } from '../storage'

const TOKEN_KEY = 'companion_token'
const USER_KEY = 'companion_user'

export const auth = reactive({
  token: readItem(TOKEN_KEY) || '',
  user: getStoredUser(),
})

export const isAuthed = () => !!auth.token

function persist(res) {
  auth.token = res.access_token
  auth.user = res.user || null
  setToken(res.access_token)
  setRefreshToken(res.refresh_token)
  setStoredUser(auth.user)
  writeItem(USER_KEY, JSON.stringify(auth.user))
}

export async function login(username, password) {
  const res = await api.login(username, password)
  persist(res)
}

export async function register(username, password, nickname = '') {
  const res = await api.register(username, password, nickname)
  persist(res)
}

export function setUser(user) {
  auth.user = user
  setStoredUser(user)
  writeItem(USER_KEY, JSON.stringify(user))
}

export async function logout() {
  try {
    if (auth.token) await api.logout(false)
  } catch {
    /* 网络失败也要本地登出 */
  }
  auth.token = ''
  auth.user = null
  clearToken()
  clearRefreshToken()
  clearStoredUser()
  removeItem(USER_KEY)
}
