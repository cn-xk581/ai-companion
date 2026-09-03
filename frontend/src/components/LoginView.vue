<script setup>
import { ref } from 'vue'
import { login, register } from '../store/auth'

const emit = defineEmits(['authed'])

const mode = ref('login') // login | register
const username = ref('')
const password = ref('')
const confirm = ref('')
const error = ref('')
const loading = ref(false)

async function submit() {
  error.value = ''
  if (!username.value || !password.value) {
    error.value = '请输入用户名和密码'
    return
  }
  if (mode.value === 'register') {
    if (password.value.length < 6) {
      error.value = '密码至少 6 位'
      return
    }
    if (password.value !== confirm.value) {
      error.value = '两次输入的密码不一致'
      return
    }
  }
  loading.value = true
  try {
    if (mode.value === 'login') {
      await login(username.value, password.value)
    } else {
      await register(username.value, password.value)
    }
    emit('authed')
  } catch (e) {
    error.value = e.message || '操作失败'
  } finally {
    loading.value = false
  }
}

function switchMode() {
  mode.value = mode.value === 'login' ? 'register' : 'login'
  error.value = ''
  confirm.value = ''
}
</script>

<template>
  <div class="login-wrap">
    <div class="login-card">
      <div class="brand">
        <div class="brand-logo">柔</div>
        <h1>小柔 · AI 女友</h1>
        <p>像微信一样，随时陪在你身边</p>
      </div>

      <div class="tabs">
        <button :class="{ active: mode === 'login' }" @click="mode = 'login'; error = ''">登录</button>
        <button :class="{ active: mode === 'register' }" @click="mode = 'register'; error = ''">注册</button>
      </div>

      <div class="form">
        <input
          v-model="username"
          class="field"
          placeholder="用户名（3-32 位字母数字下划线）"
          autocomplete="username"
          @keyup.enter="submit"
        />
        <input
          v-model="password"
          class="field"
          type="password"
          placeholder="密码"
          autocomplete="current-password"
          @keyup.enter="submit"
        />
        <input
          v-if="mode === 'register'"
          v-model="confirm"
          class="field"
          type="password"
          placeholder="确认密码"
          autocomplete="new-password"
          @keyup.enter="submit"
        />

        <p v-if="error" class="error">{{ error }}</p>

        <button class="btn btn-primary submit" :disabled="loading" @click="submit">
          {{ loading ? '请稍候…' : mode === 'login' ? '登录' : '注册并进入' }}
        </button>

        <button class="switch" @click="switchMode">
          {{ mode === 'login' ? '还没有账号？去注册' : '已有账号？去登录' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.login-wrap {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(160deg, #07c160 0%, #0e9f57 45%, #075e4a 100%);
}
.login-card {
  width: 360px;
  background: #fff;
  border-radius: 12px;
  padding: 36px 32px 28px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.25);
}
.brand {
  text-align: center;
  margin-bottom: 24px;
}
.brand-logo {
  width: 64px;
  height: 64px;
  margin: 0 auto 12px;
  border-radius: 14px;
  background: var(--wx-green);
  color: #fff;
  font-size: 34px;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 8px 20px rgba(7, 193, 96, 0.4);
}
.brand h1 {
  font-size: 20px;
  margin-bottom: 6px;
}
.brand p {
  color: var(--wx-text-sub);
  font-size: 13px;
}
.tabs {
  display: flex;
  border-bottom: 1px solid var(--wx-border);
  margin-bottom: 20px;
}
.tabs button {
  flex: 1;
  padding: 10px 0;
  font-size: 15px;
  color: var(--wx-text-sub);
  position: relative;
}
.tabs button.active {
  color: var(--wx-green);
  font-weight: 600;
}
.tabs button.active::after {
  content: '';
  position: absolute;
  left: 30%;
  right: 30%;
  bottom: -1px;
  height: 2px;
  background: var(--wx-green);
}
.field {
  width: 100%;
  padding: 12px 14px;
  border: 1px solid var(--wx-border);
  border-radius: 8px;
  font-size: 14px;
  margin-bottom: 14px;
  transition: border-color 0.15s;
}
.field:focus {
  border-color: var(--wx-green);
}
.error {
  color: #fa5151;
  font-size: 13px;
  margin: -6px 0 10px;
}
.submit {
  width: 100%;
  padding: 12px;
  font-size: 16px;
  border-radius: 8px;
  margin-top: 4px;
}
.switch {
  width: 100%;
  margin-top: 14px;
  color: var(--wx-text-sub);
  font-size: 13px;
}
.switch:hover {
  color: var(--wx-green);
}
</style>
