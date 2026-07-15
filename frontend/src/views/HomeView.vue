<template>
  <div class="home-view">
    <!-- 步骤提示 -->
    <div class="step-hint">
      <el-steps :active="0" align-center finish-status="success">
        <el-step title="填写需求" description="输入出行参数" />
        <el-step title="规划行程" description="AI Agent 生成中" />
        <el-step title="查看结果" description="预览 & 导出" />
      </el-steps>
    </div>

    <!-- 表单卡片 -->
    <TravelForm
      :loading="submitting"
      @submit="handleSubmit"
    />

    <!-- 历史记录面板 -->
    <HistoryPanel
      :refresh-key="historyRefreshKey"
      @view="handleHistoryView"
      @export="handleHistoryExport"
      @cleared="historyRefreshKey++"
    />
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import TravelForm from '../components/TravelForm.vue'
import HistoryPanel from '../components/HistoryPanel.vue'
import { exportByTaskId } from '../api/index.js'

const router = useRouter()
const submitting = ref(false)
const historyRefreshKey = ref(0)

/**
 * 提交行程生成请求，成功后跳转到结果页
 * @param {Object} formData - 与后端 TravelPlanRequest 字段对齐
 */
async function handleSubmit(formData) {
  submitting.value = true
  try {
    ElMessage.success('正在进入行程规划...')
    router.push({
      name: 'TaskResult',
      query: {
        destination: formData.destination,
        days: formData.days,
        total_budget: formData.total_budget,
        people: formData.people,
        preferences: (formData.preferences || []).join(','),
        remark: formData.remark || '',
      },
    })
  } finally {
    submitting.value = false
  }
}

/**
 * 从历史记录查看行程详情
 * @param {string} taskId - 任务 ID
 */
function handleHistoryView(taskId) {
  router.push({ name: 'TaskResult', params: { taskId } })
}

/**
 * 从历史记录直接导出行程文件
 * @param {string} taskId - 任务 ID
 * @param {'md'|'pdf'} format - 导出格式
 */
async function handleHistoryExport(taskId, format) {
  try {
    const res = await exportByTaskId(taskId, format)
    // 从 Content-Disposition 提取文件名
    const disposition = res.headers?.get?.('Content-Disposition') || ''
    const match = disposition.match(/filename\*=(?:UTF-8'')([^;]*)/)
    let filename = ''
    if (match) {
      filename = decodeURIComponent(match[1])
    } else {
      const plain = disposition.match(/filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/)
      filename = plain ? plain[1].replace(/['"]/g, '') : ''
    }
    if (!filename) {
      filename = format === 'md' ? 'travel_plan.md' : 'travel_plan.pdf'
    }

    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)

    ElMessage.success(`${format === 'md' ? 'Markdown' : 'PDF'} 文件下载成功`)
  } catch (err) {
    ElMessage.error(`导出失败: ${err.message}`)
  }
}
</script>

<style scoped>
.home-view {
  /* container */
}
.step-hint {
  margin-bottom: 24px;
}
</style>
