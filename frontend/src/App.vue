<template>
  <div id="app-root">
    <!-- 顶部标题栏 -->
    <header class="app-header">
      <h1 @click="$router.push('/')" class="app-logo">
        🧳 旅行行程规划
      </h1>
      <p class="app-subtitle">基于 AI Agent 的智能多日行程自动规划</p>
    </header>

    <!-- 路由视图 -->
    <main class="page-container">
      <router-view />
    </main>

    <!-- 底部信息 -->
    <footer class="app-footer">
      <span>Powered by LangGraph Agent · FastAPI · Vue3</span>
    </footer>
  </div>
</template>

<script setup>
// App 根组件 —— 提供全局布局骨架，具体页面由 vue-router 渲染
</script>

<style scoped>
.app-logo {
  cursor: pointer;
  user-select: none;
  transition: opacity 0.2s;
}
.app-logo:hover {
  opacity: 0.8;
}
.app-subtitle {
  color: #909399;
  font-size: 14px;
  margin: 6px 0 0;
}
.app-footer {
  text-align: center;
  padding: 16px 0 32px;
  color: #c0c4cc;
  font-size: 12px;
}
</style>
