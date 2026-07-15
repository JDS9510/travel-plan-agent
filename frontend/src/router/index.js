/**
 * Vue Router 路由配置
 *
 * 路由表：
 *   /              → HomeView    行程生成表单页
 *   /task/:taskId  → ResultView  任务进度 & 结果展示页
 */
import { createRouter, createWebHashHistory } from 'vue-router'
import HomeView from '../views/HomeView.vue'
import ResultView from '../views/ResultView.vue'

const routes = [
  {
    path: '/',
    name: 'Home',
    component: HomeView,
    meta: { title: '旅行行程规划' },
  },
  {
    path: '/task/:taskId?',
    name: 'TaskResult',
    component: ResultView,
    props: true,
    meta: { title: '行程生成结果' },
  },
  {
    // 未匹配路由 → 重定向到首页
    path: '/:pathMatch(.*)*',
    redirect: '/',
  },
]

const router = createRouter({
  // 使用 hash 模式，无后端配置也能正常运行
  history: createWebHashHistory(),
  routes,
})

// 全局路由守卫：更新页面标题
router.afterEach((to) => {
  document.title = to.meta.title || '旅行行程规划'
})

export default router
