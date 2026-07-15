<template>
  <el-card shadow="hover">
    <template #header>
      <div style="display:flex;align-items:center;justify-content:space-between;">
        <div style="display:flex;align-items:center;gap:8px;">
          <el-icon size="20" color="#e6a23c"><Loading /></el-icon>
          <span style="font-weight:600;font-size:16px;">AI 正在规划行程</span>
        </div>
        <el-tag
          :type="statusTagType"
          size="small"
          effect="dark"
        >
          {{ statusText }}
        </el-tag>
      </div>
    </template>

    <!-- 进度信息 -->
    <div style="text-align:center;padding:16px 0;">
      <!-- 加载动画 -->
      <div v-if="isPending" style="margin-bottom:20px;">
        <div class="loading-dots">
          <span></span><span></span><span></span>
        </div>
        <p style="color:#909399;margin-top:12px;">{{ sseProgress.description || '正在准备任务...' }}</p>
      </div>

      <div v-else-if="isRunning" style="margin-bottom:20px;">
        <el-progress
          :percentage="sseProgress.percent || runningProgress"
          :indeterminate="!sseProgress.percent"
          :duration="2"
          color="#409eff"
          style="max-width:400px;margin:0 auto;"
        />
        <p style="color:#606266;margin-top:16px;">
          <el-icon class="is-loading"><Loading /></el-icon>
          {{ sseProgress.description || 'AI Agent 正在规划中，请耐心等待...' }}
        </p>
        <p style="color:#909399;font-size:13px;margin-top:4px;">
          已耗时 {{ elapsedText }}
        </p>
        <!-- 超时提示 -->
        <p v-if="showTimeoutHint" style="color:#e6a23c;font-size:13px;margin-top:4px;">
          ⚠️ 生成时间较长，建议稍后查看或
          <el-button type="warning" size="small" text @click="$emit('back')">返回首页等待</el-button>
        </p>
        <!-- 网络不稳定 / SSE重连提示 -->
        <p v-if="networkErrors > 2" style="color:#f56c6c;font-size:13px;margin-top:4px;">
          🔌 网络不稳定，正在重试... ({{ networkErrors }} 次)
        </p>
      </div>

      <div v-else-if="isFailed" style="margin-bottom:20px;">
        <el-result
          icon="error"
          title="行程生成失败"
          :sub-title="error || '未知错误，请重试'"
        >
          <template #extra>
            <el-button type="primary" @click="$emit('retry')">
              <el-icon><RefreshRight /></el-icon>
              重新提交
            </el-button>
            <el-button @click="$emit('back')">
              返回修改参数
            </el-button>
          </template>
        </el-result>
      </div>

      <div v-else-if="isSuccess">
        <el-result
          icon="success"
          title="行程生成完成"
          sub-title="点击下方按钮查看详细行程"
        />
      </div>
    </div>

    <!-- 实时 Markdown 预览（SSE 流式生成期间逐段展示） -->
    <div v-if="isSSEMode && isRunning && streamedContent" class="live-preview">
      <el-divider />
      <div class="live-preview-header">
        <el-icon size="16"><View /></el-icon>
        <span style="font-weight:600;">实时预览</span>
        <span style="font-size:12px;color:#909399;">（内容逐段生成中…）</span>
      </div>
      <div class="markdown-preview" v-html="renderedStreamContent" />
    </div>

    <!-- 底部操作 -->
    <div style="display:flex;justify-content:center;gap:12px;">
      <el-button
        v-if="!isSuccess && !isFailed"
        @click="$emit('back')"
        :disabled="isRunning"
      >
        取消并返回
      </el-button>
      <el-button
        v-if="isSuccess"
        type="success"
        @click="handleViewDetail"
      >
        查看行程详情
      </el-button>
    </div>
  </el-card>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { marked } from 'marked'
import { getTaskStatus, generateTravelStream } from '../api/index.js'

// ---- Props ----
const props = defineProps({
  taskId:    { type: String, default: '' },
  status:    { type: String, default: 'pending' },
  progress:  { type: String, default: '' },
  error:     { type: String, default: '' },
  formParams:{ type: Object, default: null },   // 非 null → SSE 模式
})

// ---- Emits ----
const emit = defineEmits(['complete', 'failed', 'back', 'retry'])

// ---- 内部状态 ----
const localStatus = ref(props.status)
const localError = ref(props.error)
const resultData = ref(null)
const sseTaskId = ref(props.taskId || '')
const elapsedSeconds = ref(0)
const networkErrors = ref(0)
const showTimeoutHint = ref(false)
const streamedContent = ref('')
const sseProgress = ref({ percent: 0, description: '正在连接服务…' })

let pollTimer = null
let elapsedTimer = null
let abortSSE = null

// ---- 模式判断 ----
const isSSEMode = computed(() => !!props.formParams)

// ---- 计算属性 ----
const isPending = computed(() => localStatus.value === 'pending')
const isRunning = computed(() => localStatus.value === 'running')
const isSuccess = computed(() => localStatus.value === 'success')
const isFailed = computed(() => localStatus.value === 'failed')

const statusText = computed(() => {
  const map = { pending: '等待中', running: '执行中', success: '已完成', failed: '已失败' }
  return map[localStatus.value] || localStatus.value
})

