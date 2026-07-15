<template>
  <!-- 历史记录面板：展示最近 5 条成功生成的行程 -->
  <el-card v-if="history.length > 0" shadow="hover" class="history-panel card-spacing">
    <template #header>
      <div class="history-header">
        <div class="history-header-left">
          <el-icon size="18" color="#409eff"><Clock /></el-icon>
          <span class="history-title">历史行程</span>
          <el-tag size="small" type="info" round>{{ history.length }} 条</el-tag>
        </div>
        <el-button
          type="danger"
          size="small"
          text
          @click="handleClear"
          :disabled="clearing"
        >
          {{ clearing ? '清空中...' : '清空记录' }}
        </el-button>
      </div>
    </template>

    <div class="history-list">
      <div
        v-for="item in history"
        :key="item.taskId"
        class="history-item"
      >
        <div class="history-item-main">
          <!-- 目的地 & 天数 -->
          <div class="history-item-title">
            <span class="history-destination">{{ item.destination }}</span>
            <el-tag size="small" type="primary" effect="plain">
              {{ item.days }} 天
            </el-tag>
            <el-tag
              v-if="item.people"
              size="small"
              type="info"
              effect="plain"
            >
              {{ item.people }}
            </el-tag>
          </div>
          <!-- 偏好 & 预算 -->
          <div class="history-item-meta">
            <span class="history-budget">💰 {{ item.totalBudget }} 元</span>
            <el-tag
              v-for="tag in (item.preferences || []).slice(0, 3)"
              :key="tag"
              size="small"
              class="history-pref-tag"
            >
              {{ tag }}
            </el-tag>
            <span class="history-time">{{ formatTimeAgo(item.createdAt) }}</span>
          </div>
        </div>
        <!-- 操作按钮 -->
        <div class="history-item-actions">
          <el-button
            size="small"
            type="primary"
            text
            @click="$emit('view', item.taskId)"
          >
            查看详情
          </el-button>
          <el-dropdown @command="(fmt) => $emit('export', item.taskId, fmt)" trigger="click">
            <el-button size="small" text>
              导出 <el-icon class="el-icon--right"><ArrowDown /></el-icon>
            </el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="md">
                  <el-icon><Document /></el-icon> Markdown
                </el-dropdown-item>
                <el-dropdown-item command="pdf">
                  <el-icon><Printer /></el-icon> PDF
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </div>
    </div>
  </el-card>

  <!-- 空历史占位 -->
  <el-card v-else shadow="hover" class="card-spacing">
    <el-empty
      description="暂无历史行程记录"
      :image-size="80"
    >
      <template #description>
        <span style="color:#909399;font-size:14px;">
          提交并成功生成行程后，历史记录将显示在这里
        </span>
      </template>
    </el-empty>
  </el-card>
</template>

<script setup>
import { computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getHistory, clearHistory, formatTimeAgo } from '../composables/useHistory.js'

// ---- Props ----
const props = defineProps({
  /** 刷新触发器：外部传入，值变化时重新读取 localStorage */
  refreshKey: { type: Number, default: 0 },
})

// ---- Emits ----
const emit = defineEmits([
  'view',    // 查看详情: (taskId)
  'export',  // 导出文件: (taskId, format)
  'cleared', // 历史已清空
])

// ---- 计算属性 ----
/** 每次 refreshKey 变化时重新读取历史 */
const history = computed(() => {
  // 触发 refreshKey 的依赖
  void props.refreshKey
  return getHistory()
})

// ---- 方法 ----
const clearing = computed(() => false)

/** 清空全部历史记录（需二次确认） */
async function handleClear() {
  try {
    await ElMessageBox.confirm(
      '确定要清空全部历史记录吗？此操作不可撤销。',
      '确认清空',
      { confirmButtonText: '确定', cancelButtonText: '取消', type: 'warning' }
    )
    clearHistory()
    ElMessage.success('历史记录已清空')
    emit('cleared')
  } catch {
    // 用户取消
  }
}
</script>

<style scoped>
.history-panel {
  margin-top: 0;
}

.history-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.history-header-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.history-title {
  font-weight: 600;
  font-size: 15px;
}

.history-list {
  display: flex;
  flex-direction: column;
  gap: 0;
}

.history-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 0;
  border-bottom: 1px solid #f0f0f0;
  transition: background 0.2s;
}

.history-item:last-child {
  border-bottom: none;
}

.history-item:hover {
  background: #fafbfc;
  margin: 0 -12px;
  padding-left: 12px;
  padding-right: 12px;
  border-radius: 4px;
}

.history-item-main {
  flex: 1;
  min-width: 0;
}

.history-item-title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.history-destination {
  font-weight: 600;
  font-size: 15px;
  color: #303133;
}

.history-item-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  font-size: 13px;
  color: #909399;
}

.history-budget {
  color: #e6a23c;
  font-weight: 500;
}

.history-pref-tag {
  --el-tag-bg-color: #f0f2f5;
  --el-tag-border-color: #e4e7ed;
  --el-tag-text-color: #909399;
}

.history-time {
  color: #c0c4cc;
  font-size: 12px;
}

.history-item-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
  margin-left: 12px;
}

/* 响应式 */
@media (max-width: 768px) {
  .history-item {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }

  .history-item-actions {
    margin-left: 0;
    align-self: flex-end;
  }
}
</style>
