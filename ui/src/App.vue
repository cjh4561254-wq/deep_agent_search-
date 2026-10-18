<script setup lang="ts">
/**
 * DeepAgents 前端主页面（方案 05 · Stripe 金融科技流体渐变风）
 * ------------------------------------------------------------------
 * 视觉来源：方案 05 · Stripe 金融科技流体渐变风（原演示页已按用户要求从项目中移除）
 * 逻辑来源：原 App.vue（保留 8020 后端、WebSocket 事件流、文件上传/下载全部能力）
 *
 * 本次重构相对 Demo 的变化：
 *  1. 新增「登录页」：真实入口，登录后才能进入工作台（当前为前端演示登录，未接后端鉴权）；
 *  2. Demo 里的 Mock 消息/日志/文件全部换成真实的 messages / logs / fileList 数据；
 *  3. 保留并增强了「新会话」「侧栏开关」「附件上传」「思考过程展开」「建议问题快捷填入」。
 */
import { ref, onMounted, onBeforeUnmount, nextTick, computed } from 'vue'
import axios from 'axios'
import { marked } from 'marked'

// ===== 后端地址（唯一配置点，别再散落硬编码）=====
// 背景：本机 8000 端口被「知识库」服务占用，DAS 后端跑在 8020；
// 且后端 uvicorn 绑定的是 127.0.0.1（IPv4），所以这里必须写 127.0.0.1:8020。
// 前端页面本身仍然要用 http://localhost:5180 打开（vite 只监听 IPv6）。
// 以后后端换端口，只改下面两行即可。
const API_BASE = 'http://127.0.0.1:8020'
const WS_BASE = 'ws://127.0.0.1:8020'

// ===== 类型 =====
interface Message {
  role: 'user' | 'ai' | 'system'
  content: string
  logs?: LogItem[]
  files?: FileItem[]
  timestamp?: number
}

interface LogItem {
  type: string
  title: string
  details: any
  timestamp: string
}

interface FileItem {
  name: string
  path: string
  url: string
}

interface ServerFile {
  name: string
  path: string
  url: string
  size?: number
  mtime?: number
}

// ============================================================
// 一、登录状态（前端演示登录）
// ============================================================
const AUTH_STORAGE_KEY = 'deepagents-auth'
const REMEMBER_ACCOUNT_KEY = 'deepagents-remember-account'

const isAuthed = ref(false)
const account = ref('')
const password = ref('')
const remember = ref(false)
const showPassword = ref(false)
const loggingIn = ref(false)
const loginDone = ref(false)
const loginErrors = ref({ account: '', password: '' })
const rememberedAccount = ref(localStorage.getItem(REMEMBER_ACCOUNT_KEY) || '')

const accountValid = computed(() => !loginErrors.value.account && isAccountFormatOk(account.value))
const passwordValid = computed(() => !loginErrors.value.password && password.value.length >= 6)

function isAccountFormatOk(value: string): boolean {
  const v = value.trim()
  if (!v) return false
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v) || /^1[3-9]\d{9}$/.test(v)
}

function validateLogin(): boolean {
  const errors = { account: '', password: '' }
  const acc = account.value.trim()
  if (!acc) {
    errors.account = '请输入邮箱或手机号'
  } else if (!isAccountFormatOk(acc)) {
    errors.account = '邮箱或手机号格式不正确'
  }
  if (!password.value) {
    errors.password = '请输入密码'
  } else if (password.value.length < 6) {
    errors.password = '密码至少 6 位'
  }
  loginErrors.value = errors
  return !errors.account && !errors.password
}

function onAccountBlur() {
  if (!account.value.trim()) return
  if (!isAccountFormatOk(account.value)) {
    loginErrors.value = { ...loginErrors.value, account: '邮箱或手机号格式不正确' }
  } else {
    loginErrors.value = { ...loginErrors.value, account: '' }
  }
}

function onPasswordBlur() {
  if (!password.value) return
  if (password.value.length < 6) {
    loginErrors.value = { ...loginErrors.value, password: '密码至少 6 位' }
  } else {
    loginErrors.value = { ...loginErrors.value, password: '' }
  }
}

/**
 * 演示登录：只做前端格式校验，不请求后端。
 * 真实的账号体系（注册 / 找回密码 / 第三方 OAuth）需要后端提供接口后再接入。
 */
async function handleLogin() {
  if (loggingIn.value) return
  if (!validateLogin()) return

  loggingIn.value = true
  await new Promise((resolve) => setTimeout(resolve, 720))

  // 记住我：持久化登录态 + 账号（不保存密码）
  if (remember.value) {
    localStorage.setItem(AUTH_STORAGE_KEY, '1')
    localStorage.setItem(REMEMBER_ACCOUNT_KEY, account.value.trim())
    rememberedAccount.value = account.value.trim()
  } else {
    sessionStorage.setItem(AUTH_STORAGE_KEY, '1')
    localStorage.removeItem(REMEMBER_ACCOUNT_KEY)
    rememberedAccount.value = ''
  }

  loggingIn.value = false
  loginDone.value = true
  isAuthed.value = true
  password.value = ''
  await nextTick()
  resetSessionState()
  connectWebSocket()
  toast('登录成功，正在进入工作台…', 'info')
}

function handleLogout() {
  localStorage.removeItem(AUTH_STORAGE_KEY)
  sessionStorage.removeItem(AUTH_STORAGE_KEY)
  disconnectSocket()
  resetSessionState()
  isAuthed.value = false
  loginDone.value = false
  loginErrors.value = { account: '', password: '' }
  password.value = ''
  toast('已退出登录', 'info')
}

// ============================================================
// 二、工作台状态
// ============================================================
const inputQuery = ref('')
const messages = ref<Message[]>([])
const status = ref<'idle' | 'running'>('idle')
const socket = ref<WebSocket | null>(null)
const currentSessionPath = ref('')
const currentSessionUrl = ref('')
const scrollRef = ref<HTMLElement | null>(null)
const composerTextarea = ref<HTMLTextAreaElement | null>(null)
const fileInputRef = ref<HTMLInputElement | null>(null)
const isWelcomeScreen = computed(() => messages.value.length === 0)
const isSidebarOpen = ref(false)
const fileList = ref<ServerFile[]>([])
const currentThreadId = ref(crypto.randomUUID())
const mode = ref<'深度研究' | '快速问答'>('深度研究')
const openLogs = ref<Record<number, boolean>>({})
const selectedFiles = ref<File[]>([])
let manualSocketClose = false

const sessionLabel = computed(() => {
  if (status.value === 'running') return '研究进行中'
  if (currentSessionPath.value) return '当前会话 · 文件已生成'
  return mode.value === '深度研究' ? '新会话 · 深度研究' : '新会话 · 快速问答'
})

// ============================================================
// 三、轻提示（toast）
// ============================================================
interface ToastItem {
  id: number
  msg: string
  info: boolean
}
const toasts = ref<ToastItem[]>([])
let toastSeq = 0
function toast(msg: string, type: 'ok' | 'info' = 'ok') {
  const id = ++toastSeq
  toasts.value.push({ id, msg, info: type === 'info' })
  setTimeout(() => {
    toasts.value = toasts.value.filter((t) => t.id !== id)
  }, 2800)
}

// ============================================================
// 四、滚动 / 工具函数
// ============================================================
const scrollToBottom = async () => {
  await nextTick()
  const el = scrollRef.value
  if (el) el.scrollTop = el.scrollHeight
}

function formatClock(ts?: number): string {
  return new Date(ts ?? Date.now()).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false
  })
}

function formatDetails(details: any): string {
  try {
    if (typeof details === 'string') return details
    return JSON.stringify(details, null, 2)
  } catch {
    return String(details)
  }
}

function fileExtClass(name: string): string {
  const ext = (name.split('.').pop() || '').toLowerCase()
  if (['md', 'markdown'].includes(ext)) return 'md'
  if (ext === 'pdf') return 'pdf'
  if (['xlsx', 'xls', 'csv'].includes(ext)) return 'xlsx'
  if (['png', 'jpg', 'jpeg', 'webp', 'gif', 'svg'].includes(ext)) return 'png'
  if (['doc', 'docx'].includes(ext)) return 'doc'
  if (['zip', 'rar', '7z'].includes(ext)) return 'zip'
  return 'md'
}

function fileExtLabel(name: string): string {
  const ext = (name.split('.').pop() || '').toLowerCase()
  return ext ? ext.toUpperCase() : 'FILE'
}