const statusTagType = computed(() => {
  const map = { pending: 'info', running: 'warning', success: 'success', failed: 'danger' }
  return map[localStatus.value] || 'info'
})

const runningProgress = computed(() => {
  return Math.min(10 + elapsedSeconds.value * 3, 85)
})

const elapsedText = computed(() => {
  const s = elapsedSeconds.value
  if (s < 60) return `${s} 秒`
  const m = Math.floor(s / 60)
  const r = s % 60
  return `${m} 分 ${r} 秒`
})

const renderedStreamContent = computed(() => {
  if (!streamedContent.value) return ''
  try {
    return marked.parse(streamedContent.value)
  } catch {
    return `<pre>${streamedContent.value}</pre>`
  }
})

// ================================================================
// SSE 模式
// ================================================================
function startSSE() {
  if (abortSSE) return

  localStatus.value = 'running'
  streamedContent.value = ''

  abortSSE = generateTravelStream(props.formParams, {
    onProgress(data) {
      networkErrors.value = 0
      sseProgress.value = {
        percent: data.percent || sseProgress.value.percent,
        description: data.description || sseProgress.value.description,
      }
      // 首次 progress 后切到 running
      if (localStatus.value === 'pending') {
        localStatus.value = 'running'
      }
    },

    onContent(data) {
      networkErrors.value = 0
      streamedContent.value += data.fragment || ''
    },

    async onDone(data) {
      stopTimers()
      abortSSE = null
      sseTaskId.value = data.task_id || sseTaskId.value

      // 从任务库拉取结构化数据（兼容 TravelResult 的两种视图 + 导出）
      let structured = null
      for (let retry = 0; retry < 5; retry++) {
        try {
          const res = await getTaskStatus(sseTaskId.value)
          if (res.status === 'success' && res.data) {
            structured = res.data
            break
          }
        } catch { /* 后端可能还在写入，等待重试 */ }
        await new Promise(r => setTimeout(r, 600))
      }

      if (structured) {
        resultData.value = structured
        localStatus.value = 'success'
        emit('complete', { data: structured, taskId: sseTaskId.value })
      } else {
        // 兜底：结构化数据拉取失败，仍然标记完成（TravelResult 可用流式内容降级展示）
        localStatus.value = 'success'
        emit('complete', {
          data: { _streamedContent: streamedContent.value, destination: props.formParams?.destination },
          taskId: sseTaskId.value,
        })
      }
    },

    onError(data) {
      abortSSE = null
      localError.value = data.message || '服务内部错误'
      localStatus.value = 'failed'
      stopTimers()
      emit('failed', localError.value)
    },
  })
}

// ================================================================
// 轮询模式（历史记录 / 直接链接 —— 保留原有逻辑）
// ================================================================
function startPolling() {
  if (pollTimer) return
  pollTimer = setInterval(async () => {
    try {
      const res = await getTaskStatus(props.taskId)
      localStatus.value = res.status

      if (res.status === 'success') {
        resultData.value = res.data
        stopTimers()
        emit('complete', { data: res.data, taskId: props.taskId })
      } else if (res.status === 'failed') {
        localError.value = res.error || '任务执行失败'
        stopTimers()
        emit('failed', localError.value)
      }
    } catch (err) {
      networkErrors.value++
      console.warn(`轮询出错(${networkErrors.value}):`, err.message)
    }
  }, 2000)
}

// ================================================================
// 公共工具
// ================================================================
function stopTimers() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
  if (elapsedTimer) { clearInterval(elapsedTimer); elapsedTimer = null }
}

function handleViewDetail() {
  emit('complete', { data: resultData.value, taskId: sseTaskId.value })
}

// ---- 生命周期 ----
onMounted(() => {
  elapsedTimer = setInterval(() => {
    elapsedSeconds.value++
    if (elapsedSeconds.value >= 300) {
      showTimeoutHint.value = true
    }
  }, 1000)

  if (isSSEMode.value) {
    startSSE()
  } else if (props.taskId && localStatus.value !== 'success' && localStatus.value !== 'failed') {
    startPolling()
  }
})

onUnmounted(() => {
  stopTimers()
  if (abortSSE) { abortSSE(); abortSSE = null }
})

watch(() => props.status, (val) => {
  if (val === 'success' || val === 'failed') {
    localStatus.value = val
  }
})
</script>

<style scoped>
.loading-dots {
  display: flex;
  justify-content: center;
  gap: 8px;
}
.loading-dots span {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #409eff;
  animation: bounce 1.2s infinite ease-in-out;
}
.loading-dots span:nth-child(1) { animation-delay: 0s; }
.loading-dots span:nth-child(2) { animation-delay: 0.2s; }
.loading-dots span:nth-child(3) { animation-delay: 0.4s; }
@keyframes bounce {
  0%, 80%, 100% { transform: translateY(0); opacity: 0.3; }
  40% { transform: translateY(-12px); opacity: 1; }
}

/* 实时预览区域 */
.live-preview {
  text-align: left;
  margin-top: 8px;
}
.live-preview-header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 10px;
  color: #409eff;
}
.live-preview .markdown-preview {
  max-height: 480px;
  overflow-y: auto;
  padding: 12px;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  background: #fafafa;
}
</style>
