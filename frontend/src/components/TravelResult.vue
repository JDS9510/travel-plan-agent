<template>
  <div>
    <!-- 警告提示（兜底模式提醒） -->
    <el-alert
      v-if="displayData.warning_msg"
      :title="displayData.warning_msg"
      type="warning"
      :closable="true"
      show-icon
      class="card-spacing"
    />

    <!-- 行程信息卡片 -->
    <el-card shadow="hover" class="card-spacing">
      <template #header>
        <div class="card-header-row">
          <div class="card-header-left">
            <el-icon size="20" color="#67c23a"><SuccessFilled /></el-icon>
            <span class="card-title">行程规划结果</span>
          </div>
          <el-tag type="success" effect="dark" size="small">已完成</el-tag>
        </div>
      </template>

      <!-- 概览信息 -->
      <el-descriptions :column="2" border size="small">
        <el-descriptions-item label="目的地">
          {{ displayData.destination || '-' }}
        </el-descriptions-item>
        <el-descriptions-item label="出行天数">
          {{ displayData.total_days || 0 }} 天
        </el-descriptions-item>
        <el-descriptions-item label="总预算">
          {{ displayData.total_budget || 0 }} 元
        </el-descriptions-item>
        <el-descriptions-item label="出行人群">
          {{ displayData.people || '-' }}
        </el-descriptions-item>
        <el-descriptions-item label="偏好标签" :span="2">
          <el-tag
            v-for="tag in (displayData.preferences || [])"
            :key="tag"
            size="small"
            type="info"
            style="margin-right:6px;"
          >
            {{ tag }}
          </el-tag>
          <span v-if="!displayData.preferences?.length" class="text-muted">无</span>
        </el-descriptions-item>
        <el-descriptions-item label="迭代轮次">
          {{ displayData.iteration_count || 0 }}
        </el-descriptions-item>
        <el-descriptions-item label="运行模式">
          {{ displayData.run_mode || 'react' }}
        </el-descriptions-item>
      </el-descriptions>
    </el-card>

    <!-- 每日预算分配卡片 -->
    <el-card v-if="dailyBudgets.length > 0" shadow="hover" class="card-spacing">
      <template #header>
        <div class="card-header-row">
          <div class="card-header-left">
            <el-icon size="18" color="#e6a23c"><Money /></el-icon>
            <span class="card-title">💰 每日预算分配</span>
          </div>
          <el-tag
            :type="budgetOverTotal ? 'danger' : 'success'"
            size="small"
          >
            合计 {{ budgetTotal }} / {{ displayData.total_budget || 0 }} 元
            {{ budgetOverTotal ? '（超预算！）' : '' }}
          </el-tag>
        </div>
      </template>
      <div class="budget-bar-container">
        <div class="budget-bar">
          <div
            v-for="(b, idx) in dailyBudgets"
            :key="idx"
            class="budget-bar-segment"
            :style="{
              flex: b,
              backgroundColor: budgetColors[idx % budgetColors.length],
            }"
            :title="`第${idx+1}天: ${b} 元`"
          />
        </div>
        <div class="budget-legend">
          <span
            v-for="(b, idx) in dailyBudgets"
            :key="idx"
            class="budget-legend-item"
          >
            <span
              class="budget-legend-dot"
              :style="{ backgroundColor: budgetColors[idx % budgetColors.length] }"
            />
            第{{ idx + 1 }}天: <strong>{{ b }}</strong> 元
          </span>
        </div>
      </div>
    </el-card>

    <!-- 内容视图切换 -->
    <el-card shadow="hover" class="card-spacing">
      <template #header>
        <div class="card-header-row">
          <span class="card-title">📋 行程详情</span>
          <el-radio-group v-model="viewMode" size="small">
            <el-radio-button value="structured">结构化视图</el-radio-button>
            <el-radio-button value="markdown">Markdown 预览</el-radio-button>
          </el-radio-group>
        </div>
      </template>

      <!-- 结构化视图 -->
      <div v-if="viewMode === 'structured'">
        <el-empty
          v-if="!dailyPlans.length"
          description="暂无行程数据"
        />

        <el-collapse v-else v-model="activeDay" accordion>
          <el-collapse-item
            v-for="(day, idx) in dailyPlans"
            :key="idx"
            :name="idx"
          >
            <template #title>
              <el-tag type="primary" size="small" style="margin-right:8px;">
                第 {{ idx + 1 }} 天
              </el-tag>
              <span class="day-summary">
                {{ day.spots?.length || 0 }} 个景点 ·
                {{ (day.meals || day.food_recommendation)?.length || 0 }} 餐 ·
                {{ day.transportation || day.traffic_note || '-' }}
              </span>
              <el-tag
                v-if="day.daily_budget"
                :type="day.daily_budget > ((displayData.total_budget || 1) / (dailyPlans.length || 1)) * 1.2 ? 'danger' : 'warning'"
                size="small"
                effect="plain"
                style="margin-left:8px;"
              >
                {{ day.daily_budget }} 元
              </el-tag>
            </template>

            <!-- 景点表格 -->
            <el-table
              v-if="day.spots?.length"
              :data="day.spots"
              size="small"
              border
              stripe
              style="margin-bottom:12px;"
            >
              <el-table-column prop="name" label="景点" min-width="100" />
              <el-table-column prop="address" label="地址" min-width="120" />
              <el-table-column prop="duration" label="建议时长" width="90" align="center" />
              <el-table-column label="门票" width="100" align="center">
                <template #default="{ row }">
                  {{ formatTicket(row) }}
                </template>
              </el-table-column>
              <el-table-column label="推荐理由" min-width="140">
                <template #default="{ row }">
                  {{ row.recommendation || row.reason || '-' }}
                </template>
              </el-table-column>
            </el-table>

            <!-- 餐饮（兼容 meals / food_recommendation） -->
            <div v-if="(day.meals || day.food_recommendation)?.length" style="margin-bottom:8px;">
              <span class="label-text">🍜 推荐美食：</span>
              <el-tag
                v-for="(meal, mi) in (day.meals || day.food_recommendation)"
                :key="mi"
                size="small"
                type="warning"
                style="margin:2px 4px;"
              >
                {{ meal }}
              </el-tag>
            </div>

            <!-- 交通（兼容 transportation / traffic_note） -->
            <div v-if="day.transportation || day.traffic_note">
              <span class="label-text">🚌 交通方式：</span>
              <span class="value-text">{{ day.transportation || day.traffic_note }}</span>
            </div>
          </el-collapse-item>
        </el-collapse>
      </div>

      <!-- Markdown 预览视图 -->
      <div v-else class="markdown-preview" v-html="renderedMarkdown" />
    </el-card>

    <!-- 出行贴士 -->
    <el-card v-if="travelTips.length" shadow="hover" class="card-spacing">
      <template #header>
        <span class="card-title">💡 出行贴士</span>
      </template>
      <ul class="tips-list">
        <li v-for="(tip, ti) in travelTips" :key="ti">
          {{ typeof tip === 'string' ? tip : (tip.title || tip.content || '') }}
        </li>
      </ul>
    </el-card>

    <!-- 校验结果（如有） -->
    <el-card v-if="hasCheckIssues" shadow="hover" class="card-spacing">
      <template #header>
        <span class="card-title">
          <el-icon><Warning /></el-icon>
          校验结果
        </span>
      </template>
      <el-table
        v-if="checkResult.issues?.length"
        :data="checkResult.issues"
        size="small"
        border
      >
        <el-table-column prop="type" label="类型" width="90" align="center" />
        <el-table-column prop="severity" label="严重度" width="80" align="center">
          <template #default="{ row }">
            <el-tag
              :type="row.severity === 'error' ? 'danger' : 'warning'"
              size="small"
            >
              {{ row.severity }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="detail" label="详情" min-width="200" />
      </el-table>
      <div v-else class="text-muted" style="text-align:center;padding:12px;">
        校验通过，未发现问题
      </div>
    </el-card>

    <!-- 底部操作栏 -->
    <el-card shadow="hover">
      <div class="action-bar">
        <!-- 导出按钮 -->
        <el-dropdown @command="handleExport" :disabled="exporting">
          <el-button type="primary" :loading="exporting">
            <el-icon><Download /></el-icon>
            {{ exporting ? '导出中...' : '导出行程' }}
            <el-icon class="el-icon--right"><ArrowDown /></el-icon>
          </el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="md">
                <el-icon><Document /></el-icon>
                Markdown (.md)
              </el-dropdown-item>
              <el-dropdown-item command="pdf">
                <el-icon><Printer /></el-icon>
                PDF (.pdf)
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>

        <!-- 重新规划 -->
        <el-button @click="$emit('newPlan')">
          <el-icon><RefreshRight /></el-icon>
          重新规划
        </el-button>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { marked } from 'marked'
import { exportByTaskId } from '../api/index.js'

// ---- Props ----
const props = defineProps({
  taskId: { type: String, required: true },
  travelData: { type: Object, default: null },
})

// ---- Emits ----
const emit = defineEmits(['newPlan'])

// ---- 状态 ----
const activeDay = ref(0)
const exporting = ref(false)
const viewMode = ref('structured') // 'structured' | 'markdown'

// ---- 数据提取（兼容多种后端返回结构） ----
const displayData = computed(() => {
  const d = props.travelData || {}
  // 尝试从 travel_outline.final_result 提取
  const outline = d.travel_outline || {}
  const finalResult = outline.final_result || {}
  // 如果有 final_result 则优先用
  if (finalResult.destination || finalResult.total_days !== undefined) {
    return {
      destination: finalResult.destination || '',
      total_days: finalResult.total_days || 0,
      total_budget: finalResult.total_budget || 0,
      people: finalResult.people || '',
      preferences: finalResult.preferences || [],
      daily_plans: finalResult.daily_plans || [],
      travel_tips: finalResult.travel_tips || [],
      check_result: finalResult.check_result || {},
      iteration_count: finalResult.iteration_count || 0,
      run_mode: finalResult.run_mode || 'react',
      revision_round: finalResult.revision_round || 0,
      warning_msg: finalResult.warning_msg || '',
    }
  }
  // 否则从 travelData 直接取
  return {
    destination: d.destination || '',
    total_days: d.total_days || 0,
    total_budget: d.total_budget || 0,
    people: d.people || '',
    preferences: d.preferences || [],
    daily_plans: d.daily_plans || [],
    travel_tips: d.travel_tips || [],
    check_result: d.check_result || {},
    iteration_count: d.iteration_count || 0,
    run_mode: d.run_mode || 'react',
    revision_round: d.revision_round || 0,
    warning_msg: d.warning_msg || '',
  }
})

const dailyPlans = computed(() => displayData.value.daily_plans || [])
const travelTips = computed(() => displayData.value.travel_tips || [])
const checkResult = computed(() => displayData.value.check_result || {})

// ---- 每日预算计算 ----
/** 各天的预算金额数组 */
const dailyBudgets = computed(() => {
  return dailyPlans.value.map((day) => {
    const b = day.daily_budget
    return (typeof b === 'number' && b > 0) ? b : 0
  })
})

/** 预算总计 */
const budgetTotal = computed(() => {
  return dailyBudgets.value.reduce((sum, b) => sum + b, 0)
})

/** 是否超预算 */
const budgetOverTotal = computed(() => {
  const limit = displayData.value.total_budget || 0
  return limit > 0 && budgetTotal.value > limit
})

/** 预算柱状图颜色 */
const budgetColors = ['#409eff', '#67c23a', '#e6a23c', '#f56c6c', '#909399', '#b37feb', '#36cfc9', '#ff85c0']

/** 格式化门票显示（兼容 ticket / ticket_price，处理 0 和 'Free' 等值） */
function formatTicket(spot) {
  const val = spot.ticket ?? spot.ticket_price
  if (val === undefined || val === null) return '-'
  if (typeof val === 'string' && val.toLowerCase() === 'free') return '免费'
  const num = Number(val)
  if (Number.isNaN(num)) return String(val)
  if (num === 0) return '免费'
  return `${num} 元`
}

const hasCheckIssues = computed(() => {
  const cr = checkResult.value
  return cr && Object.keys(cr).length > 0
})

// ---- Markdown 渲染 ----
/**
 * 将结构化的行程数据转换为 Markdown 字符串，用于预览
 */
function buildMarkdown(data) {
  const lines = []

  lines.push(`# ✈️ ${data.destination || '行程'} · ${data.total_days || 0} 天旅行规划`)
  lines.push('')

  // 概览表格
  lines.push(`| 项目 | 详情 |`)
  lines.push(`|------|------|`)
  lines.push(`| 目的地 | ${data.destination || '-'} |`)
  lines.push(`| 天数 | ${data.total_days || 0} 天 |`)
  lines.push(`| 预算 | ${data.total_budget || 0} 元 |`)
  lines.push(`| 出行人群 | ${data.people || '-'} |`)
  lines.push(`| 偏好标签 | ${(data.preferences || []).join('、') || '-'} |`)
  lines.push(`| 运行模式 | ${data.run_mode || 'react'} |`)
  lines.push('')

  // 每日预算分配
  const plans = data.daily_plans || []
  const totalDailyBudget = plans.reduce((sum, d) => sum + (d.daily_budget || 0), 0)
  if (totalDailyBudget > 0) {
    lines.push('## 💰 每日预算分配')
    lines.push('')
    lines.push('| 天数 | 主题 | 预算 |')
    lines.push('|------|------|------|')
    plans.forEach((day, idx) => {
      const budget = day.daily_budget || 0
      const flag = (data.total_budget > 0 && budget > (data.total_budget / plans.length) * 1.2) ? ' ⚠️超支' : ''
      lines.push(`| 第 ${idx + 1} 天 | ${day.theme || '-'} | ${budget} 元${flag} |`)
    })
    if (data.total_budget > 0) {
      const overClass = totalDailyBudget > data.total_budget ? '（超预算！）' : ''
      lines.push(`| **合计** | | **${totalDailyBudget} 元** ${overClass} |`)
    }
    lines.push('')
  }

  // 每日行程
  plans.forEach((day, idx) => {
    const dayBudget = day.daily_budget ? `（预算: ${day.daily_budget} 元）` : ''
    lines.push(`## 📍 第 ${idx + 1} 天${dayBudget}`)
    if (day.theme) {
      lines.push(`> 主题：${day.theme}`)
    }
    lines.push('')

    const spots = day.spots || []
    if (spots.length > 0) {
      lines.push('| 景点 | 地址 | 时长 | 门票 | 推荐理由 |')
      lines.push('|------|------|------|------|----------|')
      spots.forEach((s) => {
        const ticket = s.ticket_price ?? s.ticket
        const ticketStr = ticket === 0 || ticket === 'Free' || ticket === '免费' ? '免费'
          : ticket ? `${ticket} 元` : '-'
        const reason = s.recommendation || s.reason || '-'
        lines.push(
          `| ${s.name || '-'} | ${s.address || '-'} | ${s.duration || '-'}h | ${ticketStr} | ${reason} |`
        )
      })
      lines.push('')
    }

    const meals = day.meals || day.food_recommendation || []
    if (meals.length > 0) {
      lines.push(`**🍜 推荐美食：** ${meals.join('、')}`)
      lines.push('')
    }

    const traffic = day.transportation || day.traffic_note || ''
    if (traffic) {
      lines.push(`**🚌 交通方式：** ${traffic}`)
      lines.push('')
    }
  })

  // 贴士
  const tips = data.travel_tips || []
  if (tips.length > 0) {
    lines.push('## 💡 出行贴士')
    lines.push('')
    tips.forEach((tip) => {
      const text = typeof tip === 'string' ? tip : (tip.title || tip.content || '')
      lines.push(`- ${text}`)
    })
    lines.push('')
  }

  return lines.join('\n')
}

const renderedMarkdown = computed(() => {
  const md = buildMarkdown(displayData.value)
  try {
    return marked.parse(md)
  } catch {
    return `<pre>${md}</pre>`
  }
})

// ---- 文件导出 ----
async function handleExport(format) {
  exporting.value = true
  try {
    const res = await exportByTaskId(props.taskId, format)

    // 从 Content-Disposition 提取文件名
    const disposition = res.headers?.get?.('Content-Disposition') || ''
    const match = disposition.match(/filename\*=(?:UTF-8'')([^;]*)/)
    let filename = ''
    if (match) {
      // RFC 5987 编码: filename*=UTF-8''url_encoded
      filename = decodeURIComponent(match[1])
    } else {
      // fallback: 普通 filename=
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
  } finally {
    exporting.value = false
  }
}
</script>

<style scoped>
.card-header-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.card-header-left {
  display: flex;
  align-items: center;
  gap: 8px;
}
.card-title {
  font-weight: 600;
}
.day-summary {
  font-weight: 500;
}
.label-text {
  font-weight: 600;
  font-size: 13px;
}
.value-text {
  font-size: 13px;
}
.text-muted {
  color: #909399;
}
.tips-list {
  padding-left: 20px;
  margin: 0;
  line-height: 2;
}
.tips-list li {
  color: #606266;
}
.action-bar {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}

/* ---- 每日预算分配 ---- */
.budget-bar-container {
  padding: 8px 0;
}

.budget-bar {
  display: flex;
  gap: 4px;
  height: 24px;
  border-radius: 4px;
  overflow: hidden;
  margin-bottom: 12px;
}

.budget-bar-segment {
  min-width: 20px;
  border-radius: 2px;
  transition: all 0.3s;
  cursor: pointer;
}

.budget-bar-segment:hover {
  opacity: 0.8;
  transform: scaleY(1.2);
}

.budget-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  font-size: 13px;
}

.budget-legend-item {
  display: flex;
  align-items: center;
  gap: 4px;
  color: #606266;
}

.budget-legend-dot {
  width: 10px;
  height: 10px;
  border-radius: 2px;
  display: inline-block;
}
</style>