function formatSize(size?: number): string {
  if (size === undefined || size === null) return ''
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

function logsOpen(index: number): boolean {
  return openLogs.value[index] !== false
}

function toggleLogs(index: number) {
  openLogs.value[index] = !logsOpen(index)
}

function useSuggestion(text: string) {
  inputQuery.value = text
  autoGrow()
  composerTextarea.value?.focus()
}

function autoGrow() {
  const el = composerTextarea.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, 180)}px`
}

function handleComposerKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault()
    sendMessage()
  }
}

// ============================================================
// 五、WebSocket 连接（与后端 8020 的 /ws/{thread_id}）
// ============================================================
function connectWebSocket() {
  if (!isAuthed.value) return
  if (socket.value && socket.value.readyState <= WebSocket.OPEN) return

  const ws = new WebSocket(`${WS_BASE}/ws/${currentThreadId.value}`)

  ws.onopen = () => {
    console.log('WebSocket Connected')
  }

  ws.onmessage = (event) => {
    try {
      handleSocketMessage(JSON.parse(event.data))
    } catch (e) {
      console.error('Error parsing WS message:', e)
    }
  }

  ws.onclose = () => {
    console.log('WebSocket Disconnected')
    if (manualSocketClose || !isAuthed.value) return
    setTimeout(() => {
      if (isAuthed.value) connectWebSocket()
    }, 3000)
  }

  socket.value = ws
}

function disconnectSocket() {
  manualSocketClose = true
  try {
    socket.value?.close()
  } catch {
    /* ignore */
  }
  socket.value = null
  setTimeout(() => {
    manualSocketClose = false
  }, 0)
}

// ============================================================
// 六、处理后端推送事件
// ============================================================
function handleSocketMessage(data: any) {
  const { type, event, message, data: eventData } = data
  if (type === 'pong') return

  const lastAiMsg = messages.value.slice().reverse().find((m) => m.role === 'ai')

  if (event === 'session_created') {
    currentSessionPath.value = eventData.path
    const parts = String(eventData.path).split(/output[\\/]/)
    if (parts.length > 1) {
      currentSessionUrl.value = `${API_BASE}/outputs/${parts[1].replace(/\\/g, '/')}`
    }
    isSidebarOpen.value = true
    fetchFiles()
  } else if (event === 'tool_start') {
    if (currentSessionPath.value) {
      fetchFiles()
      setTimeout(fetchFiles, 2000)
    }
    if (lastAiMsg) {
      if (!lastAiMsg.logs) lastAiMsg.logs = []
      lastAiMsg.logs.push({
        type: 'tool',
        title: eventData.tool_name ? `调用工具：${eventData.tool_name}` : '调用工具',
        details: eventData.args,
        timestamp: new Date().toLocaleTimeString('zh-CN', { hour12: false })
      })
      if (eventData.args && eventData.args.filename && currentSessionUrl.value) {
        if (!lastAiMsg.files) lastAiMsg.files = []
        const fileUrl = `${currentSessionUrl.value}/${eventData.args.filename}`
        if (!lastAiMsg.files.find((f) => f.name === eventData.args.filename)) {
          lastAiMsg.files.push({
            name: eventData.args.filename,
            path: eventData.args.filename,
            url: fileUrl
          })
        }
      }
    }
  } else if (event === 'assistant_call') {
    if (currentSessionPath.value) fetchFiles()
    if (lastAiMsg) {
      if (!lastAiMsg.logs) lastAiMsg.logs = []
      lastAiMsg.logs.push({
        type: 'agent',
        title: eventData.assistant_name ? `委派子智能体：${eventData.assistant_name}` : '委派子智能体',
        details: eventData.args,
        timestamp: new Date().toLocaleTimeString('zh-CN', { hour12: false })
      })
    }
  } else if (event === 'task_result') {
    if (lastAiMsg) {
      lastAiMsg.content = eventData.result
    } else {
      messages.value.push({ role: 'ai', content: eventData.result, timestamp: Date.now() })
    }
    status.value = 'idle'
    fetchFiles()
  } else if (event === 'error') {
    messages.value.push({
      role: 'system',
      content: `Error: ${message}`,
      timestamp: Date.now()
    })
    status.value = 'idle'
  }

  scrollToBottom()
}

// ============================================================
// 七、文件列表 / 上传 / 下载
// ============================================================
async function fetchFiles() {
  if (!currentSessionPath.value) return
  try {
    const res = await axios.get(`${API_BASE}/api/files`, {
      params: { path: currentSessionPath.value }
    })
    if (res.data.files) {
      fileList.value = res.data.files.map((f: any) => ({
        ...f,
        url: `${API_BASE}/api/download?path=${encodeURIComponent(f.path)}`
      }))
    }
  } catch (e) {
    console.error('Failed to fetch files', e)
  }
}

function triggerFileUpload() {
  fileInputRef.value?.click()
}

function handleFileChange(event: Event) {
  const target = event.target as HTMLInputElement
  if (target.files && target.files.length > 0) {
    selectedFiles.value = [...selectedFiles.value, ...Array.from(target.files)]
    toast(`已添加 ${target.files.length} 个附件`, 'info')
    target.value = ''
  }
}

function removeFile(index: number) {
  selectedFiles.value.splice(index, 1)
}

// ============================================================
// 八、发送任务
// ============================================================
async function sendMessage() {
  if (!isAuthed.value) return
  if ((!inputQuery.value.trim() && selectedFiles.value.length === 0) || status.value === 'running') return

  const query = inputQuery.value
  inputQuery.value = ''
  autoGrow()
  status.value = 'running'

  messages.value.push({ role: 'user', content: query, timestamp: Date.now() })
  messages.value.push({ role: 'ai', content: '', logs: [], files: [], timestamp: Date.now() })
  scrollToBottom()

  // 附件上传
  if (selectedFiles.value.length > 0) {
    const lastAiMsg = messages.value[messages.value.length - 1]
    if (lastAiMsg && lastAiMsg.role === 'ai') {
      if (!lastAiMsg.logs) lastAiMsg.logs = []
      lastAiMsg.logs.push({
        type: 'info',
        title: `正在上传 ${selectedFiles.value.length} 个附件…`,
        details: selectedFiles.value.map((f) => ({ name: f.name, size: f.size })),
        timestamp: new Date().toLocaleTimeString('zh-CN', { hour12: false })
      })
    }
    try {
      const formData = new FormData()
      formData.append('thread_id', currentThreadId.value)
      selectedFiles.value.forEach((file) => formData.append('files', file))
      await axios.post(`${API_BASE}/api/upload`, formData, {
        headers: { 'Content-Type': 'multipart/form-data' }
      })
      selectedFiles.value = []
      if (lastAiMsg && lastAiMsg.logs) {
        lastAiMsg.logs.push({
          type: 'success',
          title: '附件上传成功',
          details: null,
          timestamp: new Date().toLocaleTimeString('zh-CN', { hour12: false })
        })
      }
    } catch (e: any) {
      console.error('Upload failed', e)
      if (lastAiMsg && lastAiMsg.logs) {
        lastAiMsg.logs.push({
          type: 'error',
          title: '附件上传失败',
          details: e.message || '未知错误',
          timestamp: new Date().toLocaleTimeString('zh-CN', { hour12: false })
        })
      }
    }
  }

  try {
    const payload: any = { query }
    if (currentThreadId.value) payload.thread_id = currentThreadId.value
    const res = await axios.post(`${API_BASE}/api/task`, payload)
    if (res.data && res.data.thread_id) {
      currentThreadId.value = res.data.thread_id
    }
  } catch (error: any) {
    console.error('Request failed:', error)
    let errorMsg = '任务发送失败。'
    if (error.message) errorMsg += `（${error.message}）`
    if (error.response && error.response.data) {
      errorMsg += ` 服务端返回：${JSON.stringify(error.response.data)}`
    }
    messages.value.push({ role: 'system', content: errorMsg, timestamp: Date.now() })
    status.value = 'idle'
    scrollToBottom()
  }
}

// ============================================================
// 九、会话管理
// ============================================================
function resetSessionState() {
  messages.value = []
  status.value = 'idle'
  currentThreadId.value = crypto.randomUUID()
  currentSessionPath.value = ''
  currentSessionUrl.value = ''
  fileList.value = []
  isSidebarOpen.value = false
  inputQuery.value = ''
  selectedFiles.value = []
  openLogs.value = {}
}

function startNewChat() {
  if (status.value === 'running') {
    toast('当前任务仍在执行，已为你开启新会话', 'info')
  }
  disconnectSocket()
  resetSessionState()
  connectWebSocket()
  if (!status.value) {
    /* noop */
  }
  toast('已开启新会话，可上传文件或直接提问', 'info')
  nextTick(() => composerTextarea.value?.focus())
}

function renderMarkdown(text: string): string {
  if (!text) return ''
  return marked(text) as string
}

// ============================================================
// 十、生命周期
// ============================================================
onMounted(() => {
  const persisted = localStorage.getItem(AUTH_STORAGE_KEY) === '1'
  const session = sessionStorage.getItem(AUTH_STORAGE_KEY) === '1'
  if (rememberedAccount.value) {
    account.value = rememberedAccount.value
    remember.value = true
  }
  if (persisted || session) {
    isAuthed.value = true
    connectWebSocket()
  }
})

onBeforeUnmount(() => {
  manualSocketClose = true
  socket.value?.close()
})
</script>

<template>
  <!-- ==================== 登录视图 ==================== -->
  <div id="view-login" class="view view-login" :class="{ 'is-active': !isAuthed }">
    <div class="login-shell">
      <section class="login-art">
        <div class="mesh" aria-hidden="true"><i class="m1"></i><i class="m2"></i><i class="m3"></i><i class="m4"></i></div>
        <div class="art-grid" aria-hidden="true"></div>
        <div class="art-top">
          <svg class="brand-mark" width="30" height="30" viewBox="0 0 32 32" aria-hidden="true">
            <rect x="1" y="1" width="30" height="30" rx="9" fill="#fff" fill-opacity=".16" stroke="rgba(255,255,255,.35)" />
            <path d="M10 11.5c0-1.4 1.1-2.5 2.5-2.5h3.2c3.7 0 6.3 2.5 6.3 6.1s-2.6 6.1-6.3 6.1h-3.2A2.5 2.5 0 0 1 10 18.7V11.5Z" fill="#fff" />
            <circle cx="16" cy="15.2" r="2.1" fill="#635bff" />
          </svg>
          <span class="brand-name" style="color: #fff">DeepAgents</span>
        </div>
        <div class="art-mid">
          <h1>让多智能体团队<br />替你把研究做完。<em>Research, finished.</em></h1>
          <p>搜索、取数、写作、排版，由智能体协作串联成一条完整的调研流水线，会后直接产出可交付的 Markdown、PDF 与 Excel。</p>
          <div class="art-stats">
            <div class="stat"><b>4.2k+</b><span>累计研究任务</span></div>
            <div class="stat"><b>98.6%</b><span>任务完成率</span></div>
            <div class="stat"><b>18s</b><span>平均首轮回答</span></div>
          </div>
        </div>
        <div class="art-bottom">
          <span class="stack" aria-hidden="true"><i></i><i></i><i></i></span>
          已经有 1,200+ 研究团队在使用 DeepAgents
        </div>
      </section>

      <section class="login-form-side">
        <div class="login-card stagger">
          <h2>欢迎回来</h2>
          <p class="login-sub">
            登录以继续你的深度研究工作台 ·
            <a href="#" @click.prevent="toast('演示环境：注册流程尚未接入', 'info')">还没有账号？立即注册</a>
          </p>

          <form @submit.prevent="handleLogin" novalidate>
            <div class="field" :class="{ valid: accountValid, error: loginErrors.account }">
              <input id="account" v-model="account" type="text" placeholder=" " autocomplete="username"
                aria-label="邮箱 / 手机号" @blur="onAccountBlur" />
              <label for="account">邮箱 / 手机号</label>
              <span class="f-ico">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="2.5" y="4.5" width="19" height="15" rx="2.5" /><path d="m3 7 9 6 9-6" /></svg>
              </span>
              <svg class="f-ok" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m5 13 4 4L19 7" /></svg>
              <div class="field-err">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9" /><path d="M12 8v4M12 16h.01" /></svg>
                <span>{{ loginErrors.account || '请输入邮箱或手机号' }}</span>
              </div>
            </div>

            <div class="field has-pw" :class="{ valid: passwordValid, error: loginErrors.password }">
              <input id="password" v-model="password" :type="showPassword ? 'text' : 'password'" placeholder=" "
                autocomplete="current-password" aria-label="密码" @blur="onPasswordBlur" />
              <label for="password">密码</label>
              <button class="toggle-pw" type="button" @click="showPassword = !showPassword"
                :aria-label="showPassword ? '隐藏密码' : '显示密码'" title="显示 / 隐藏密码">
                <svg v-if="!showPassword" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7Z" /><circle cx="12" cy="12" r="3" /></svg>
                <svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9.9 4.2A10.9 10.9 0 0 1 12 4c6.4 0 10 7 10 7a18 18 0 0 1-2.4 3.3" /><path d="M6.1 6.1A17.9 17.9 0 0 0 2 12s3.6 7 10 7a10.6 10.6 0 0 0 4.5-1" /><path d="M2 2l20 20" /><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2" /></svg>
              </button>
              <div class="field-err">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9" /><path d="M12 8v4M12 16h.01" /></svg>
                <span>{{ loginErrors.password || '请输入密码' }}</span>
              </div>
            </div>

            <div class="form-row">
              <label class="check"><input v-model="remember" type="checkbox" /> 记住我</label>
              <button class="link-btn" type="button" @click="toast('演示环境：找回密码邮件尚未接入', 'info')">忘记密码？</button>
            </div>

            <button class="submit-btn" :class="{ 'is-loading': loggingIn, 'is-success': loginDone }" type="submit" :disabled="loggingIn">
              <span class="btn-label">{{ loggingIn ? '登录中…' : '登录' }}</span>
              <svg class="btn-arrow" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14" /><path d="m13 6 6 6-6 6" /></svg>
            </button>

            <div class="form-note" :class="{ show: !!rememberedAccount && !account }">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9" /><path d="M12 8v8M8 12h8" /></svg>
              已记住该账号（{{ rememberedAccount }}），下次登录自动填充
            </div>
          </form>

          <div class="divider">或使用以下方式登录</div>
          <div class="third-party">
            <button class="tp-btn" type="button" @click="toast('演示环境：第三方登录尚未接入', 'info')" aria-label="使用 Google 登录">
              <svg viewBox="0 0 24 24"><path fill="#4285F4" d="M23.5 12.3c0-.8-.1-1.6-.2-2.3H12v4.5h6.5a5.6 5.6 0 0 1-2.4 3.6v3h3.9c2.3-2.1 3.5-5.2 3.5-8.8Z" /><path fill="#34A853" d="M12 24c3.2 0 5.9-1.1 7.9-2.9l-3.9-3c-1.1.7-2.4 1.2-4 1.2a7 7 0 0 1-6.6-4.8H1.4v3.1A12 12 0 0 0 12 24Z" /><path fill="#FBBC05" d="M5.4 14.5a7.2 7.2 0 0 1 0-4.9V6.5H1.4a12 12 0 0 0 0 11l4-3Z" /><path fill="#EA4335" d="M12 4.8c1.8 0 3.3.6 4.6 1.8l3.4-3.4A12 12 0 0 0 1.4 6.5l4 3.1A7 7 0 0 1 12 4.8Z" /></svg>
            </button>
            <button class="tp-btn" type="button" @click="toast('演示环境：第三方登录尚未接入', 'info')" aria-label="使用 Apple 登录">
              <svg viewBox="0 0 24 24" fill="#111"><path d="M16.4 12.7c0-2.4 2-3.6 2.1-3.7-1.1-1.7-2.9-1.9-3.5-1.9-1.5-.2-2.9.9-3.7.9-.8 0-1.9-.9-3.2-.9-1.6 0-3.1 1-4 2.4-1.7 3-.4 7.4 1.2 9.8.8 1.2 1.8 2.5 3.1 2.4 1.2-.1 1.7-.8 3.2-.8s1.9.8 3.2.8c1.3 0 2.2-1.2 3-2.4a10.6 10.6 0 0 0 1.3-2.8c-.1 0-2.7-1-2.7-3.8ZM14 5.4c.7-.8 1.1-2 1-3.1-1 0-2.2.7-2.9 1.5-.6.7-1.2 1.9-1 3 1.1.1 2.2-.6 2.9-1.4Z" /></svg>
            </button>
            <button class="tp-btn" type="button" @click="toast('演示环境：第三方登录尚未接入', 'info')" aria-label="使用微信登录">
              <svg viewBox="0 0 24 24"><path fill="#1AAD19" d="M9.1 3.2C5 3.2 1.7 5.9 1.7 9.3c0 1.9 1.1 3.6 2.8 4.8l-.7 2.1 2.5-1.3c.9.2 1.8.4 2.8.4h.6a5.4 5.4 0 0 1-.2-1.5c0-3.2 3.1-5.8 6.9-5.8h.6C15.9 5.2 12.8 3.2 9.1 3.2Zm-2.6 3.4a1 1 0 1 1 0 2 1 1 0 0 1 0-2Zm5 0a1 1 0 1 1 0 2 1 1 0 0 1 0-2Z" /><path fill="#1AAD19" d="M22.3 13.9c0-2.8-2.8-5.1-6.2-5.1s-6.2 2.3-6.2 5.1 2.8 5.1 6.2 5.1c.7 0 1.5-.1 2.2-.3l2 1-.6-1.7c1.6-1 2.6-2.5 2.6-4.1Zm-8.2-1.3a.9.9 0 1 1 0-1.7.9.9 0 0 1 0 1.7Zm4 0a.9.9 0 1 1 0-1.7.9.9 0 0 1 0 1.7Z" /></svg>
            </button>
          </div>

          <div class="login-foot">
            继续即表示同意
            <a href="#" @click.prevent="toast('演示环境：服务条款尚未接入', 'info')">服务条款</a>
            与
            <a href="#" @click.prevent="toast('演示环境：隐私政策尚未接入', 'info')">隐私政策</a>
          </div>
          <p class="login-demo-note">演示登录：任意格式正确的邮箱 / 手机号 + 至少 6 位密码即可进入，尚未接入后端鉴权。</p>
        </div>
      </section>
    </div>
  </div>

  <!-- ==================== 工作台主视图 ==================== -->
  <div id="view-main" class="view view-main" :class="{ 'is-active': isAuthed }">
    <!-- 顶栏 -->
    <header class="topbar">
      <a class="brand" href="#" @click.prevent="startNewChat" aria-label="DeepAgents 首页">
        <svg class="brand-mark" viewBox="0 0 32 32" aria-hidden="true">
          <defs>
            <linearGradient id="lgA" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0" stop-color="#635bff" /><stop offset=".55" stop-color="#00c3ff" /><stop offset="1" stop-color="#ff6b6b" />
            </linearGradient>
          </defs>
          <rect x="1" y="1" width="30" height="30" rx="9" fill="url(#lgA)" />
          <path d="M10 11.5c0-1.4 1.1-2.5 2.5-2.5h3.2c3.7 0 6.3 2.5 6.3 6.1s-2.6 6.1-6.3 6.1h-3.2A2.5 2.5 0 0 1 10 18.7V11.5Z" fill="#fff" fill-opacity=".96" />
          <circle cx="16" cy="15.2" r="2.1" fill="#635bff" />
        </svg>
        <span class="brand-name">Deep<b>Agents</b></span>
      </a>
      <span class="topbar-divider" aria-hidden="true"></span>
      <button class="session-chip" type="button" @click="toast('当前会话：' + sessionLabel, 'info')">
        <span class="dot" aria-hidden="true"></span>
        <span>{{ sessionLabel }}</span>
      </button>
      <div class="topbar-spacer"></div>

      <div class="modeswitch" role="group" aria-label="研究模式">
        <button type="button" :aria-pressed="mode === '深度研究'" @click="mode = '深度研究'; toast('已切换为「深度研究」模式', 'info')">深度研究</button>
        <button type="button" :aria-pressed="mode === '快速问答'" @click="mode = '快速问答'; toast('已切换为「快速问答」模式', 'info')">快速问答</button>
      </div>

      <button class="tb-btn" type="button" aria-label="设置" @click="toast('演示环境：设置面板尚未接入', 'info')">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.87l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1.87-.34 1.7 1.7 0 0 0-1 1.56V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.56 1.7 1.7 0 0 0-1.88.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.7 1.7 0 0 0 .34-1.87 1.7 1.7 0 0 0-1.56-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.56-1.1 1.7 1.7 0 0 0-.34-1.88l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.7 1.7 0 0 0 1.87.34H9a1.7 1.7 0 0 0 1-1.56V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.56 1.7 1.7 0 0 0 1.88-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.7 1.7 0 0 0-.34 1.87V9a1.7 1.7 0 0 0 1.56 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z" /></svg>
      </button>
      <button class="tb-btn" type="button" aria-label="通知" @click="toast('演示环境：通知中心尚未接入', 'info')">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" /><path d="M13.7 21a2 2 0 0 1-3.4 0" /></svg>
        <span class="badge-dot"></span>
      </button>
      <button class="tb-btn" type="button" aria-label="新会话" @click="startNewChat">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14M5 12h14" /></svg>
      </button>

      <button class="login-entry" type="button" @click="handleLogout" title="退出登录">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4" /><path d="M10 17l5-5-5-5" /><path d="M15 12H3" /></svg>
        退出登录
      </button>
      <button class="avatar" type="button" :title="account || 'Jun'" aria-label="用户菜单" @click="toast('演示环境：账号设置尚未接入', 'info')">J</button>
    </header>

    <div class="workspace">
      <button class="sidebar-again" type="button" :class="{ show: !isSidebarOpen && !!currentSessionPath }" @click="isSidebarOpen = true" aria-label="打开 Session Files 侧栏">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z" /></svg>
        Session Files
      </button>

      <!-- 聊天列 -->
      <main class="chat-col">
        <div class="mesh-top" aria-hidden="true">
          <div class="mesh"><i class="m1"></i><i class="m2"></i><i class="m3"></i><i class="m4"></i></div>
        </div>

        <div class="scroll" ref="scrollRef">
          <!-- 欢迎屏 -->
          <section class="welcome" :class="{ hidden: !isWelcomeScreen }">
            <div class="welcome-eyebrow enter d1"><span class="pulse"></span>DeepAgents Research · 多智能体研究工作台</div>
            <h1 class="enter d2">Hello, <span class="grad">User</span></h1>
            <p class="enter d3">How can I help you today?</p>
            <div class="welcome-suggests stagger">
              <button class="sug" type="button" @click="useSuggestion('拆解一家上市公司的年报核心指标并生成对比表格')">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18" /><path d="M7 13l4-4 3 3 5-6" /></svg>
                拆解财报核心指标
              </button>
              <button class="sug" type="button" @click="useSuggestion('调研某药品在医院与零售渠道的市场表现并输出报告')">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z" /><path d="M14 2v6h6" /></svg>
                渠道市场调研
              </button>
              <button class="sug" type="button" @click="useSuggestion('检索行业政策变化，梳理对创新药企的影响')">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
                政策影响梳理
              </button>
            </div>

            <div class="caps stagger">
              <button class="cap" type="button" @click="useSuggestion('联网检索沃华医药 2024 年心脑血管产品线的市场与政策动态，标注信息来源')">
                <span class="cap-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg></span>
                <span class="cap-t">网络搜索</span>
                <span class="cap-d">Tavily 实时检索政策、竞品与行业动态，结果带来源标注。</span>
                <span class="cap-go">立即使用 <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6" /></svg></span>
              </button>
              <button class="cap" type="button" @click="useSuggestion('按渠道拆解 2024 年心可舒片销售额，生成季度对比表格与同比增长')">
                <span class="cap-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="5.5" rx="7.5" ry="3" /><path d="M4.5 5.5v13c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3v-13" /><path d="M4.5 12c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3" /></svg></span>
                <span class="cap-t">数据库查询</span>
                <span class="cap-d">直连 MySQL 销售库，按渠道 / 季度自动聚合取数。</span>
                <span class="cap-go">立即使用 <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6" /></svg></span>
              </button>
              <button class="cap" type="button" @click="useSuggestion('在 RAGFlow 知识库检索心可舒片的临床研究证据与药典标准')">
                <span class="cap-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19V6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v13" /><path d="M4 19h16" /><path d="M8 8h8M8 12h5" /></svg></span>
                <span class="cap-t">RAGFlow 知识库</span>
                <span class="cap-d">检索内部研报、临床资料与药典原文，附段落引用。</span>
                <span class="cap-go">立即使用 <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6" /></svg></span>
              </button>
              <button class="cap" type="button" @click="useSuggestion('把以上研究结果整理成一份 1000 字以上的 Markdown 报告并导出 PDF')">
                <span class="cap-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8Z" /><path d="M14 3v5h5" /><path d="M9 13h6M9 17h4" /></svg></span>
                <span class="cap-t">报告生成</span>
                <span class="cap-d">一键成稿 Markdown，同步导出 PDF 与 Excel 附录。</span>
                <span class="cap-go">立即使用 <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6" /></svg></span>
              </button>
            </div>
          </section>

          <!-- 对话区 -->
          <div class="inner" :class="{ hidden: isWelcomeScreen }">
            <template v-for="(msg, index) in messages" :key="index">
              <!-- 用户消息 -->
              <div v-if="msg.role === 'user'" class="msg msg-user">
                <div>
                  <div class="bubble-user">{{ msg.content }}</div>
                  <div class="msg-user-meta"><span>{{ formatClock(msg.timestamp) }}</span><span>·</span><span>已发送</span></div>
                </div>
              </div>

              <!-- AI 消息 -->
              <div v-else-if="msg.role === 'ai'" class="msg msg-ai">
                <div class="ai-avatar" aria-hidden="true">
                  <svg viewBox="0 0 32 32">
                    <defs><linearGradient :id="'lgB' + index" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#635bff" /><stop offset="1" stop-color="#00c3ff" /></linearGradient></defs>
                    <rect x="2" y="2" width="28" height="28" rx="9" :fill="'url(#lgB' + index + ')'" />
                    <path d="M11 21.5V13l5 4.2L21 13v8.5" stroke="#fff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" fill="none" />
                  </svg>
                </div>
                <div class="ai-body">
                  <div class="ai-head">
                    <span class="ai-name">DeepAgents</span>
                    <span class="ai-tool">
                      <svg viewBox="0 0 24 24" width="10" height="10" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1" /></svg>
                      多智能体协作
                    </span>
                    <span class="ai-time">{{ formatClock(msg.timestamp) }}</span>
                  </div>

                  <!-- 思考过程 -->
                  <section v-if="msg.logs && msg.logs.length > 0" class="process" :data-open="logsOpen(index)">
                    <button class="process-toggle" type="button" :aria-expanded="logsOpen(index)" @click="toggleLogs(index)">
                      <svg class="process-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3a5 5 0 0 0-3 9v3h6v-3a5 5 0 0 0-3-9Z" /><path d="M9 18h6M10 21h4" /></svg>
                      <span class="pt-label">{{ status === 'running' && index === messages.length - 1 ? '智能体团队执行中…' : '查看思考过程 / View thought process' }}</span>
                      <span class="process-meta">
                        <span class="tick"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.6" stroke-linecap="round" stroke-linejoin="round"><path d="m5 13 4 4L19 7" /></svg></span>
                        {{ msg.logs.length }} 个步骤
                      </span>
                      <svg class="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6" /></svg>
                    </button>
                    <div class="process-body">
                      <div>
                        <ul class="process-list">
                          <li v-for="(log, li) in msg.logs" :key="li" class="log">
                            <span class="log-ico">
                              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
                            </span>
                            <div class="log-main">
                              <div class="log-top">
                                <span class="log-title">{{ log.title }}</span>
                                <code class="log-tool">{{ log.type }}</code>
                                <span class="log-time">{{ log.timestamp }}</span>
                              </div>
                              <div v-if="log.details" class="log-code"><pre>{{ formatDetails(log.details) }}</pre></div>
                            </div>
                          </li>
                        </ul>
                      </div>
                    </div>
                  </section>

                  <!-- 正在思考占位 -->
                  <div v-if="!msg.content && status === 'running' && index === messages.length - 1" class="thinking">
                    <span class="spinner"></span>
                    <span>智能体团队正在检索与分析</span>
                    <span class="dots"><i></i><i></i><i></i></span>
                  </div>

                  <!-- Markdown 正文 -->
                  <article v-if="msg.content" class="md" v-html="renderMarkdown(msg.content)"></article>

                  <!-- 文件卡片 -->
                  <div v-if="msg.files && msg.files.length > 0" class="files">
                    <div class="files-head">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><path d="M7 10l5 5 5-5" /><path d="M12 15V3" /></svg>
                      已生成 {{ msg.files.length }} 个文件
                    </div>
                    <div class="file-grid stagger">
                      <a v-for="file in msg.files" :key="file.name" class="file-card" :href="file.url" target="_blank" :download="file.name">
                        <span class="file-ico" :class="fileExtClass(file.name)">
                          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z" /><path d="M14 2v6h6" /><path d="M9 15h6M9 18h4" /></svg>
                        </span>
                        <span class="file-meta">
                          <span class="file-name">{{ file.name }}</span>
                          <span class="file-sub">{{ fileExtLabel(file.name) }}</span>
                        </span>
                        <span class="file-go"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 17 17 7" /><path d="M9 7h8v8" /></svg></span>
                      </a>
                    </div>
                  </div>
                </div>
              </div>

              <!-- 系统 / 错误消息 -->
              <div v-else class="msg msg-system">
                <div class="bubble-system">{{ msg.content }}</div>
              </div>
            </template>
          </div>
        </div>

        <!-- 输入区 -->
        <div class="composer-wrap" id="composerWrap">
          <div class="composer">
            <div class="chips" v-if="selectedFiles.length > 0">
              <div class="chip" v-for="(file, index) in selectedFiles" :key="index">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="m21.4 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" /></svg>
                <span>{{ file.name }}</span>
                <button type="button" aria-label="移除附件" @click="removeFile(index)">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12" /></svg>
                </button>
              </div>
            </div>

            <div class="input-shell" :class="{ 'is-busy': status === 'running' }">
              <input ref="fileInputRef" type="file" multiple class="sr" tabindex="-1" aria-hidden="true" @change="handleFileChange" />
              <textarea id="input" ref="composerTextarea" v-model="inputQuery" rows="1" placeholder="Enter a prompt here"
                aria-label="输入你的研究问题" @keydown="handleComposerKeydown" @input="autoGrow"></textarea>
              <div class="input-actions">
                <button class="icon-btn" type="button" aria-label="添加附件" title="添加附件" @click="triggerFileUpload" :disabled="status === 'running'">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="m21.4 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" /></svg>
                </button>
                <button class="icon-btn" type="button" aria-label="研究工具" @click="toast('演示环境：工具配置尚未接入', 'info')">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16M4 12h16M4 17h10" /></svg>
                </button>
                <span class="composer-hint"><kbd>Enter</kbd> 发送 · <kbd>Shift</kbd><kbd>Enter</kbd> 换行</span>
                <button class="send-btn" type="button" :disabled="(!inputQuery.trim() && selectedFiles.length === 0) || status === 'running'" @click="sendMessage" aria-label="发送">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5" /><path d="m5 12 7-7 7 7" /></svg>
                  {{ status === 'running' ? '执行中' : '发送' }}
                </button>
              </div>
              <div class="run-progress" aria-hidden="true"><i></i></div>
            </div>
            <p class="disclaimer">DeepAgents may display inaccurate info, including people, so double-check its responses.</p>
          </div>
        </div>
      </main>

      <!-- 会话文件侧栏 -->
      <aside class="sidebar" :class="{ 'is-collapsed': !isSidebarOpen }" aria-label="Session Files">
        <div class="sb-head">
          <span class="sb-title">Session Files <span class="count">{{ fileList.length }}</span></span>
          <div class="sb-actions">
            <button class="sb-btn" type="button" @click="fetchFiles" aria-label="刷新文件列表" title="刷新">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-2.64-6.36" /><path d="M21 3v6h-6" /></svg>
            </button>
            <button class="sb-btn" type="button" @click="isSidebarOpen = false" aria-label="关闭侧栏" title="关闭">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18M6 6l12 12" /></svg>
            </button>
          </div>
        </div>
        <div class="sb-body">
          <div v-if="fileList.length === 0" class="sb-empty">
            <div class="ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z" /></svg></div>
            <p>No files generated yet.</p>
            <small>发起一次研究任务后，产出文件会自动出现在这里。</small>
          </div>
          <div v-else>
            <a v-for="file in fileList" :key="file.path" class="sb-item" :href="file.url" target="_blank" :download="file.name">
              <span class="sb-ico file-ico" :class="fileExtClass(file.name)">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z" /><path d="M14 2v6h6" /><path d="M9 15h6M9 18h4" /></svg>
              </span>
              <span class="sb-meta">
                <span class="sb-name">{{ file.name }}</span>
                <span class="sb-sub">{{ fileExtLabel(file.name) }}<span v-if="formatSize(file.size)"> · {{ formatSize(file.size) }}</span></span>
              </span>
            </a>
          </div>
        </div>
        <div class="sb-foot">
          <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9" /><path d="M12 8v4l2.5 2.5" /></svg>
          会话存储沙箱 · 24h 后自动清理
        </div>
      </aside>
    </div>

    <!-- 新会话悬浮按钮 -->
    <button class="newchat-fab" type="button" @click="startNewChat" aria-label="新会话" title="新会话">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14M5 12h14" /></svg>
      新会话
    </button>
  </div>

  <!-- 轻提示 -->
  <div class="toasts" aria-live="polite">
    <div v-for="t in toasts" :key="t.id" class="toast" :class="{ info: t.info }">
      <span class="t-ico">
        <svg v-if="t.info" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8h.01" /></svg>
        <svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round"><path d="m5 13 4 4L19 7" /></svg>
      </span>
      <span>{{ t.msg }}</span>
    </div>
  </div>
</template>

<style>


/* ============ Tokens ============ */
:root{
  --brand:#635bff;
  --brand-600:#4f46e5;
  --brand-700:#3d34d6;
  --brand-soft:#f4f3ff;
  --cyan:#00d4ff;
  --coral:#ff6b6b;
  --amber:#ffd166;
  --ink:#0a2540;
  --ink-2:#3c4257;
  --ink-3:#697386;
  --ink-4:#8792a2;
  --line:#e3e8ee;
  --line-2:#edf0f5;
  --bg:#ffffff;
  --bg-sub:#f6f9fc;
  --ok:#04724d;
  --ok-bg:#e6f6ef;
  --warn:#b76e00;
  --warn-bg:#fff6e6;
  --danger:#c53030;
  --danger-bg:#fdecec;
  --r-sm:8px;
  --r-md:12px;
  --r-lg:16px;
  --r-xl:20px;
  --r-pill:999px;
  --sh-xs:0 1px 2px rgba(50,50,93,.08), 0 1px 1px rgba(0,0,0,.03);
  --sh-sm:0 2px 5px -1px rgba(50,50,93,.10), 0 1px 3px -1px rgba(0,0,0,.06);
  --sh-md:0 6px 16px -6px rgba(50,50,93,.16), 0 3px 8px -4px rgba(0,0,0,.10);
  --sh-lg:0 30px 60px -12px rgba(50,50,93,.25), 0 18px 36px -18px rgba(0,0,0,.30);
  --sh-pop:0 8px 24px -6px rgba(50,50,93,.20), 0 4px 12px -6px rgba(0,0,0,.12);
  --ease:cubic-bezier(.25,.1,.25,1);
  --ease-out:cubic-bezier(.16,1,.3,1);
  --t-fast:180ms;
  --t:240ms;
  --t-slow:300ms;
  --font:"Inter","SF Pro","PingFang SC","Helvetica Neue","Microsoft YaHei",sans-serif;
  --mono:"SFMono-Regular","JetBrains Mono",Menlo,Consolas,"Courier New",monospace;
  --shell-h:100vh;
  --shell-dvh:100dvh;
}
*{box-sizing:border-box;}
html,body{margin:0;padding:0;overflow-x:hidden;}
body{
  font-family:var(--font);
  color:var(--ink);
  background:var(--bg);
  -webkit-font-smoothing:antialiased;
  text-rendering:optimizeLegibility;
  font-size:14px;
  line-height:1.55;
}
button{font-family:inherit;cursor:pointer;border:none;background:none;color:inherit;}
input,textarea{font-family:inherit;color:inherit;}
a{color:var(--brand);text-decoration:none;}
svg{display:block;}
:focus-visible{outline:2px solid var(--brand);outline-offset:2px;border-radius:6px;}
.hidden{display:none !important;}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap;}

/* ============ Mesh gradient art ============ */
.mesh{position:absolute;inset:-20%;pointer-events:none;z-index:0;filter:blur(70px);opacity:.5;}
.mesh i{position:absolute;display:block;border-radius:50%;}
.mesh .m1{width:52vw;height:52vw;left:-8vw;top:-14vw;background:radial-gradient(circle at 30% 30%,#635bff 0%,rgba(99,91,255,0) 68%);animation:drift1 22s var(--ease) infinite alternate;}
.mesh .m2{width:44vw;height:44vw;right:-6vw;top:-10vw;background:radial-gradient(circle at 60% 40%,#00d4ff 0%,rgba(0,212,255,0) 66%);animation:drift2 19s var(--ease) infinite alternate;}
.mesh .m3{width:38vw;height:38vw;left:22vw;bottom:-16vw;background:radial-gradient(circle at 50% 50%,#ff6b6b 0%,rgba(255,107,107,0) 65%);animation:drift3 24s var(--ease) infinite alternate;}
.mesh .m4{width:30vw;height:30vw;right:14vw;bottom:-12vw;background:radial-gradient(circle at 50% 50%,#ffd166 0%,rgba(255,209,102,0) 64%);animation:drift4 20s var(--ease) infinite alternate;}
@keyframes drift1{0%{transform:translate3d(0,0,0) scale(1) rotate(0)}100%{transform:translate3d(9vw,6vw,0) scale(1.18) rotate(24deg)}}
@keyframes drift2{0%{transform:translate3d(0,0,0) scale(1.05) rotate(0)}100%{transform:translate3d(-8vw,7vw,0) scale(.9) rotate(-20deg)}}
@keyframes drift3{0%{transform:translate3d(0,0,0) scale(1) rotate(0)}100%{transform:translate3d(-7vw,-6vw,0) scale(1.15) rotate(18deg)}}
@keyframes drift4{0%{transform:translate3d(0,0,0) scale(.95) rotate(0)}100%{transform:translate3d(6vw,-8vw,0) scale(1.2) rotate(-26deg)}}

/* ============ View shells ============ */
.view{display:none;}
.view.is-active{display:block;}
#view-main.is-active{display:flex;flex-direction:column;height:var(--shell-h);min-height:600px;position:relative;isolation:isolate;}
#view-login.is-active{display:block;min-height:var(--shell-h);position:relative;isolation:isolate;}
@supports (height:100dvh){
  #view-main.is-active{height:100dvh;}
  #view-login.is-active{min-height:100dvh;}
}

/* ============ Top bar ============ */
.topbar{
  position:relative;z-index:20;flex:none;
  height:60px;display:flex;align-items:center;gap:16px;
  padding:0 20px;background:rgba(255,255,255,.86);
  backdrop-filter:saturate(180%) blur(14px);
  -webkit-backdrop-filter:saturate(180%) blur(14px);
  border-bottom:1px solid var(--line-2);
}
.brand{display:flex;align-items:center;gap:10px;padding:6px 6px;border-radius:var(--r-sm);transition:background var(--t-fast) var(--ease);}
.brand:hover{background:var(--bg-sub);}
.brand-mark{width:26px;height:26px;flex:none;filter:drop-shadow(0 4px 8px rgba(99,91,255,.35));}
.brand-name{font-size:15.5px;font-weight:600;letter-spacing:-.02em;color:var(--ink);}
.brand-name b{font-weight:600;background:linear-gradient(96deg,#635bff,#00a6ff 55%,#635bff);-webkit-background-clip:text;background-clip:text;color:transparent;}
.topbar-divider{width:1px;height:22px;background:var(--line);}
.session-chip{
  display:flex;align-items:center;gap:8px;padding:6px 12px 6px 10px;border-radius:var(--r-pill);
  border:1px solid var(--line);background:#fff;font-size:13px;color:var(--ink-2);font-weight:500;
  max-width:340px;transition:border-color var(--t-fast) var(--ease),box-shadow var(--t-fast) var(--ease),background var(--t-fast);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}
.session-chip:hover{border-color:#c9c2ff;box-shadow:0 0 0 4px rgba(99,91,255,.10);background:var(--brand-soft);}
.session-chip .dot{width:6px;height:6px;border-radius:50%;background:#1ec8a5;box-shadow:0 0 0 3px rgba(30,200,165,.16);flex:none;}
.topbar-spacer{flex:1 1 auto;}
.modeswitch{
  display:flex;align-items:center;gap:2px;padding:3px;border-radius:var(--r-pill);
  background:var(--bg-sub);border:1px solid var(--line-2);
}
.modeswitch button{
  font-size:12.5px;font-weight:500;color:var(--ink-3);padding:5px 12px;border-radius:var(--r-pill);
  transition:all var(--t-fast) var(--ease);white-space:nowrap;
}
.modeswitch button:hover{color:var(--ink);}
.modeswitch button[aria-pressed="true"]{background:#fff;color:var(--brand);box-shadow:var(--sh-xs);}
.tb-btn{
  width:34px;height:34px;border-radius:var(--r-sm);display:grid;place-items:center;color:var(--ink-3);
  transition:all var(--t-fast) var(--ease);position:relative;
}
.tb-btn:hover{background:var(--bg-sub);color:var(--ink);}
.tb-btn:active{transform:scale(.94);}
.tb-btn svg{width:17px;height:17px;}
.tb-btn .badge-dot{position:absolute;top:7px;right:8px;width:6px;height:6px;border-radius:50%;background:#ff6b6b;border:1.5px solid #fff;}
.login-entry{
  display:inline-flex;align-items:center;gap:7px;height:34px;padding:0 14px;border-radius:var(--r-pill);
  font-size:13px;font-weight:600;color:var(--brand);background:#fff;border:1px solid #d8d4ff;
  transition:all var(--t) var(--ease);white-space:nowrap;position:relative;overflow:hidden;
}
.login-entry::after{
  content:"";position:absolute;inset:0;background:linear-gradient(110deg,transparent 20%,rgba(99,91,255,.16) 45%,transparent 70%);
  transform:translateX(-130%);transition:transform 620ms var(--ease-out);
}
.login-entry:hover{border-color:#b9b2ff;box-shadow:0 6px 16px -8px rgba(99,91,255,.6);transform:translateY(-1px);}
.login-entry:hover::after{transform:translateX(130%);}
.login-entry:active{transform:translateY(0) scale(.985);}
.avatar{
  width:32px;height:32px;border-radius:50%;flex:none;
  background:linear-gradient(140deg,#635bff,#00c3ff 60%,#8f7bff);
  color:#fff;font-size:12.5px;font-weight:600;display:grid;place-items:center;
  border:2px solid #fff;box-shadow:0 0 0 1px var(--line),0 4px 10px -3px rgba(99,91,255,.6);
  transition:transform var(--t) var(--ease);
}
.avatar:hover{transform:scale(1.06);}

/* ============ Body layout ============ */
.workspace{flex:1 1 auto;display:flex;min-height:0;position:relative;z-index:1;}
.chat-col{
  flex:1 1 auto;min-width:0;display:flex;flex-direction:column;position:relative;
  background:linear-gradient(180deg,#ffffff 0%,#fafbff 100%);
}
.chat-col::before{
  content:"";position:absolute;inset:0;pointer-events:none;z-index:0;
  background-image:radial-gradient(rgba(10,37,64,.045) 1px,transparent 1px);
  background-size:24px 24px;
  -webkit-mask-image:linear-gradient(180deg,rgba(0,0,0,.7),transparent 55%);
  mask-image:linear-gradient(180deg,rgba(0,0,0,.7),transparent 55%);
}
.mesh-top{position:absolute;left:0;right:0;top:0;height:420px;overflow:hidden;z-index:0;pointer-events:none;}
.mesh-top .mesh{inset:-40% -10% -20% -10%;opacity:.34;}

/* ============ Scroll area ============ */
.scroll{flex:1 1 auto;overflow-y:auto;overflow-x:hidden;position:relative;z-index:1;scrollbar-width:thin;scrollbar-color:#d7dce5 transparent;}
.scroll::-webkit-scrollbar{width:10px;}
.scroll::-webkit-scrollbar-thumb{background:#d7dce5;border-radius:99px;border:3px solid transparent;background-clip:padding-box;}
.scroll::-webkit-scrollbar-thumb:hover{background:#c2c9d6;background-clip:padding-box;}
.inner{max-width:880px;margin:0 auto;padding:32px 32px 8px;width:100%;}

/* ============ Welcome ============ */
.welcome{padding:8vh 32px 24px;text-align:center;}
.welcome-eyebrow{
  display:inline-flex;align-items:center;gap:7px;padding:5px 12px 5px 8px;border-radius:var(--r-pill);
  background:rgba(255,255,255,.8);border:1px solid var(--line);box-shadow:var(--sh-xs);
  font-size:12px;font-weight:600;color:var(--ink-2);letter-spacing:.01em;margin-bottom:26px;
}
.welcome-eyebrow .pulse{width:6px;height:6px;border-radius:50%;background:var(--brand);box-shadow:0 0 0 0 rgba(99,91,255,.4);animation:pulse 2.4s infinite;}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(99,91,255,.45)}70%{box-shadow:0 0 0 8px rgba(99,91,255,0)}100%{box-shadow:0 0 0 0 rgba(99,91,255,0)}}
.welcome h1{
  margin:0;font-size:clamp(34px,4.4vw,56px);line-height:1.06;font-weight:600;letter-spacing:-.03em;
  color:var(--ink);
}
.welcome h1 .grad{background:linear-gradient(96deg,#635bff 0%,#00a6ff 48%,#7a5af8 100%);-webkit-background-clip:text;background-clip:text;color:transparent;}
.welcome p{margin:16px auto 0;font-size:19px;line-height:1.5;color:var(--ink-3);font-weight:400;letter-spacing:-.01em;max-width:520px;}
.welcome-suggests{display:flex;flex-wrap:wrap;gap:10px;justify-content:center;margin-top:34px;}
.sug{
  display:inline-flex;align-items:center;gap:8px;padding:9px 16px;border-radius:var(--r-pill);
  background:#fff;border:1px solid var(--line);font-size:13px;color:var(--ink-2);font-weight:500;
  box-shadow:var(--sh-xs);transition:all var(--t) var(--ease);position:relative;overflow:hidden;
}
.sug svg{width:14px;height:14px;color:var(--brand);flex:none;}
.sug::after{content:"";position:absolute;inset:0;background:linear-gradient(110deg,transparent 25%,rgba(99,91,255,.10) 50%,transparent 75%);transform:translateX(-130%);transition:transform 640ms var(--ease-out);}
.sug:hover{border-color:#c9c2ff;color:var(--ink);transform:translateY(-2px);box-shadow:0 12px 24px -14px rgba(50,50,93,.5);}
.sug:hover::after{transform:translateX(130%);}

/* ============ Capability cards (欢迎区 4 张能力卡) ============ */
.caps{
  display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;
  margin:38px auto 0;max-width:880px;text-align:left;
}
.cap{
  position:relative;display:flex;flex-direction:column;gap:10px;text-align:left;
  padding:18px 16px 16px;border-radius:16px;background:#fff;border:1px solid rgba(15,23,42,.07);
  box-shadow:var(--sh-xs),0 1px 2px rgba(50,50,93,.04);
  transition:transform var(--t) var(--ease-out),box-shadow var(--t) var(--ease-out),border-color var(--t);
  overflow:hidden;
}
.cap::before{
  content:"";position:absolute;inset:0;opacity:0;transition:opacity var(--t);
  background:radial-gradient(120% 90% at 100% 0%,rgba(99,91,255,.10),transparent 60%),
             radial-gradient(90% 80% at 0% 100%,rgba(0,212,255,.10),transparent 60%);
}
.cap:hover{transform:translateY(-4px);border-color:rgba(99,91,255,.28);box-shadow:0 20px 40px -22px rgba(50,50,93,.45),0 6px 14px -10px rgba(99,91,255,.35);}
.cap:hover::before{opacity:1;}
.cap:active{transform:translateY(-1px) scale(.994);}
.cap-ico{
  position:relative;width:36px;height:36px;border-radius:11px;display:grid;place-items:center;flex:none;
  background:linear-gradient(150deg,rgba(99,91,255,.14),rgba(0,212,255,.14));
  color:#4b45d6;border:1px solid rgba(99,91,255,.16);
}
.cap-ico svg{width:17px;height:17px;}
.cap:nth-child(2) .cap-ico{color:#0b7dd1;background:linear-gradient(150deg,rgba(0,166,255,.16),rgba(0,212,255,.12));border-color:rgba(0,166,255,.18);}
.cap:nth-child(3) .cap-ico{color:#0b9a7a;background:linear-gradient(150deg,rgba(0,212,160,.16),rgba(0,212,255,.10));border-color:rgba(0,200,160,.20);}
.cap:nth-child(4) .cap-ico{color:#8a4bd6;background:linear-gradient(150deg,rgba(140,90,255,.16),rgba(255,107,107,.10));border-color:rgba(140,90,255,.18);}
.cap-t{position:relative;font-size:14px;font-weight:650;color:var(--ink);letter-spacing:-.01em;}
.cap-d{position:relative;font-size:12.5px;line-height:1.55;color:var(--ink-3);font-weight:450;}
.cap-go{
  position:relative;margin-top:auto;display:inline-flex;align-items:center;gap:5px;
  font-size:12px;font-weight:600;color:var(--brand);opacity:0;transform:translateX(-4px);
  transition:opacity var(--t),transform var(--t) var(--ease-out);
}
.cap-go svg{width:12px;height:12px;}
.cap:hover .cap-go{opacity:1;transform:translateX(0);}
.cap.is-picked{animation:capPick 620ms var(--ease-out);}
@keyframes capPick{
  0%{transform:translateY(0) scale(1);box-shadow:0 0 0 0 rgba(99,91,255,.35);}
  35%{transform:translateY(-3px) scale(1.015);box-shadow:0 0 0 4px rgba(99,91,255,.18);}
  100%{transform:translateY(0) scale(1);box-shadow:0 0 0 0 rgba(99,91,255,0);}
}
@media (max-width:1180px){ .caps{grid-template-columns:repeat(2,minmax(0,1fr));} }
@media (max-width:640px){ .caps{grid-template-columns:1fr;} }

/* ============ Messages ============ */
.msg{margin-bottom:32px;animation:riseIn 420ms var(--ease-out) both;}
@keyframes riseIn{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:translateY(0)}}
.msg-user{display:flex;justify-content:flex-end;}
.bubble-user{
  max-width:560px;background:linear-gradient(135deg,#635bff 0%,#5b52f0 60%,#4f46e5 100%);
  color:#fff;padding:14px 18px;border-radius:var(--r-xl) var(--r-xl) 6px var(--r-xl);
  font-size:14.5px;line-height:1.65;font-weight:450;
  box-shadow:0 14px 28px -16px rgba(99,91,255,.9),0 4px 10px -6px rgba(50,50,93,.35);
  position:relative;overflow:hidden;
}
.bubble-user::before{content:"";position:absolute;inset:0;background:radial-gradient(120% 100% at 100% 0%,rgba(0,212,255,.35),transparent 55%);pointer-events:none;}
.msg-user-meta{display:flex;align-items:center;gap:8px;justify-content:flex-end;font-size:11.5px;color:var(--ink-4);margin-top:8px;letter-spacing:.01em;}

.msg-ai{display:flex;gap:14px;align-items:flex-start;}
.ai-avatar{
  width:32px;height:32px;border-radius:10px;flex:none;margin-top:2px;
  background:linear-gradient(145deg,#fff,#f4f3ff);
  border:1px solid var(--line);display:grid;place-items:center;
  box-shadow:var(--sh-sm),inset 0 1px 0 #fff;
}
.ai-avatar svg{width:18px;height:18px;}
.ai-body{flex:1 1 auto;min-width:0;}
.ai-head{display:flex;align-items:center;gap:10px;margin-bottom:12px;min-height:24px;}
.ai-name{font-size:13.5px;font-weight:600;color:var(--ink);letter-spacing:-.01em;}
.ai-tool{
  display:inline-flex;align-items:center;gap:5px;font-size:11px;font-weight:500;color:var(--brand);
  background:var(--brand-soft);border:1px solid #e6e3ff;padding:2px 8px;border-radius:var(--r-pill);
}
.ai-time{font-size:11.5px;color:var(--ink-4);margin-left:auto;font-variant-numeric:tabular-nums;}

/* Thought process */
.process{
  border:1px solid var(--line);border-radius:var(--r-md);background:#fff;overflow:hidden;
  box-shadow:var(--sh-xs);margin-bottom:18px;transition:box-shadow var(--t) var(--ease),border-color var(--t) var(--ease);
}
.process:hover{border-color:#d3d8e3;box-shadow:var(--sh-sm);}
.process[data-open="true"]{box-shadow:var(--sh-md);border-color:#dcdfe8;}
.process-toggle{
  width:100%;display:flex;align-items:center;gap:10px;padding:11px 14px;
  font-size:13px;font-weight:600;color:var(--ink-2);transition:background var(--t-fast) var(--ease);
}
.process-toggle:hover{background:var(--bg-sub);}
.process-toggle .chev{width:15px;height:15px;color:var(--ink-4);transition:transform var(--t-slow) var(--ease);flex:none;}
.process[data-open="true"] .chev{transform:rotate(180deg);}
.process-icon{width:16px;height:16px;color:var(--brand);flex:none;}
.process-meta{margin-left:auto;display:flex;align-items:center;gap:6px;font-size:11.5px;font-weight:500;color:var(--ink-4);font-variant-numeric:tabular-nums;}
.process-meta .tick{width:14px;height:14px;border-radius:50%;background:var(--ok-bg);color:var(--ok);display:grid;place-items:center;}
.process-meta .tick svg{width:9px;height:9px;}
.process-body{
  display:grid;grid-template-rows:1fr;transition:grid-template-rows var(--t-slow) var(--ease);
}
.process[data-open="false"] .process-body{grid-template-rows:0fr;}
.process-body > div{overflow:hidden;}
.process-list{border-top:1px solid var(--line-2);padding:6px 0;margin:0;list-style:none;}
.log{
  display:flex;gap:12px;padding:9px 14px;position:relative;
  opacity:0;transform:translateY(6px);animation:logIn 380ms var(--ease-out) forwards;
}
@keyframes logIn{to{opacity:1;transform:translateY(0)}}
.log:hover{background:linear-gradient(90deg,#fbfaff,rgba(251,250,255,0));}
.log-ico{
  width:24px;height:24px;border-radius:7px;flex:none;display:grid;place-items:center;
  background:var(--bg-sub);border:1px solid var(--line-2);color:var(--ink-3);margin-top:1px;
}
.log-ico svg{width:13px;height:13px;}
.log-main{flex:1 1 auto;min-width:0;}
.log-top{display:flex;align-items:center;gap:8px;flex-wrap:wrap;}
.log-title{font-size:13px;font-weight:500;color:var(--ink-2);}
.log-tool{
  font-family:var(--mono);font-size:11.5px;font-weight:500;color:var(--brand);
  background:#f5f4ff;border:1px solid #e9e6ff;border-radius:5px;padding:1px 6px;
}
.log-time{font-family:var(--mono);font-size:11px;color:var(--ink-4);margin-left:auto;font-variant-numeric:tabular-nums;flex:none;}
.log-sub{font-size:12.5px;color:var(--ink-3);margin-top:3px;line-height:1.5;}
.log-code{
  margin-top:7px;background:#0f1b2d;border:1px solid #1d2c44;border-radius:var(--r-sm);
  padding:9px 12px;font-family:var(--mono);font-size:11.8px;line-height:1.6;color:#b9c6dc;
  overflow-x:auto;white-space:pre;scrollbar-width:thin;
}
.log-code .k{color:#8b9dff;}
.log-code .s{color:#7fe0c3;}
.log-code .n{color:#ffd166;}
.log-code .p{color:#9aa8bf;}

/* Markdown */
.md{font-size:14.5px;line-height:1.78;color:var(--ink-2);}
.md > *:first-child{margin-top:0;}
.md h2{
  font-size:21px;line-height:1.35;font-weight:600;letter-spacing:-.025em;color:var(--ink);
  margin:0 0 14px;padding-bottom:12px;border-bottom:1px solid var(--line-2);position:relative;
}
.md h2::after{content:"";position:absolute;left:0;bottom:-1px;width:52px;height:2px;border-radius:2px;background:linear-gradient(90deg,#635bff,#00d4ff);}
.md h3{
  font-size:15.5px;font-weight:600;color:var(--ink);letter-spacing:-.015em;
  margin:24px 0 10px;display:flex;align-items:center;gap:9px;
}
.md h3::before{
  content:"";width:4px;height:15px;border-radius:2px;flex:none;
  background:linear-gradient(180deg,#635bff,#00c3ff);
}
.md p{margin:0 0 12px;}
.md strong{color:var(--ink);font-weight:600;}
.md blockquote{
  margin:16px 0;padding:13px 16px 13px 15px;border-left:3px solid var(--brand);
  background:linear-gradient(90deg,#f7f6ff,#fdfdff);border-radius:0 var(--r-sm) var(--r-sm) 0;
  color:var(--ink-2);font-size:13.8px;
}
.md blockquote p{margin:0;}
.md ul,.md ol{margin:0 0 12px;padding-left:0;list-style:none;}
.md li{position:relative;padding-left:22px;margin-bottom:8px;}
.md ul > li::before{
  content:"";position:absolute;left:4px;top:.72em;width:5px;height:5px;border-radius:50%;
  background:var(--brand);box-shadow:0 0 0 3px rgba(99,91,255,.12);
}
.md ol{counter-reset:oli;}
.md ol > li{counter-increment:oli;padding-left:28px;}
.md ol > li::before{
  content:counter(oli);position:absolute;left:0;top:.18em;width:19px;height:19px;border-radius:6px;
  background:var(--brand-soft);color:var(--brand);font-size:11.5px;font-weight:600;
  display:grid;place-items:center;line-height:1;border:1px solid #e6e3ff;
}
.md table{width:100%;border-collapse:separate;border-spacing:0;margin:14px 0 16px;font-size:13.5px;
  border:1px solid var(--line);border-radius:var(--r-md);overflow:hidden;box-shadow:var(--sh-xs);}
.md thead th{
  background:linear-gradient(180deg,#fafbff,#f4f6fb);font-weight:600;color:var(--ink-2);font-size:12.5px;
  text-align:left;padding:11px 14px;border-bottom:1px solid var(--line);letter-spacing:.01em;
}
.md tbody td{padding:11px 14px;border-bottom:1px solid var(--line-2);color:var(--ink-2);font-variant-numeric:tabular-nums;}
.md tbody tr:last-child td{border-bottom:none;}
.md tbody tr{transition:background var(--t-fast) var(--ease);}
.md tbody tr:hover{background:#fafbff;}
.md td.num{font-weight:500;color:var(--ink);}
.up{color:var(--ok);font-weight:600;}
.down{color:var(--danger);font-weight:600;}
.md .src{font-size:12.5px;color:var(--ink-4);font-style:normal;}

/* File cards */
.files{margin-top:22px;padding-top:18px;border-top:1px dashed var(--line);}
.files-head{display:flex;align-items:center;gap:8px;font-size:12px;font-weight:600;color:var(--ink-3);letter-spacing:.04em;text-transform:uppercase;margin-bottom:12px;}
.files-head svg{width:14px;height:14px;color:var(--ink-4);}
.file-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(232px,1fr));gap:12px;}
.file-card{
  display:flex;align-items:center;gap:12px;padding:12px 14px;background:#fff;
  border:1px solid var(--line);border-radius:var(--r-md);text-align:left;width:100%;
  box-shadow:var(--sh-xs);transition:all var(--t) var(--ease);position:relative;overflow:hidden;
}
.file-card::after{
  content:"";position:absolute;inset:0;background:linear-gradient(110deg,transparent 25%,rgba(99,91,255,.09) 50%,transparent 75%);
  transform:translateX(-130%);transition:transform 680ms var(--ease-out);pointer-events:none;
}
.file-card:hover{border-color:#c9c2ff;box-shadow:var(--sh-pop);transform:translateY(-2px);}
.file-card:hover::after{transform:translateX(130%);}
.file-card:active{transform:translateY(-1px) scale(.995);}
.file-ico{width:34px;height:34px;border-radius:9px;flex:none;display:grid;place-items:center;background:var(--bg-sub);border:1px solid var(--line-2);}
.file-ico svg{width:16px;height:16px;}
.file-meta{flex:1 1 auto;min-width:0;}
.file-name{font-size:13px;font-weight:500;color:var(--ink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;letter-spacing:-.01em;}
.file-sub{font-size:11.5px;color:var(--ink-4);margin-top:2px;display:flex;gap:6px;align-items:center;}
.file-sub .sep{width:3px;height:3px;border-radius:50%;background:#cbd2dd;}
.file-go{width:22px;height:22px;border-radius:6px;display:grid;place-items:center;color:var(--ink-4);flex:none;transition:all var(--t-fast) var(--ease);}
.file-card:hover .file-go{background:var(--brand-soft);color:var(--brand);}
.file-go svg{width:13px;height:13px;}
.file-ico.md{background:#f4f3ff;border-color:#e6e3ff;color:#635bff;}
.file-ico.pdf{background:#fdecec;border-color:#fbdcdc;color:#e02f2f;}
.file-ico.xlsx{background:#e7f7ef;border-color:#d3f0e1;color:#0f9d58;}
.file-ico.png{background:#eaf6ff;border-color:#d8ecff;color:#0b8fd6;}
.file-ico.csv{background:#fff6e6;border-color:#fdeac7;color:#b98200;}

/* streaming caret */
.caret{display:inline-block;width:2px;height:1em;background:var(--brand);margin-left:2px;vertical-align:-2px;animation:blink 1s steps(1) infinite;border-radius:1px;}
@keyframes blink{50%{opacity:0}}

/* thinking state */
.thinking{display:flex;align-items:center;gap:10px;padding:12px 14px;border:1px solid var(--line);border-radius:var(--r-md);background:#fff;box-shadow:var(--sh-xs);margin-bottom:14px;}
.spinner{width:16px;height:16px;border-radius:50%;border:2px solid #e6e3ff;border-top-color:var(--brand);animation:spin .8s linear infinite;flex:none;}
@keyframes spin{to{transform:rotate(360deg)}}
.thinking span{font-size:13px;color:var(--ink-3);font-weight:500;}
.thinking .dots {display:inline-flex;gap:3px;margin-left:2px;}
.thinking .dots i{width:4px;height:4px;border-radius:50%;background:#b9b2ff;animation:bounce 1.2s infinite;}
.thinking .dots i:nth-child(2){animation-delay:.15s}
.thinking .dots i:nth-child(3){animation-delay:.3s}
@keyframes bounce{0%,80%,100%{transform:translateY(0);opacity:.5}40%{transform:translateY(-4px);opacity:1}}

/* ============ Composer ============ */
.composer-wrap{flex:none;position:relative;z-index:5;padding:0 32px 14px;background:linear-gradient(180deg,rgba(250,251,255,0),#fafbff 32%);}
.composer{max-width:880px;margin:0 auto;width:100%;}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px;}
.chips:empty{display:none;}
.chip{
  display:inline-flex;align-items:center;gap:8px;padding:6px 8px 6px 10px;background:#fff;
  border:1px solid var(--line);border-radius:var(--r-pill);font-size:12.5px;color:var(--ink-2);
  box-shadow:var(--sh-xs);animation:riseIn 260ms var(--ease-out) both;max-width:240px;
}
.chip svg{width:13px;height:13px;color:var(--brand);flex:none;}
.chip span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.chip button{width:17px;height:17px;border-radius:50%;display:grid;place-items:center;color:var(--ink-4);flex:none;transition:all var(--t-fast);}
.chip button:hover{background:var(--danger-bg);color:var(--danger);}
.chip button svg{width:9px;height:9px;}
.input-shell{
  background:#fff;border:1px solid var(--line);border-radius:var(--r-lg);
  box-shadow:var(--sh-sm);transition:border-color var(--t) var(--ease),box-shadow var(--t) var(--ease),transform var(--t) var(--ease);
  position:relative;overflow:hidden;
}
.input-shell::before{
  content:"";position:absolute;top:0;left:0;right:0;height:2px;opacity:0;
  background:linear-gradient(90deg,#635bff,#00d4ff,#ff6b6b,#ffd166,#635bff);
  background-size:300% 100%;transition:opacity var(--t) var(--ease);animation:sheen 8s linear infinite;
}
@keyframes sheen{to{background-position:300% 0}}
.input-shell:focus-within{border-color:rgba(99,91,255,.55);box-shadow:0 0 0 4px rgba(99,91,255,.13),var(--sh-md);}
.input-shell:focus-within::before{opacity:1;}
.input-shell.is-busy{opacity:.72;pointer-events:none;}
#input{
  width:100%;border:none;outline:none;resize:none;background:transparent;
  padding:16px 18px 2px;font-size:14.5px;line-height:1.6;color:var(--ink);
  min-height:52px;max-height:180px;scrollbar-width:thin;
}
#input::placeholder{color:var(--ink-4);}
.input-actions{display:flex;align-items:center;gap:8px;padding:8px 10px 10px 10px;}
.icon-btn{
  width:34px;height:34px;border-radius:9px;display:grid;place-items:center;color:var(--ink-3);
  transition:all var(--t-fast) var(--ease);
}
.icon-btn:hover{background:var(--bg-sub);color:var(--brand);}
.icon-btn:active{transform:scale(.94);}
.icon-btn svg{width:17px;height:17px;}
.composer-hint{margin-left:auto;font-size:11.5px;color:var(--ink-4);display:flex;align-items:center;gap:6px;}
kbd{
  font-family:var(--font);font-size:10.5px;font-weight:600;color:var(--ink-3);background:var(--bg-sub);
  border:1px solid var(--line);border-bottom-width:2px;border-radius:5px;padding:1px 5px;line-height:1.5;
}
.send-btn{
  height:34px;min-width:37px;padding:0 17px 0 12px;border-radius:9px;display:inline-flex;align-items:center;gap:6px;
  font-size:13px;font-weight:600;color:#fff;position:relative;overflow:hidden;
  background:linear-gradient(135deg,#635bff,#4f46e5);box-shadow:0 6px 14px -6px rgba(99,91,255,.9);
  transition:all var(--t) var(--ease);
}
.send-btn svg{width:15px;height:15px;}
.send-btn::after{content:"";position:absolute;inset:0;background:linear-gradient(110deg,transparent 25%,rgba(255,255,255,.38) 50%,transparent 75%);transform:translateX(-130%);transition:transform 620ms var(--ease-out);}
.send-btn:hover:not(:disabled){transform:translateY(-1px);box-shadow:0 12px 22px -10px rgba(99,91,255,1);background:linear-gradient(135deg,#7069ff,#5b52f0);}
.send-btn:hover:not(:disabled)::after{transform:translateX(130%);}
.send-btn:active:not(:disabled){transform:translateY(0) scale(.97);}
.send-btn:disabled{background:#e9ecf2;color:#a9b2c0;box-shadow:none;cursor:not-allowed;}
.disclaimer{
  text-align:center;font-size:11.5px;color:var(--ink-4);margin-top:10px;line-height:1.5;
  padding:0 8px;
}

/* ============ Sidebar ============ */
.sidebar{
  width:320px;flex:none;background:rgba(255,255,255,.9);
  backdrop-filter:saturate(160%) blur(12px);
  border-left:1px solid var(--line-2);display:flex;flex-direction:column;
  position:relative;z-index:10;min-height:0;
}
.sidebar.is-collapsed{display:none;}
.sb-head{display:flex;align-items:center;gap:8px;padding:16px 16px 12px;}
.sb-title{font-size:13px;font-weight:600;color:var(--ink);letter-spacing:-.01em;display:flex;align-items:center;gap:8px;}
.sb-title .count{
  font-size:11px;font-weight:600;color:var(--brand);background:var(--brand-soft);
  border:1px solid #e6e3ff;border-radius:var(--r-pill);padding:1px 7px;font-variant-numeric:tabular-nums;
}
.sb-actions{margin-left:auto;display:flex;gap:2px;}
.sb-btn{width:28px;height:28px;border-radius:7px;display:grid;place-items:center;color:var(--ink-4);transition:all var(--t-fast) var(--ease);}
.sb-btn:hover{background:var(--bg-sub);color:var(--ink);}
.sb-btn svg{width:15px;height:15px;}
.sb-btn.spinning svg{animation:spin .6s var(--ease);}
.sb-body{flex:1 1 auto;overflow-y:auto;padding:0 12px 16px;scrollbar-width:thin;scrollbar-color:#dfe3ea transparent;}
.sb-body::-webkit-scrollbar{width:8px;}
.sb-body::-webkit-scrollbar-thumb{background:#dfe3ea;border-radius:99px;border:2px solid transparent;background-clip:padding-box;}
.sb-item{
  width:100%;display:flex;align-items:center;gap:10px;padding:9px 10px;border-radius:var(--r-sm);
  text-align:left;transition:all var(--t-fast) var(--ease);border:1px solid transparent;margin-bottom:2px;
  animation:riseIn 380ms var(--ease-out) both;
}
.sb-item:hover{background:#fff;border-color:var(--line);box-shadow:var(--sh-xs);transform:translateX(-1px);}
.sb-item:active{transform:translateX(0) scale(.995);}
.sb-ico{width:28px;height:28px;border-radius:7px;flex:none;display:grid;place-items:center;background:var(--bg-sub);border:1px solid var(--line-2);}
.sb-ico svg{width:14px;height:14px;}
.sb-meta{flex:1 1 auto;min-width:0;}
.sb-name{font-size:12.5px;font-weight:500;color:var(--ink-2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.sb-sub{font-size:11px;color:var(--ink-4);margin-top:1px;font-variant-numeric:tabular-nums;}
.sb-foot{padding:10px 16px 14px;border-top:1px solid var(--line-2);font-size:11px;color:var(--ink-4);display:flex;align-items:center;gap:6px;}
.sb-empty{
  display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;
  padding:56px 24px;color:var(--ink-4);
}
.sb-empty .ico{
  width:52px;height:52px;border-radius:16px;display:grid;place-items:center;margin-bottom:14px;
  background:linear-gradient(150deg,#f7f6ff,#eef0ff);border:1px solid #e6e3ff;color:#a9a2ff;
  box-shadow:var(--sh-xs);
}
.sb-empty .ico svg{width:24px;height:24px;}
.sb-empty p{margin:0;font-size:13px;font-weight:500;color:var(--ink-3);}
.sb-empty small{font-size:11.5px;margin-top:6px;color:var(--ink-4);line-height:1.5;}

/* ============ Toast ============ */
.toasts{position:fixed;right:22px;bottom:22px;z-index:200;display:flex;flex-direction:column;gap:10px;align-items:flex-end;pointer-events:none;}
.toast{
  display:flex;align-items:center;gap:10px;padding:11px 16px 11px 13px;border-radius:var(--r-md);
  background:rgba(255,255,255,.96);border:1px solid var(--line);
  box-shadow:var(--sh-pop);font-size:13px;color:var(--ink-2);font-weight:500;
  animation:toastIn 320ms var(--ease-out) both;max-width:340px;backdrop-filter:blur(10px);
}
.toast.out{animation:toastOut 260ms var(--ease) forwards;}
@keyframes toastIn{from{opacity:0;transform:translateY(10px) scale(.97)}to{opacity:1;transform:translateY(0) scale(1)}}
@keyframes toastOut{to{opacity:0;transform:translateY(6px) scale(.98)}}
.toast .t-ico{width:22px;height:22px;border-radius:7px;display:grid;place-items:center;flex:none;background:var(--ok-bg);color:var(--ok);}
.toast .t-ico svg{width:12px;height:12px;}
.toast.info .t-ico{background:var(--brand-soft);color:var(--brand);}

/* ============ Badge ============ */
.newchat-fab{
  position:fixed;left:22px;bottom:22px;z-index:60;display:inline-flex;align-items:center;gap:7px;
  height:36px;padding:0 15px 0 12px;border-radius:var(--r-pill);font-size:12.5px;font-weight:600;
  color:var(--ink-2);background:rgba(255,255,255,.92);border:1px solid var(--line);
  box-shadow:var(--sh-pop);backdrop-filter:blur(12px);transition:all var(--t) var(--ease);
}
.newchat-fab svg{width:14px;height:14px;color:var(--brand);transition:transform var(--t) var(--ease);}
.newchat-fab:hover{color:var(--brand);border-color:#c9c2ff;transform:translateY(-2px);box-shadow:0 14px 26px -12px rgba(50,50,93,.5);}
.newchat-fab:hover svg{transform:rotate(90deg);}
.newchat-fab:active{transform:translateY(0) scale(.97);}
@media (max-width:940px){ .newchat-fab{bottom:auto;top:70px;left:16px;height:32px;padding:0 12px 0 10px;} }

.demo-badge{
  position:fixed;right:16px;bottom:16px;z-index:300;pointer-events:none;
  font-size:11px;font-weight:500;letter-spacing:.01em;color:rgba(10,37,64,.62);
  background:rgba(255,255,255,.72);border:1px solid rgba(10,37,64,.10);
  padding:5px 11px;border-radius:var(--r-pill);backdrop-filter:blur(8px);
  box-shadow:0 2px 8px -3px rgba(50,50,93,.24);white-space:nowrap;
}

/* ============ LOGIN ============ */
.login-shell{display:grid;grid-template-columns:1.08fr .92fr;min-height:var(--shell-h);position:relative;z-index:1;}
.login-art{
  position:relative;overflow:hidden;background:#0a0f2c;color:#fff;
  display:flex;flex-direction:column;justify-content:space-between;padding:44px 52px;
  clip-path:polygon(0 0,100% 0,92% 100%,0 100%);
}
.login-art .mesh{opacity:.72;filter:blur(75px);inset:-25%;}
.login-art::after{
  content:"";position:absolute;inset:0;z-index:1;pointer-events:none;
  background:
    linear-gradient(180deg,rgba(10,15,44,.15),rgba(10,15,44,.72)),
    radial-gradient(70% 60% at 20% 20%,rgba(99,91,255,.32),transparent 70%);
}
.art-grid{
  position:absolute;inset:0;z-index:1;opacity:.28;pointer-events:none;
  background-image:linear-gradient(rgba(255,255,255,.14) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.14) 1px,transparent 1px);
  background-size:56px 56px;
  -webkit-mask-image:radial-gradient(80% 70% at 30% 30%,#000,transparent 78%);
  mask-image:radial-gradient(80% 70% at 30% 30%,#000,transparent 78%);
}
.art-top{position:relative;z-index:2;display:flex;align-items:center;gap:11px;}
.art-top .brand-name{color:#fff;}
.art-mid{position:relative;z-index:2;max-width:520px;padding:20px 0;}
.art-mid h1{
  margin:0 0 20px;font-size:clamp(34px,3.6vw,48px);line-height:1.1;font-weight:600;letter-spacing:-.03em;
}
.art-mid h1 em{font-style:normal;background:linear-gradient(96deg,#8f88ff,#00d4ff 60%,#ffd166);-webkit-background-clip:text;background-clip:text;color:transparent;}
.art-mid p{margin:0;font-size:16px;line-height:1.65;color:rgba(255,255,255,.74);max-width:440px;}
.art-stats{display:flex;gap:14px;margin-top:34px;}
.stat{
  padding:14px 18px;border-radius:var(--r-md);min-width:130px;
  background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.16);backdrop-filter:blur(12px);
  transition:all var(--t) var(--ease);
}
.stat:hover{background:rgba(255,255,255,.14);transform:translateY(-2px);}
.stat b{display:block;font-size:22px;font-weight:600;letter-spacing:-.02em;font-variant-numeric:tabular-nums;}
.stat span{font-size:11.5px;color:rgba(255,255,255,.62);letter-spacing:.02em;}
.art-bottom{position:relative;z-index:2;display:flex;align-items:center;gap:14px;font-size:12px;color:rgba(255,255,255,.5);}
.art-bottom .stack{display:flex;}
.art-bottom .stack i{
  width:24px;height:24px;border-radius:50%;border:2px solid #0a0f2c;margin-left:-7px;
  background:linear-gradient(140deg,#635bff,#00d4ff);
}
.art-bottom .stack i:first-child{margin-left:0;background:linear-gradient(140deg,#ff6b6b,#ffd166);}
.art-bottom .stack i:nth-child(3){background:linear-gradient(140deg,#00d4ff,#635bff);}
.login-form-side{
  background:#fff;display:flex;align-items:center;justify-content:center;padding:48px 40px;position:relative;
}
.login-form-side::before{
  content:"";position:absolute;left:-60px;top:-60px;width:320px;height:320px;pointer-events:none;
  background:radial-gradient(circle,rgba(99,91,255,.10),transparent 68%);filter:blur(28px);
}
.login-card{width:100%;max-width:404px;position:relative;z-index:2;}
.login-card h2{margin:0 0 8px;font-size:27px;font-weight:600;letter-spacing:-.03em;color:var(--ink);}
.login-sub{margin:0 0 28px;font-size:14px;color:var(--ink-3);}
.login-sub a:hover{text-decoration:underline;}
.field{position:relative;margin-bottom:18px;}
.field label{
  position:absolute;left:13px;top:14px;font-size:14px;color:var(--ink-4);pointer-events:none;
  transition:all var(--t) var(--ease-out);transform-origin:left top;background:#fff;padding:0 4px;line-height:1;
}
.field input{
  width:100%;height:50px;padding:14px 44px 0 14px;border-radius:10px;border:1px solid var(--line);
  background:#fff;font-size:14px;outline:none;color:var(--ink);
  transition:border-color var(--t) var(--ease),box-shadow var(--t) var(--ease),background var(--t);
}
.field input::placeholder{color:transparent;}
.field input:hover{border-color:#cfd5e1;}
.field input:focus{border-color:var(--brand);box-shadow:0 0 0 4px rgba(99,91,255,.15);}
.field input:focus + label,
.field input:not(:placeholder-shown) + label{
  top:-7px;left:10px;font-size:11.5px;color:var(--brand);font-weight:600;letter-spacing:.01em;
}
.field input:not(:focus):not(:placeholder-shown) + label{color:var(--ink-3);}
.field .toggle-pw{
  position:absolute;right:8px;top:50%;transform:translateY(-50%);width:34px;height:34px;border-radius:8px;
  display:grid;place-items:center;color:var(--ink-4);transition:all var(--t-fast) var(--ease);
}
.field .toggle-pw:hover{background:var(--bg-sub);color:var(--ink-2);}
.field .toggle-pw svg{width:16px;height:16px;}
.field .f-ico{position:absolute;right:14px;top:17px;color:var(--ink-4);pointer-events:none;}
.field .f-ico svg{width:16px;height:16px;}
.field.has-pw input{padding-right:48px;}
.field.has-pw .f-ico{display:none;}
.field.error input{border-color:var(--danger);box-shadow:0 0 0 4px rgba(197,48,48,.10);}
.field.error label{color:var(--danger);}
.field-err{
  display:none;align-items:center;gap:5px;font-size:12px;color:var(--danger);margin-top:6px;
}
.field-err svg{width:12px;height:12px;flex:none;}
.field.error .field-err{display:flex;animation:riseIn 220ms var(--ease-out) both;}
.form-row{display:flex;align-items:center;justify-content:space-between;margin:2px 0 22px;font-size:13px;}
.check{display:inline-flex;align-items:center;gap:8px;color:var(--ink-2);cursor:pointer;user-select:none;}
.check input{
  appearance:none;-webkit-appearance:none;width:17px;height:17px;border-radius:5px;border:1.5px solid #cfd5e1;
  background:#fff;margin:0;cursor:pointer;transition:all var(--t-fast) var(--ease);position:relative;flex:none;
}
.check input:checked{background:var(--brand);border-color:var(--brand);}
.check input:checked::after{
  content:"";position:absolute;left:5px;top:1.5px;width:4px;height:8px;
  border:solid #fff;border-width:0 2px 2px 0;transform:rotate(45deg);
}
.check input:focus-visible{outline:none;box-shadow:0 0 0 4px rgba(99,91,255,.15);}
.link-btn{font-size:13px;font-weight:500;color:var(--brand);transition:color var(--t-fast);}
.link-btn:hover{color:var(--brand-700);text-decoration:underline;}
.submit-btn{
  width:100%;height:48px;border-radius:10px;font-size:14.5px;font-weight:600;color:#fff;position:relative;
  display:inline-flex;align-items:center;justify-content:center;gap:9px;overflow:hidden;
  background:linear-gradient(135deg,#635bff,#4f46e5 70%,#4338ca);
  box-shadow:0 10px 22px -12px rgba(99,91,255,.95),0 2px 6px -2px rgba(50,50,93,.28);
  transition:all var(--t) var(--ease);
}
.submit-btn::after{content:"";position:absolute;inset:0;background:linear-gradient(110deg,transparent 25%,rgba(255,255,255,.35) 50%,transparent 75%);transform:translateX(-130%);transition:transform 700ms var(--ease-out);}
.submit-btn:hover:not(:disabled){transform:translateY(-1px);box-shadow:0 18px 32px -16px rgba(99,91,255,1),0 3px 8px -3px rgba(50,50,93,.3);}
.submit-btn:hover:not(:disabled)::after{transform:translateX(130%);}
.submit-btn:active:not(:disabled){transform:translateY(0) scale(.99);}
.submit-btn:disabled{cursor:default;}
.submit-btn .btn-spin{width:15px;height:15px;border-radius:50%;border:2px solid rgba(255,255,255,.35);border-top-color:#fff;animation:spin .7s linear infinite;}
.submit-btn.is-loading{background:linear-gradient(135deg,#6f68ff,#5b52f0);}
.submit-btn.is-success{background:linear-gradient(135deg,#0fae7d,#04724d);box-shadow:0 10px 22px -12px rgba(4,114,77,.9);}
.divider{display:flex;align-items:center;gap:14px;margin:24px 0;color:var(--ink-4);font-size:11.5px;letter-spacing:.04em;}
.divider::before,.divider::after{content:"";flex:1;height:1px;background:var(--line);}
.third-party{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;}
.tp-btn{
  height:46px;border-radius:10px;border:1px solid var(--line);background:#fff;display:grid;place-items:center;
  transition:all var(--t) var(--ease);box-shadow:var(--sh-xs);
}
.tp-btn:hover{border-color:#c9c2ff;background:#fcfbff;transform:translateY(-2px);box-shadow:var(--sh-pop);}
.tp-btn:active{transform:translateY(0) scale(.98);}
.tp-btn svg{width:19px;height:19px;}
.login-foot{margin-top:26px;text-align:center;font-size:13px;color:var(--ink-3);}
.login-foot a{margin-left:4px;font-weight:600;color:var(--brand);}
.login-foot a:hover{text-decoration:underline;}
.back-link{
  position:absolute;right:28px;top:24px;z-index:5;display:inline-flex;align-items:center;gap:7px;
  font-size:13px;font-weight:500;color:rgba(255,255,255,.80);padding:7px 13px 7px 10px;border-radius:var(--r-pill);
  background:rgba(255,255,255,.10);border:1px solid rgba(255,255,255,.20);backdrop-filter:blur(10px);
  transition:all var(--t) var(--ease);
}
.back-link:hover{background:rgba(255,255,255,.2);color:#fff;transform:translateX(-2px);}
.back-link svg{width:14px;height:14px;}
.enter-link{
  display:none;margin-top:14px;text-align:center;font-size:13px;font-weight:600;color:var(--ok);
  animation:riseIn 320ms var(--ease-out) both;
}
.enter-link.show{display:block;}

/* ============ v2 polish additions ============ */
.field .f-ok{position:absolute;right:36px;top:19px;width:14px;height:14px;color:var(--ok);opacity:0;transform:scale(.6);transition:all var(--t) var(--ease-out);pointer-events:none;}
.field.valid .f-ok{opacity:1;transform:scale(1);}
.field.error .f-ico{color:var(--danger);}
.field.has-pw.valid .toggle-pw{color:var(--ok);}
.field input:-webkit-autofill{-webkit-box-shadow:0 0 0 40px #fff inset;}
.shake{animation:shake 420ms var(--ease) both;}
@keyframes shake{0%,100%{transform:translateX(0)}20%{transform:translateX(-6px)}40%{transform:translateX(5px)}60%{transform:translateX(-3px)}80%{transform:translateX(2px)}}
.submit-btn.is-success .btn-arrow{transition:transform var(--t) var(--ease-out);}
.submit-btn.is-success:hover .btn-arrow{transform:translateX(3px);}
.enter-link{display:block;max-height:0;overflow:hidden;opacity:0;margin-top:0;transition:max-height var(--t-slow) var(--ease),opacity var(--t) var(--ease),margin var(--t) var(--ease);}
.enter-link.show{max-height:44px;opacity:1;margin-top:14px;animation:none;}
.enter-link:hover{text-decoration:underline;color:var(--brand-700);}
.form-note{display:none;align-items:center;gap:6px;font-size:11.5px;color:var(--ink-4);margin-top:9px;}
.form-note.show{display:flex;animation:riseIn 260ms var(--ease-out) both;}
.form-note svg{width:12px;height:12px;flex:none;}

/* server switch (reopen sidebar) */
.sidebar-again{
  position:absolute;right:14px;top:12px;z-index:30;display:none;align-items:center;gap:6px;height:30px;
  padding:0 12px 0 9px;border-radius:var(--r-pill);font-size:12px;font-weight:600;color:var(--ink-2);
  background:rgba(255,255,255,.94);border:1px solid var(--line);box-shadow:var(--sh-sm);
  backdrop-filter:blur(10px);transition:all var(--t) var(--ease);
}
.sidebar-again.show{display:inline-flex;animation:riseIn 280ms var(--ease-out) both;}
.sidebar-again:hover{color:var(--brand);border-color:#c9c2ff;transform:translateY(-1px);}
.sidebar-again svg{width:13px;height:13px;}

/* typing blocks */
.typing > *{animation:riseIn 340ms var(--ease-out) both;}

/* ============ stagger entrance ============ */
.stagger > *{animation:riseIn 460ms var(--ease-out) both;}
.stagger > *:nth-child(1){animation-delay:0ms}
.stagger > *:nth-child(2){animation-delay:120ms}
.stagger > *:nth-child(3){animation-delay:240ms}
.stagger > *:nth-child(4){animation-delay:360ms}
.stagger > *:nth-child(5){animation-delay:480ms}

@media (max-width:1180px){
  .sidebar{width:288px;}
  .session-chip{max-width:220px;}
}
@media (max-width:980px){
  .login-shell{grid-template-columns:1fr;}
  .login-art{display:none;}
  .modeswitch{display:none;}
}
@media (max-width:940px){
  .sidebar{position:absolute;right:0;top:0;bottom:0;width:300px;box-shadow:var(--sh-lg);background:#fff;}
  .inner{padding:24px 20px 8px;}
  .composer-wrap{padding:0 16px 10px;}
  .welcome{padding:6vh 16px 20px;}
  .session-chip{display:none;}
  .login-form-side{padding:32px 20px;}
  .composer-hint{display:none;}
  .send-btn{margin-left:auto;}
}
/* ================================================================
   MOTION LAYER · 动效增强（仅 transform / opacity / filter，无布局抖动）
   ================================================================ */

/* 1) 页面进入编排：欢迎区标题 → 副标题 → 建议标签 → 4 张能力卡 → 输入框，依次 stagger */
@keyframes enterUp{from{opacity:0;transform:translate3d(0,16px,0)}to{opacity:1;transform:translate3d(0,0,0)}}
@keyframes enterUpSm{from{opacity:0;transform:translate3d(0,10px,0)}to{opacity:1;transform:translate3d(0,0,0)}}
@keyframes softIn{from{opacity:0}to{opacity:1}}
.enter{animation:enterUp 620ms var(--ease-out) both;}
.enter-sm{animation:enterUpSm 520ms var(--ease-out) both;}
.d1{animation-delay:60ms}.d2{animation-delay:150ms}.d3{animation-delay:230ms}.d4{animation-delay:310ms}
.d5{animation-delay:390ms}.d6{animation-delay:470ms}.d7{animation-delay:550ms}.d8{animation-delay:630ms}
/* 欢迎区内部编排（覆盖既有 .stagger 的同名延迟，改为可感知的级联） */
.welcome-suggests.stagger > *:nth-child(1){animation-delay:300ms}
.welcome-suggests.stagger > *:nth-child(2){animation-delay:360ms}
.welcome-suggests.stagger > *:nth-child(3){animation-delay:420ms}
.caps.stagger > *:nth-child(1){animation-delay:440ms}
.caps.stagger > *:nth-child(2){animation-delay:520ms}
.caps.stagger > *:nth-child(3){animation-delay:600ms}
.caps.stagger > *:nth-child(4){animation-delay:680ms}
.composer-wrap.enter{animation-delay:760ms;}

/* 2) 能力卡 hover：抬升 + 跟随指针的光斑（--mx/--my 由 JS 写入） */
.cap{will-change:transform;}
.cap::after{
  content:"";position:absolute;inset:0;pointer-events:none;opacity:0;transition:opacity 320ms var(--ease);
  background:radial-gradient(220px circle at var(--mx,50%) var(--my,0%),rgba(99,91,255,.16),transparent 62%);
}
.cap:hover::after{opacity:1;}
.cap:hover .cap-ico{transform:translateY(-2px) scale(1.06);}
.cap-ico{transition:transform 320ms var(--ease-out),box-shadow 320ms var(--ease);}
.cap:hover .cap-ico{box-shadow:0 10px 20px -12px rgba(99,91,255,.85);}

/* 3) 按钮按压回弹：按下 3% 缩放，松开由 spring 曲线回弹 */
.send-btn:active:not(:disabled),.login-entry:active,.submit-btn:active:not(:disabled),
.tp-btn:active,.sug:active,.newchat-fab:active{transition:transform 90ms var(--ease);}
.release-bounce{animation:releaseBounce 460ms cubic-bezier(.22,1.4,.36,1) both;}
@keyframes releaseBounce{0%{transform:scale(.955)}55%{transform:scale(1.028)}100%{transform:scale(1)}}

/* 4) 发送按钮 loading 水波 + 顶部进度扫光 */
.send-btn.is-loading{pointer-events:none;background:linear-gradient(135deg,#7069ff,#5b52f0);}
.send-btn.is-loading::after{
  background:conic-gradient(from 0turn,rgba(255,255,255,.05),rgba(255,255,255,.42),rgba(255,255,255,.05));
  transform:none !important;animation:btnSweep 1.1s linear infinite;
}
@keyframes btnSweep{to{transform:rotate(1turn)}}
.send-btn .ripple{
  position:absolute;border-radius:50%;pointer-events:none;background:rgba(255,255,255,.55);
  width:10px;height:10px;margin:-5px 0 0 -5px;transform:scale(0);opacity:.85;
  animation:rippleOut 620ms var(--ease-out) forwards;
}
@keyframes rippleOut{to{transform:scale(14);opacity:0}}
.input-shell .run-progress{
  position:absolute;left:0;right:0;bottom:0;height:2px;overflow:hidden;opacity:0;transition:opacity 240ms;
}
.input-shell.is-busy .run-progress{opacity:1;}
.run-progress i{
  display:block;height:100%;width:36%;border-radius:2px;
  background:linear-gradient(90deg,transparent,#635bff,#00d4ff,transparent);
  animation:runBar 1.25s var(--ease) infinite;
}
@keyframes runBar{0%{transform:translateX(-110%)}100%{transform:translateX(310%)}}

/* 5) 用户气泡弹入 / AI 回复整体浮现 */
.msg-user.enter-bubble{animation:bubblePop 520ms cubic-bezier(.2,1.28,.4,1) both;}
.msg-user.bubble-pop{animation:bubblePop 520ms cubic-bezier(.2,1.28,.4,1) both;}
.msg-ai.reply-in{animation:replyIn 480ms var(--ease-out) both;}
@keyframes replyIn{
  from{opacity:0;transform:translate3d(0,12px,0);} 
  to{opacity:1;transform:translate3d(0,0,0);}
}
.replay-group{animation:softIn 220ms var(--ease-out) both;}
@keyframes bubblePop{
  from{opacity:0;transform:translate3d(0,14px,0) scale(.94);}
  60%{opacity:1;}
  to{opacity:1;transform:translate3d(0,0,0) scale(1);}
}

/* 静态工具条（reduced-motion 降级用） */
.tool-strip.is-static li{opacity:1;transform:none;}

/* 文件面板：新增文件时顶栏一次性水波（只动 transform/opacity） */
.sb-head{position:relative;overflow:hidden;}
.panel-wave{
  position:absolute;left:0;top:0;bottom:0;width:46%;
  pointer-events:none;opacity:0;
  background:linear-gradient(100deg,transparent,rgba(99,91,255,.16),transparent);
  transform:translate3d(-120%,0,0);
}
.panel-wave.run{animation:panelWave 900ms var(--ease-out) both;}
@keyframes panelWave{
  0%{opacity:0;transform:translate3d(-120%,0,0);}
  18%{opacity:1;}
  100%{opacity:0;transform:translate3d(260%,0,0);}
}

/* 6) AI 思考：骨架 shimmer + 脉冲光标 */
.skeleton{padding:2px 0 4px;margin-bottom:14px;}
.sk-line{
  height:12px;border-radius:6px;margin-bottom:10px;
  background:linear-gradient(90deg,#eef1f7 25%,#f7f9fd 42%,#eef1f7 60%);
  background-size:280% 100%;animation:shimmer 1.5s var(--ease) infinite;
}
.sk-line:nth-child(1){width:62%;}
.sk-line:nth-child(2){width:100%;}
.sk-line:nth-child(3){width:84%;}
@keyframes shimmer{0%{background-position:120% 0}100%{background-position:-120% 0}}
.thinking-bar{
  display:flex;align-items:center;gap:9px;padding:11px 14px;margin-bottom:14px;
  border:1px dashed #dfdaff;border-radius:var(--r-md);background:linear-gradient(90deg,#fbfaff,#fff);
  animation:softIn 320ms var(--ease-out) both;
}
.thinking-bar span.t-txt-b{font-size:12.8px;color:var(--ink-3);font-weight:500;}
.thinking-bar .spinner{width:14px;height:14px;}
.think-caret{display:inline-block;width:7px;height:13px;border-radius:2px;background:var(--brand);vertical-align:-2px;margin-left:4px;animation:caretPulse 1s steps(1) infinite;}
@keyframes caretPulse{50%{opacity:0}}
/* 骨架内的「思考中」抬头行 + 脉冲光标 */
.think-skeleton{opacity:1;transition:opacity 180ms var(--ease);}
.thinking-reflect{display:flex;align-items:center;gap:6px;margin:0 0 10px;font-size:12.6px;font-weight:600;color:var(--brand);}
.thinking-reflect .reflect-txt{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%;}
.reflect-caret{display:inline-block;width:2px;height:1em;border-radius:1px;background:var(--brand);animation:blink 1s steps(1) infinite;flex:none;}
.btn-spin{width:15px;height:15px;border-radius:50%;border:2px solid rgba(255,255,255,.35);border-top-color:#fff;animation:spin 720ms linear infinite;flex:none;}

/* 7) 工具调用状态条：依次点亮、脉冲、勾选 */
.tool-strip{
  display:flex;flex-wrap:wrap;gap:8px;margin:0 0 14px;padding:0;list-style:none;
}
.tool-strip li{
  display:inline-flex;align-items:center;gap:7px;padding:6px 11px 6px 9px;border-radius:var(--r-pill);
  border:1px solid var(--line);background:#fff;font-size:12.5px;font-weight:500;color:var(--ink-4);
  box-shadow:var(--sh-xs);opacity:.5;transition:opacity 260ms var(--ease),transform 320ms var(--ease-out),
  border-color 260ms,color 260ms,background 260ms,box-shadow 320ms;
  transform:translate3d(0,3px,0);
}
.tool-strip li .ts-dot{
  width:8px;height:8px;border-radius:50%;background:#cfd6e2;flex:none;position:relative;transition:background 260ms;
}
.tool-strip li.is-run{
  opacity:1;transform:translate3d(0,0,0);color:var(--brand);border-color:#d8d4ff;background:var(--brand-soft);
  box-shadow:0 0 0 4px rgba(99,91,255,.07);
}
.tool-strip li.is-run .ts-dot{background:var(--brand);}
.tool-strip li.is-run .ts-dot::after{
  content:"";position:absolute;inset:-4px;border-radius:50%;border:2px solid rgba(99,91,255,.5);
  animation:ringPulse 1.1s var(--ease) infinite;
}
@keyframes ringPulse{0%{transform:scale(.55);opacity:.9}100%{transform:scale(1.7);opacity:0}}
.tool-strip li.is-done{opacity:1;transform:translate3d(0,0,0);color:var(--ink-2);border-color:#cfeee0;background:var(--ok-bg);}
.tool-strip li.is-done .ts-dot{background:var(--ok);}
.tool-strip li.is-done .ts-dot::before{
  content:"";position:absolute;left:2px;top:1px;width:2px;height:4px;border:solid #fff;border-width:0 1.4px 1.4px 0;
  transform:rotate(45deg);
}
.tool-strip li .ts-count{font-size:11px;color:var(--ink-4);font-variant-numeric:tabular-nums;}
.tool-strip li.is-done .ts-count{color:var(--ok);}

/* 8) 打字机光标 & 逐段浮现 */
.type-caret{display:inline-block;width:2px;height:1.05em;background:var(--brand);border-radius:1px;margin-left:2px;vertical-align:-3px;animation:blink 1s steps(1) infinite;}
.md .type-fade{animation:typeFade 420ms var(--ease-out) both;}
@keyframes typeFade{from{opacity:0;transform:translate3d(0,6px,0)}to{opacity:1;transform:translate3d(0,0,0)}}

/* 9) 文件面板滑入滑出 + 新增文件高亮 */
.sidebar{transition:transform 340ms var(--ease-out),opacity 260ms var(--ease);}
.sidebar.slide-out{transform:translate3d(24px,0,0);opacity:0;pointer-events:none;}
.sb-item.flash{animation:fileFlash 1.4s var(--ease-out);}
@keyframes fileFlash{
  0%{opacity:0;transform:translate3d(-14px,0,0);background:#f2f0ff;}
  40%{background:#efeDFF;}
  100%{opacity:1;transform:translate3d(0,0,0);background:transparent;}
}
.sb-item .new-tag{
  margin-left:auto;flex:none;font-size:10px;font-weight:700;letter-spacing:.04em;color:#fff;
  background:linear-gradient(135deg,#635bff,#00a6ff);border-radius:var(--r-pill);padding:1px 7px;
  animation:newTag 520ms cubic-bezier(.2,1.4,.4,1) both;
}
@keyframes newTag{from{opacity:0;transform:scale(.6)}to{opacity:1;transform:scale(1)}}
.count.bump{animation:countBump 520ms cubic-bezier(.2,1.4,.4,1) both;}
@keyframes countBump{0%{transform:scale(1)}45%{transform:scale(1.22)}100%{transform:scale(1)}}
.file-card.is-new{border-color:#cfc9ff;box-shadow:0 0 0 4px rgba(99,91,255,.10),var(--sh-pop);}
.file-card.is-new .file-ico{animation:icoPop 620ms cubic-bezier(.2,1.4,.4,1) both;}
@keyframes icoPop{0%{transform:scale(.7)}60%{transform:scale(1.12)}100%{transform:scale(1)}}

/* 10) 登录页编排：左栏艺术区 + 右栏表单逐项浮现 */
.login-art .art-top{animation:enterUpSm 560ms var(--ease-out) 80ms both;}
.login-art .art-mid{animation:enterUp 700ms var(--ease-out) 160ms both;}
.login-art .art-bottom{animation:enterUpSm 560ms var(--ease-out) 380ms both;}
.login-art .stat{animation:enterUpSm 520ms var(--ease-out) both;}
.art-stats .stat:nth-child(1){animation-delay:300ms}
.art-stats .stat:nth-child(2){animation-delay:380ms}
.art-stats .stat:nth-child(3){animation-delay:460ms}
.login-card.stagger > *:nth-child(1){animation-delay:120ms}
.login-card.stagger > *:nth-child(2){animation-delay:180ms}
.login-card.stagger > *:nth-child(3){animation-delay:250ms}
.login-card.stagger > *:nth-child(4){animation-delay:320ms}
.login-card.stagger > *:nth-child(5){animation-delay:390ms}
.login-card.stagger > *:nth-child(6){animation-delay:460ms}
.login-card.stagger > *:nth-child(7){animation-delay:520ms}

/* 登录提交按钮内的进度水波 */
.submit-btn .btn-fill{
  position:absolute;inset:0;transform-origin:left center;transform:scaleX(0);
  background:linear-gradient(90deg,rgba(255,255,255,.22),rgba(255,255,255,.06));pointer-events:none;
}
.submit-btn.is-loading .btn-fill{animation:fillBar 1.2s var(--ease-out) forwards;}
@keyframes fillBar{to{transform:scaleX(1)}}

/* 11) 重放按钮（追加在对话副标题旁，absolute 定位，不参与竖向布局） */
.replay-wrap{display:flex;justify-content:center;margin-top:9px;}
.replay-btn{
  display:inline-flex;align-items:center;gap:6px;height:26px;padding:0 11px 0 9px;border-radius:var(--r-pill);
  font-size:11.5px;font-weight:600;color:var(--brand);background:#fff;border:1px solid #dfdbff;
  box-shadow:var(--sh-xs);transition:transform 200ms var(--ease-out),box-shadow 200ms var(--ease),background 200ms;
  animation:softIn 360ms var(--ease-out) both;
}
.replay-btn svg{width:12px;height:12px;}
.replay-btn:hover{transform:translateY(-1px);background:var(--brand-soft);box-shadow:0 10px 20px -14px rgba(99,91,255,.9);}
.replay-btn:active{transform:scale(.96);}
.replay-btn:disabled{opacity:.5;pointer-events:none;}

@media (prefers-reduced-motion:no-preference){
  .scroll{scroll-behavior:smooth;}
}
/* 12) 降级：关闭位移动画，只保留淡入 */
@media (prefers-reduced-motion:reduce){
  *,*::before,*::after{
    animation-duration:.001ms !important;animation-iteration-count:1 !important;
    transition-duration:.001ms !important;scroll-behavior:auto !important;
  }
  .enter,.enter-sm,.msg-user.enter-bubble,.msg-user.bubble-pop,.msg-ai.reply-in,.md .type-fade,.tool-strip li,.sb-item.flash,.file-card.is-new .file-ico{
    animation:softIn 1ms linear both !important;transform:none !important;opacity:1 !important;
  }
  .thinking-reflect,.reflect-caret{animation:none !important;}
  .panel-wave{animation:none !important;opacity:0 !important;}
  .mesh i,.mesh .m1,.mesh .m2,.mesh .m3,.mesh .m4{animation:none !important;}
  .sk-line{animation:none !important;background:#eef1f7 !important;}
  .run-progress i,.send-btn.is-loading::after,.tool-strip li.is-run .ts-dot::after{animation:none !important;}
}


/* ============================================================
   真实应用适配（在 05-stripe Demo 样式之后追加）
   —— 只做必需的小补充，不覆盖 Demo 的任何视觉规则
   ============================================================ */

/* 用 <a> 承载文件卡片 / 侧栏文件项时去掉默认链接样式 */
.file-card{text-decoration:none;color:inherit;}
.sb-item{text-decoration:none;color:inherit;}

/* 登录页底部的演示说明 */
.login-demo-note{
  margin:16px 0 0;padding:9px 12px;border-radius:10px;
  background:var(--bg-sub);border:1px dashed var(--line);
  font-size:11.5px;line-height:1.6;color:var(--ink-4);text-align:center;
}

/* 工具调用参数（JSON）在日志里的排版 */
.log-code pre{
  margin:0;font-family:var(--mono);font-size:11.5px;line-height:1.55;color:var(--ink-2);
  white-space:pre-wrap;word-break:break-word;max-height:190px;overflow:auto;
}

/* 系统 / 错误消息 */
.msg-system{display:flex;justify-content:center;margin:8px 0 18px;}
.bubble-system{
  max-width:86%;padding:10px 14px;border-radius:12px;
  background:var(--danger-bg);border:1px solid #f3d1d1;color:var(--danger);
  font-size:13px;line-height:1.65;word-break:break-word;
}

/* 右侧「新会话」悬浮按钮在窄屏别挡住输入区 */
@media (max-width:940px){
  .newchat-fab{z-index:40;}
}

/* 登录视图未激活时不占位、不拦截点击 */
#view-login:not(.is-active){display:none;}
#view-main:not(.is-active){display:none;}

</style>
