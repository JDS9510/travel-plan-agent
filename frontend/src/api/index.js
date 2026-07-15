/**
 * API 请求封装 —— 统一管理后端接口调用
 *
 * 所有请求字段与后端 Pydantic schemas 严格对齐：
 * - TravelPlanRequest: destination, days, total_budget, people, preferences, remark
 * - AsyncTaskResponse: code, msg, task_id
 * - TaskStatusResponse: code, task_id, status, progress, data, error
 * - ApiResponse: code, msg, data
 */

// 开发环境通过 vite proxy 转发，生产环境按需修改
const BASE_URL = ''

/**
 * 通用 fetch 封装，含超时与错误处理
 */
async function request(url, options = {}) {
  const timeout = options.timeout || 30000
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeout)

  try {
    const res = await fetch(`${BASE_URL}${url}`, {
      ...options,
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
        ...options.headers,
      },
    })

    // 导出接口返回二进制流，直接透传
    if (options.responseType === 'blob') {
      if (!res.ok) {
        const errText = await res.text().catch(() => '')
        throw new Error(errText || `导出失败 (HTTP ${res.status})`)
      }
      return res
    }

    const json = await res.json()
    if (!res.ok) {
      throw new Error(json.detail || json.msg || `请求失败 (HTTP ${res.status})`)
    }
    return json
  } catch (err) {
    if (err.name === 'AbortError') {
      throw new Error('请求超时，请检查后端服务是否正常运行')
    }
    throw err
  } finally {
    clearTimeout(timer)
  }
}

// ============================================================
// 行程生成
// ============================================================

/**
 * 异步提交行程生成任务
 * POST /api/travel/generate-async
 *
 * @param {Object} params - 与后端 TravelPlanRequest 字段对齐
 * @param {string} params.destination - 目的地（必填）
 * @param {number} params.days - 天数（必填，>=1）
 * @param {number} params.total_budget - 总预算（必填，>=0）
 * @param {string} params.people - 出行人群（必填）
 * @param {string[]} params.preferences - 偏好标签（选填，默认 []）
 * @param {string} params.remark - 备注（选填，默认 ""）
 * @returns {Promise<{code: number, msg: string, task_id: string}>}
 */
export function submitTravelTask(params) {
  return request('/api/travel/generate-async', {
    method: 'POST',
    body: JSON.stringify({
      destination: params.destination,
      days: params.days,
      total_budget: params.total_budget,
      people: params.people,
      preferences: params.preferences || [],
      remark: params.remark || '',
    }),
  })
}

/**
 * 查询异步任务状态
 * GET /api/travel/task/{task_id}
 *
 * @param {string} taskId - 任务 ID
 * @returns {Promise<{code: number, task_id: string, status: string, progress: string, data: object|null, error: string|null}>}
 */
export function getTaskStatus(taskId) {
  return request(`/api/travel/task/${taskId}`)
}

// ============================================================
// SSE 流式生成
// ============================================================

/**
 * SSE 流式行程生成 —— 通过 fetch + ReadableStream 解析 SSE 事件
 * POST /api/travel/generate-stream
 *
 * @param {Object} params - 与 submitTravelTask 一致的出行参数
 * @param {Object} callbacks - 事件回调
 * @param {Function} callbacks.onProgress - progress 事件 ({ node, step, percent, description })
 * @param {Function} callbacks.onContent  - content 事件 ({ fragment })
 * @param {Function} callbacks.onDone     - done 事件 ({ task_id })
 * @param {Function} callbacks.onError    - error 事件 ({ code, message, node? })
 * @returns {Function} 取消连接函数（调用后中止 SSE）
 */
export function generateTravelStream(params, callbacks) {
  const { onProgress, onContent, onDone, onError } = callbacks
  const controller = new AbortController()

  fetch(`${BASE_URL}/api/travel/generate-stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      destination: params.destination,
      days: params.days,
      total_budget: params.total_budget,
      people: params.people,
      preferences: params.preferences || [],
      remark: params.remark || '',
    }),
    signal: controller.signal,
  }).then(async (response) => {
    if (!response.ok) {
      const text = await response.text().catch(() => '')
      onError?.({ code: response.status, message: text || `HTTP ${response.status}` })
      return
    }

    const reader = response.body?.getReader()
    if (!reader) {
      onError?.({ code: 0, message: '浏览器不支持流式读取' })
      return
    }

    const decoder = new TextDecoder()
    let buffer = ''

    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        let eventType = ''
        for (const line of lines) {
          if (line.startsWith('event: ')) {
            eventType = line.slice(7).trim()
          } else if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6))
              switch (eventType) {
                case 'progress': onProgress?.(data); break
                case 'content':  onContent?.(data); break
                case 'done':     onDone?.(data); break
                case 'error':    onError?.(data); break
              }
            } catch { /* 跳过解析失败的行 */ }
          }
        }
      }
    } catch (err) {
      if (err.name !== 'AbortError') {
        onError?.({ code: 0, message: err.message })
      }
    }
  }).catch(err => {
    if (err.name !== 'AbortError') {
      onError?.({ code: 0, message: err.message || '网络连接失败' })
    }
  })

  return () => controller.abort()
}

// ============================================================
// 行程导出
// ============================================================

/**
 * 通过任务 ID 导出行程文件
 * GET /api/travel/export/{task_id}?format=md|pdf
 *
 * @param {string} taskId - 任务 ID
 * @param {'md'|'pdf'} format - 导出格式
 * @returns {Promise<Response>} - fetch Response（blob）
 */
export function exportByTaskId(taskId, format = 'md') {
  return request(`/api/travel/export/${taskId}?format=${format}`, {
    method: 'GET',
    responseType: 'blob',
    timeout: 60000,
  })
}

// ============================================================
// 健康检查
// ============================================================

/**
 * 健康检查
 * GET /health
 * @returns {Promise<{status: string, service: string, version: string, cache: object}>}
 */
export function healthCheck() {
  return request('/health', { timeout: 5000 })
}
