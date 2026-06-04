# AI 本地路线智能规划

> 美团 Hackathon 第五题「现在就出发：AI本地路线智能规划」
> 提交 DDL：2026年6月7日 24:00

---

## 项目简介

用 LLM × POI 数据 × 用户偏好，自动生成「直接用、不踩雷」的个性化本地路线方案。

用户一句话描述需求（城市、商圈、预算、口味偏好），系统自动规划包含餐饮 + 文化/娱乐的完整路线，标注排队风险、团购优惠、实时趋势，并在地图上打点展示。

---

## 核心能力

- **自然语言理解**：解析"外滩附近、预算300、想吃本帮菜、逛文化景点"等自由格式输入
- **多约束路线规划**：综合考虑预算、排队时长、营业时间、地理相邻性
- **团购信息整合**：自动匹配可用团购套餐，标注折扣
- **排队风险预警**：高峰期等位时间提示，建议最佳到店时间
- **地图可视化**：高德静态地图打点，所有 POI 一图尽览
- **多轮对话**：支持"换一家餐厅"、"去掉景点"等局部调整

---

## 系统架构

```
用户输入
  → IntentAgent     LLM：自然语言 → 结构化意图 JSON
  → POISearchAgent  纯代码：按城市/商圈/类别召回候选 POI
  → RouteAgent      LLM：从候选中选出最优 3-5 站路线
  → EnrichAgent     纯代码：补充团购/排队/趋势字段
  → OutputAgent     纯代码：格式化 JSON + 拼接高德地图 URL
```

LLM 仅调用 **2 次**（IntentAgent + RouteAgent），其余均为纯代码，保证 < 10 秒响应。

---

## 技术栈

| 模块 | 技术 |
|---|---|
| LLM 主力 | DeepSeek（OpenAI 兼容格式） |
| LLM Fallback | Claude Sonnet 4.6 |
| Agent 框架 | LangGraph（StateGraph） |
| 后端 | FastAPI + uvicorn（SSE 流式） |
| 地图 | 高德静态地图 API |
| 前端 | NoCode（nocode.host） |
| 部署 | Railway / Render |

---

## 快速开始

### 1. 环境准备

```bash
# 复用已有 venv（已安装所有依赖）
source ../intelligent-trading-dag/.venv/bin/activate

# 或新建
pip install -e .
```

### 2. 配置 API Key

```bash
cp .env.example .env
# 编辑 .env，填入：
# DEEPSEEK_API_KEY=...
# ANTHROPIC_API_KEY=...
# AMAP_API_KEY=...
```

### 3. 验证流水线

```bash
# 完整流水线（推荐）
python scripts/run_pipeline.py

# 自定义输入
python scripts/run_pipeline.py "帮我找北京三里屯周六晚上，预算500元，想吃火锅"

# 仅测试 IntentAgent
python scripts/run_intent.py
```

### 预期输出

```
用户输入: 帮我规划上海外滩附近的周末下午，预算300元，想吃本帮菜，顺便逛文化景点

=== Agent 日志 ===
  • 已解析需求：上海外滩，预算300元，餐饮、文化
  • 找到候选POI：餐饮10个、文化8个
  • 路线生成完成，共4个地点
  • 已补充团购/排队/趋势信息
  • 路线规划完成，已生成地图链接

=== 路线结果 ===
  1. 沈大成（南京东路店） (餐饮) | 评分:4.5 | 等位:中
  2. 豫园 (文化) | 评分:4.5 | 等位:高 | 团购:35元
  ...

=== 总结 ===
为你安排了4站行程，预计游玩4小时15分钟，2处有团购优惠，餐饮消费约283元。
```

---

## 项目结构

```
ai-route-planner/
├── route_planner/          # 核心业务包
│   ├── state.py            # RouteState TypedDict（全局状态）
│   ├── node.py             # BaseNode 基类
│   ├── graph.py            # LangGraph 完整流水线
│   ├── llm.py              # DeepSeek + Claude fallback，指数退避重试
│   ├── nodes/
│   │   ├── intent.py       # IntentAgent：意图解析
│   │   ├── poi_search.py   # POISearchAgent：候选召回
│   │   ├── route.py        # RouteAgent：路线规划
│   │   ├── enrich.py       # EnrichAgent：数据补充
│   │   └── output.py       # OutputAgent：格式化输出
│   └── data/
│       └── mock_poi.json   # Mock POI 数据库（100条，覆盖上海主要商圈）
├── app/                    # FastAPI 应用
│   ├── main.py             # 路由 + SSE 接口 + 内存缓存
│   └── schemas.py          # Pydantic 请求/响应模型
├── scripts/                # 调试脚本
│   ├── run_pipeline.py     # 完整流水线测试
│   └── run_intent.py       # IntentAgent 单测
├── docs/
│   └── ARCHITECTURE.md     # 系统架构详解
├── README.md
├── pyproject.toml
└── .env.example
```

---

## 启动后端服务

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

---

## API 接口

```
POST /route/generate   # 首次生成路线（SSE 流式）
POST /route/refine     # 局部替换（SSE 流式）
GET  /health           # 健康检查
```

### 请求格式

```json
{
  "user_input": "帮我规划上海外滩附近的周末下午，预算300元，想吃本帮菜，顺便逛文化景点",
  "conversation_history": [],
  "locked_nodes": []
}
```

### SSE 事件流格式

每完成一个 Agent 步骤立即推送进度，用户边等边看；所有步骤完成后推送完整结果。

```
event: step
data: {"message": "已解析需求：上海外滩，预算300元，餐饮、文化"}

event: step
data: {"message": "找到候选POI：餐饮10个、文化8个"}

event: step
data: {"message": "路线生成完成，共3个地点"}

event: result
data: {"route": [...], "map_url": "...", "summary": "...", "agent_steps": [...]}

event: done
data: {}
```

前端使用 `EventSource` 或 `fetch()` + `ReadableStream` 接收，相同请求命中缓存时直接返回结果，响应时间 < 1 秒。

---

## .env 配置说明

```env
DEEPSEEK_API_KEY=sk-...        # 主力 LLM（必填）
ANTHROPIC_API_KEY=sk-ant-...   # Fallback LLM（可选，建议填）
AMAP_API_KEY=...               # 高德地图静态图 API（由成员B填入）
```

---

## 开发进度

- [x] 项目骨架 + IntentAgent + DeepSeek API 调通
- [x] 完整 LangGraph 流水线，5个 Agent 全部接通，Mock POI 数据库（100条）
- [x] FastAPI + SSE 流式输出（/route/generate, /route/refine, /health，内存缓存）
- [ ] RefineAgent 局部替换 + 多轮对话
- [ ] 前后端联调
- [ ] 优化（缓存、小红书风格输出）+ 录制 Demo
- [ ] 文档整理 + 提交

---

## 团队分工

| 成员 | 负责内容 |
|---|---|
| 成员 A | LangGraph Agent 框架、LLM Prompt、FastAPI 后端 |
| 成员 B | 高德地图 API、Mock POI 数据库、路线评分、部署 |
| 成员 C | NoCode 前端、PPT、Demo 视频 |
