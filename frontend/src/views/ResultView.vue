<template>
  <div class="result-view">
    <!-- 步骤提示 -->
    <div class="step-hint">
      <el-steps :active="viewStep" align-center finish-status="success">
        <el-step title="填写需求" description="已提交" />
        <el-step title="规划行程" description="AI Agent 生成中" />
        <el-step title="查看结果" description="预览 & 导出" />
      </el-steps>
    </div>

    <!-- Phase 1: 任务进度（SSE 流式 / 轮询） -->
    <div v-if="viewStep < 2">
      <TaskProgress
        :task-id="taskId || ''"
        :status="taskStatus"
        :progress="taskProgress"
        :error="taskError"
        :form-params="formParams"
        @complete="handleComplete"
        @failed="handleFailed"
        @back="handleBack"
        @retry="handleRetry"
      />
    </div>

    <!-- Phase 2: 结果展示 -->
    <div v-if="viewStep === 2">
      <TravelResult
        :task-id="displayTaskId"
        :travel-data="travelData"
        @new-plan="handleBack"
      />
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import TaskProgress from '../components/TaskProgress.vue'
import TravelResult from '../components/TravelResult.vue'
import { addHistory } from '../composables/useHistory.js'

// ---- Props（来自路由参数，taskId 可为空——SSE 新生成场景） ----
const props = defineProps({
  taskId: { type: String, default: '' },
})

const route = useRoute()
const router = useRouter()

// ---- 从 route.query 构建 formParams（非空 → SSE 模式） ----
const formParams = computed(() => {
  const q = route.query
  if (q.destination) {
    return {
      destination: q.destination,
      days: parseInt(q.days) || 1,
      total_budget: parseFloat(q.total_budget) || 0,
      people: q.people || '',
      preferences: q.preferences ? q.preferences.split(',').filter(Boolean) : [],
      remark: q.remark || '',
    }
  }
  return null
})

// ---- 内部状态 ----
const taskStatus = ref('pending')
const taskProgress = ref('')
const taskError = ref('')
const travelData = ref(null)
const displayTaskId = ref(props.taskId || '')

// viewStep: 1 = 生成中, 2 = 结果展示
const viewStep = computed(() => {
  if (travelData.value) return 2
  return 1
})

// ---- 事件处理 ----

/** 任务完成回调（SSE 模式传入 { data, taskId }，轮询模式也适配此签名） */
function handleComplete(payload) {
  const data = payload?.data || payload
  const tid  = payload?.taskId

  travelData.value = data
  if (tid) displayTaskId.value = tid

  // 将最终结果写入历史记录
  const outline = data?.travel_outline || {}
  const finalResult = outline.final_result || {}
  const historyData = finalResult.destination
    ? finalResult
    : (data?.destination ? data : finalResult)
  if (tid || displayTaskId.value) {
    addHistory(tid || displayTaskId.value, historyData)
  }

  ElMessage.success('行程生成完成！')
}

/** 任务失败回调 */
function handleFailed(errMsg) {
  taskError.value = errMsg
}

/** 返回首页 */
function handleBack() {
  router.push({ name: 'Home' })
}

/** 重试：返回首页重新填写 */
function handleRetry() {
  router.push({ name: 'Home' })
}
</script>

<style scoped>
.result-view {
  /* container */
}
.step-hint {
  margin-bottom: 24px;
}
</style>
