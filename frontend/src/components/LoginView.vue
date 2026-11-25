<script setup>
import { ref } from 'vue'
import { login, register } from '../store/auth'

const emit = defineEmits(['authed'])

const mode = ref('login')
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
    if (mode.value === 'login') await login(username.value, password.value)
    else await register(username.value, password.value)
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
  <div class="gate">
    <section class="word">
      <p class="mark serif">小柔</p>
      <p class="quote serif">「你回来啦。<br />今天想从哪儿说起？」</p>
      <p class="explain">
        她记得你说过的事，关系会从陌生一点点往前走。
        想改她的性格、说话方式或者禁区，进去以后在左下角「设置」里都能改。
      </p>
    </section>

    <section class="enter">
      <div class="enter-inner">
        <div class="modes" role="tablist">
          <button
            class="mode"
            :class="{ 'is-on': mode === 'login' }"
            role="tab"
            :aria-selected="mode === 'login'"
            @click="mode = 'login'; error = ''"
          >
            登录
          </button>
          <button
            class="mode"
            :class="{ 'is-on': mode === 'register' }"
            role="tab"
            :aria-selected="mode === 'register'"
            @click="mode = 'register'; error = ''"
          >
            注册
          </button>
        </div>

        <form class="form" @submit.prevent="submit">
          <label class="row">
            <span class="field-label">用户名</span>
            <input
              v-model="username"
              class="field"
              placeholder="3–32 位字母、数字或下划线"
              autocomplete="username"
            />
          </label>

          <label class="row">
            <span class="field-label">密码</span>
            <input
              v-model="password"
              class="field"
              type="password"
              :placeholder="mode === 'register' ? '至少 6 位' : '你的密码'"
              :autocomplete="mode === 'login' ? 'current-password' : 'new-password'"
            />
          </label>

          <label v-if="mode === 'register'" class="row">
            <span class="field-label">再输一次</span>
            <input
              v-model="confirm"
              class="field"
              type="password"
              placeholder="确认密码"
              autocomplete="new-password"
            />
          </label>

          <p v-if="error" class="error">{{ error }}</p>

          <button class="btn btn--primary btn--full go" type="submit" :disabled="loading">
            {{ loading ? '稍等…' : mode === 'login' ? '进去' : '注册并进去' }}
          </button>

          <button class="flip" type="button" @click="switchMode">
            {{ mode === 'login' ? '第一次来？注册一个' : '已经有账号，去登录' }}
          </button>
        </form>
      </div>
    </section>
  </div>
</template>

<style scoped>
.gate {
  display: grid;
  grid-template-columns: 1.1fr 0.9fr;
  height: 100%;
  background: var(--bg);
}
.word {
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  gap: 20px;
  padding: clamp(32px, 6vw, 72px);
  padding-bottom: clamp(48px, 11vh, 108px);
  background: var(--panel);
  border-right: 1px solid var(--line);
  background-image: repeating-linear-gradient(
    to bottom,
    var(--grain) 0,
    var(--grain) 1px,
    transparent 1px,
    transparent 34px
  );
}
.mark {
  font-size: clamp(40px, 4.6vw, 60px);
  line-height: 1;
  letter-spacing: 0.04em;
  color: var(--text);
  animation: rise 0.9s var(--ease) both;
}
@keyframes rise {
  from {
    opacity: 0;
    filter: blur(7px);
    transform: translateY(10px);
  }
  to {
    opacity: 1;
    filter: blur(0);
    transform: none;
  }
}
.quote {
  max-width: 21em;
  font-size: clamp(17px, 1.45vw, 21px);
  line-height: 1.85;
  color: var(--text);
  opacity: 0.92;
  animation: rise 0.9s var(--ease) 0.12s both;
}
.explain {
  max-width: 24em;
  padding-top: 16px;
  font-size: var(--step-0);
  line-height: 1.9;
  color: var(--text-3);
  animation: rise 0.9s var(--ease) 0.22s both;
}
.explain::before {
  content: '';
  display: block;
  width: 52px;
  height: 1px;
  margin-bottom: 14px;
  background: var(--line);
}
.enter {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: clamp(24px, 5vw, 56px);
}
.enter-inner {
  width: 100%;
  max-width: 320px;
}
.modes {
  display: flex;
  gap: 20px;
  margin-bottom: 22px;
  border-bottom: 1px solid var(--line);
}
.mode {
  padding: 0 0 9px;
  margin-bottom: -1px;
  font-size: var(--step-1);
  color: var(--text-3);
  border-bottom: 1px solid transparent;
}
.mode:hover {
  color: var(--text-2);
}
.mode.is-on {
  color: var(--text);
  border-bottom-color: var(--accent);
}
.form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.row {
  display: block;
}
.error {
  font-size: var(--step-0);
  color: var(--bad);
}
.go {
  margin-top: 4px;
  height: 42px;
}
.flip {
  align-self: center;
  margin-top: 2px;
  font-size: var(--step-0);
  color: var(--text-3);
}
.flip:hover {
  color: var(--text);
}
@media (max-width: 820px) {
  .gate {
    grid-template-columns: 1fr;
    grid-template-rows: auto 1fr;
    overflow-y: auto;
  }
  .word {
    gap: 16px;
    padding: 34px 26px 26px;
    border-right: 0;
    border-bottom: 1px solid var(--line);
  }
  .mark {
    font-size: 38px;
  }
  .quote {
    font-size: 16px;
  }
  .enter {
    padding: 28px 26px 44px;
  }
}
</style>
