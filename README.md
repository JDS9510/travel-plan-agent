# 🗺️ AI 旅行行程规划 Agent

> 基于 **LangGraph** 的智能多日旅行行程规划 AI Agent —— 输入目的地、天数、预算与偏好，自动生成一份真实可用的多日旅行计划。

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688.svg)](https://fastapi.tiangolo.com/)
[![Vue](https://img.shields.io/badge/Vue-3.4+-4FC08D.svg)](https://vuejs.org/)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.2+-orange.svg)](https://langchain-ai.github.io/langgraph/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

---

## 📖 项目简介

**AI 旅行行程规划 Agent** 是一个全栈 AI 应用，用户只需输入目的地、天数、人数、预算和偏好，系统即可自动生成一份包含每日景点安排、费用明细、交通建议的完整旅行计划。

工作流引擎基于 LangGraph 构建，采用 **需求解析 → 景点检索 → 框架生成+预校验 → 每日填充 → 事实校验 → 智能修正** 的多节点有向图架构。校验不通过时自动触发 ReAct 自主修正闭环，确保输出行程真实、可用。

---

## ✨ 核心功能

| 功能 | 说明 |
|------|------|
| 🌍 **全地区城市景点生成** | 支持热门城市、地级市、小众城市，RAG 向量检索 + LLM 知识互补，3 级检索策略兜底 |
| 💰 **预算精细化管控** | 5 类目（住宿/餐饮/交通/门票/其他）预算约束，搭载 `budget_calculator` 实时核算，浮动 ≤10% |
| 🔍 **全链路真实性校验** | 事实校验（景点名称、地址、预算）+ 机械校验（天数、空值、重复），规则优先 + LLM 兜底 |
| 📡 **SSE 流式实时输出** | Server-Sent Events 推送生成进度，前端逐节点渲染，无需等待全链路完成 |
| 📄 **文件导出** | 支持 Markdown / Excel 格式导出行程，适合打印和分享 |
| 🛡️ **异常兜底** | LLM 调用失败自动降级为规则兜底方案，保证任务不中断 |
| 🔄 **多轮修订** | 支持对已生成行程进行增量修改，保持上下文连贯 |
| ⚡ **热门城市景点缓存** | 高频城市景点检索结果缓存，命中时加速 ~99% |

---

## 🧰 技术栈

### 后端
| 技术 | 用途 |
|------|------|
| **Python 3.10+** | 主要开发语言 |
| **LangGraph** | Agent 工作流编排（有向图 + 条件路由） |
| **LangChain** | LLM 调用抽象与工具链 |
| **FastAPI** | REST API 服务 + SSE 流式推送 |
| **Uvicorn** | ASGI 服务器 |
| **ChromaDB** | 向量数据库，景点语义检索 |
| **sentence-transformers** | 文本嵌入模型（bge-small-zh-v1.5） |
| **SQLite** | 任务数据持久化 |
| **Pydantic** | 数据模型校验 |

### 大模型
| 模型 | 用途 |
|------|------|
| **DeepSeek Chat** | 主力 LLM（需求解析、行程生成、事实校验、修正） |

### 前端
| 技术 | 用途 |
|------|------|
| **Vue 3.4+** | 前端框架（Composition API） |
| **Vue Router 4** | 前端路由 |
| **Element Plus** | UI 组件库 |
| **Vite 5** | 构建工具与开发服务器 |
| **marked** | Markdown 渲染 |

---

## 🚀 本地运行指南

### 环境要求

- **Python** ≥ 3.10
- **Node.js** ≥ 18
- **npm** ≥ 9

### 1. 克隆项目

```bash
git clone https://github.com/JDS9510/travel-plan-agent.git
cd travel-plan-agent
```

### 2. 后端启动

```bash
# 创建虚拟环境（推荐）
python -m venv venv
source venv/bin/activate   # Linux / macOS
venv\Scripts\activate      # Windows

# 安装依赖
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env
# 编辑 .env，填入你的 DeepSeek API Key（必填项：LLM_API_KEY）
```

`.env` 关键变量说明：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `LLM_API_KEY` | DeepSeek API 密钥（**必填**） | — |
| `LLM_BASE_URL` | API 接口地址 | `https://api.deepseek.com/v1` |
| `LLM_MODEL` | 模型名称 | `deepseek-chat` |
| `API_SERVER_PORT` | 后端服务端口 | `8000` |
| `TRAVEL_PLANNER_MODE` | Agent 运行模式（`react` / `classic`） | `react` |
| `TRAVEL_CACHE_ENABLED` | 景点缓存开关 | `true` |

```bash
# 启动后端 API 服务
uvicorn src.api.main:app --reload --port 8000
```

启动后访问：
- API 文档：http://localhost:8000/docs
- 健康检查：http://localhost:8000/health

### 3. 前端启动

```bash
cd frontend

# 安装依赖
npm install

# 启动开发服务器
npm run dev
```

前端开发服务器默认运行在 `http://localhost:3000`，已配置代理将 `/api` 请求转发到后端 `8000` 端口。

### 4. 验证运行

```bash
# 测试 LLM 连通性
python test_llm.py

# 运行测试套件
python -m pytest tests/ -v
```

---

## ⚡ 性能优化成果

> 优化前基线：**143.8s** → 优化后：**54.2s** | 降幅：**62.3%**

| 指标 | 优化前 | 优化后 | 降幅 |
|------|--------|--------|------|
| **全场景平均总耗时** | 143.8s | 54.2s | **62.3%** |
| 热门城市（成都） | 165.7s | 48.9s | 70.5% |
| 地级市（郑州） | 141.7s | 55.5s | 60.8% |
| 小众城市（平顶山） | 124.0s | 58.2s | 53.1% |

### 主要优化措施

| 措施 | 影响节点 | 贡献 |
|------|---------|------|
| 🌟 热门城市景点缓存 | 景点检索 | ~99% 加速（缓存命中） |
| 🌟 每日行程并发生成 | 每日填充 | ~60%（N 天并发） |
| 🌟 事实校验规则优先 | 事实校验 | ~56%（跳过 LLM） |
| 🔧 Prompt 精简 | 框架生成 + 每日填充 | ~15% |
| 🔧 Temperature 优化 | 全部 LLM 节点 | ~10% |
| 🔧 景点预校验前置 | 预校验 | 规则毫秒级 |

### SSE 流式首屏优化

前端采用 SSE 流式接收，后端每完成一个图节点即推送进度事件（`demand_analyze → spot_retrieve → ... → result_summary`），用户可在首个节点完成后即看到解析结果，无需等待全链路 54s。结合 Element Plus 骨架屏与 `TaskProgress` 进度组件，感知等待时长大幅缩短。

---

## 📁 项目结构

```
travel-plan-agent/
│
├── src/                            # 后端源码
│   ├── agent/                      # LangGraph Agent 核心
│   │   ├── graph.py                #   有向图编排（主图 + 条件路由）
│   │   ├── nodes.py                #   图节点实现（需求解析、填充、校验等）
│   │   ├── state.py                #   Agent 状态定义（TravelState）
│   │   ├── react_revise.py         #   ReAct 自主修正节点
│   │   └── revise_node.py          #   多轮修订意图解析
│   │
│   ├── api/                        # FastAPI 接口层
│   │   ├── main.py                 #   应用入口 + REST 路由
│   │   ├── schemas.py              #   请求/响应 Pydantic 模型
│   │   └── stream_endpoint.py      #   SSE 流式端点
│   │
│   ├── llm/                        # 大模型客户端
│   │   ├── llm_client.py           #   DeepSeek Chat API 封装
│   │   └── model_router.py         #   主模型/小模型路由分发
│   │
│   ├── rag/                        # RAG 检索增强
│   │   ├── embedding.py            #   文本向量化（sentence-transformers）
│   │   └── vector_store.py         #   ChromaDB 向量存储与检索
│   │
│   ├── tools/                      # Agent 工具集
│   │   ├── budget_calculator.py    #   预算核算（5 类目约束）
│   │   ├── plan_checker.py         #   行程机械校验
│   │   ├── spot_retriever.py       #   景点检索（3 级策略）
│   │   └── weather_query.py        #   天气查询
│   │
│   ├── services/                   # 业务服务
│   │   ├── task_service.py         #   异步任务管理
│   │   ├── cache_service.py        #   缓存服务
│   │   └── export_service.py       #   文件导出（Markdown/Excel）
│   │
│   ├── schemas/                    # 内部数据模型
│   │   └── models.py
│   │
│   ├── cache/                      # 景点缓存
│   │   └── spot_cache.py
│   │
│   └── utils/                      # 工具函数
│       ├── llm_validator.py        #   LLM 输出校验
│       ├── spot_supplement.py      #   景点补充策略（小众城市兜底）
│       └── tracer.py               #   全链路追踪
│
├── frontend/                       # 前端源码（Vue 3）
│   ├── src/
│   │   ├── views/                  # 页面
│   │   │   ├── HomeView.vue        #   首页（表单输入）
│   │   │   └── ResultView.vue      #   结果页（行程展示）
│   │   ├── components/             # 组件
│   │   │   ├── TravelForm.vue      #   旅行需求表单
│   │   │   ├── TravelResult.vue    #   行程结果渲染
│   │   │   ├── TaskProgress.vue    #   任务进度展示
│   │   │   └── HistoryPanel.vue    #   历史记录面板
│   │   ├── composables/            # 组合式函数
│   │   │   └── useHistory.js
│   │   ├── api/                    # 接口请求封装
│   │   │   └── index.js
│   │   ├── router/                 # 路由配置
│   │   │   └── index.js
│   │   ├── styles/                 # 全局样式
│   │   │   └── main.css
│   │   ├── App.vue
│   │   └── main.js
│   ├── index.html
│   ├── vite.config.js              # Vite 配置（含代理）
│   └── package.json
│
├── tests/                          # 测试
│   ├── test_agent.py               #   Agent 单元测试
│   ├── test_tools.py               #   工具集测试
│   ├── test_models.py              #   数据模型测试
│   ├── acceptance_test.py          #   全量验收测试
│   ├── api_verify.py               #   API 兼容性验证
│   ├── perf_baseline.py            #   性能基线测试
│   ├── perf_compare.py             #   性能对比测试
│   ├── quick_verify.py             #   快速回归验证
│   └── output/                     #   测试报告
│
├── data/
│   └── spots.json                  # 热门城市景点缓存数据
│
├── requirements.txt                # Python 依赖清单
├── .env.example                    # 环境变量模板
├── .gitignore
└── README.md
```

---

## 🏗️ LangGraph 工作流

```
demand_analyze（需求解析）
      │
spot_retrieve（景点检索，缓存优先）
      │
 ┌────┴────────────┐  ← 并行
 │                 │
outline_generate   spot_pre_check
（LLM 框架生成）    （规则预校验）
 └────┬────────────┘
      │
daily_fill（每日行程填充，并发 N 天）
      │
fact_check（事实校验，规则优先 + LLM 兜底）
      │
plan_check（机械校验）
   ├── pass | iter≥3 ──→ result_summary（汇总输出）
   │
   └── not pass & iter<3
         │
    react_revise（ReAct 自主修正）
         │
         └──→ daily_fill（循环修正）
```

---

## 📄 License

MIT

---

<p align="center">
  <sub>Built with ❤️ using LangGraph · FastAPI · Vue 3 · DeepSeek</sub>
</p>
