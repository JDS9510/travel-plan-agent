/**
 * useHistory — 本地行程历史记录管理 composable
 *
 * 基于 localStorage 实现，保存最近 5 条成功生成的行程记录。
 * 支持查看历史、重新导出文件，无需重复提交表单。
 *
 * 数据结构：
 *   [{
 *     taskId: string,        // 任务 ID（用于重新导出）
 *     destination: string,   // 目的地
 *     days: number,          // 天数
 *     people: string,        // 出行人群
 *     preferences: string[], // 偏好标签
 *     totalBudget: number,   // 总预算
 *     createdAt: string,     // ISO 时间戳
 *   }]
 */

const STORAGE_KEY = 'travel_history'
const MAX_HISTORY = 5

/**
 * 从 localStorage 读取历史记录
 * @returns {Array} 历史记录数组（最新在前）
 */
export function getHistory() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed
  } catch {
    return []
  }
}

/**
 * 保存历史记录到 localStorage
 * @param {Array} list - 历史记录数组
 */
function saveHistory(list) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(list))
  } catch {
    // localStorage 满或不可用，静默失败
    console.warn('无法保存历史记录，localStorage 可能已满')
  }
}

/**
 * 新增一条历史记录（自动去重，超过 MAX_HISTORY 条则移除最旧）
 * @param {string} taskId - 任务 ID
 * @param {Object} travelData - 行程数据（来自后端返回的 final_result）
 */
export function addHistory(taskId, travelData) {
  if (!taskId || !travelData) return

  const list = getHistory()

  // 去重：同 taskId 的旧记录移除
  const filtered = list.filter((item) => item.taskId !== taskId)

  const record = {
    taskId,
    destination: travelData.destination || '',
    days: travelData.total_days || travelData.days || 0,
    people: travelData.people || '',
    preferences: travelData.preferences || [],
    totalBudget: travelData.total_budget || 0,
    createdAt: new Date().toISOString(),
  }

  // 新记录插入最前面，限制最大条数
  filtered.unshift(record)
  saveHistory(filtered.slice(0, MAX_HISTORY))
}

/**
 * 删除单条历史记录
 * @param {string} taskId - 任务 ID
 */
export function removeHistory(taskId) {
  const list = getHistory().filter((item) => item.taskId !== taskId)
  saveHistory(list)
}

/**
 * 清空全部历史记录
 */
export function clearHistory() {
  saveHistory([])
}

/**
 * 格式化时间为友好的相对时间描述
 * @param {string} isoString - ISO 时间字符串
 * @returns {string} 如 "3 分钟前"、"1 小时前"、"3 天前"
 */
export function formatTimeAgo(isoString) {
  const now = Date.now()
  const then = new Date(isoString).getTime()
  const diffMs = now - then

  const seconds = Math.floor(diffMs / 1000)
  if (seconds < 60) return '刚刚'

  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes} 分钟前`

  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时前`

  const days = Math.floor(hours / 24)
  if (days < 30) return `${days} 天前`

  return new Date(isoString).toLocaleDateString('zh-CN')
}
