# AI 本地路线智能规划

> 美团 Hackathon 第五题「现在就出发：AI本地路线智能规划」
> 提交 DDL：2026年6月7日 24:00

**线上地址**：`https://ai-route-planner-production.up.railway.app`

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
- **静态地图**：高德静态地图打点 + 真实步行路径蓝线连接，后端生成图片 URL
- **动态地图**：前端嵌入高德 JS SDK，可缩放交互，点击 POI 弹出详情
- **一键导航**：每个 POI 附带高德导航链接，手机点击直接跳转导航 App
- **多轮对话**：支持"换一家餐厅"、"换掉第二个"等局部调整，仅 1 次 LLM 调用

---

## 系统架构

### 首次生成

```
用户输入
  → IntentAgent      LLM①：自然语言 → 结构化意图 JSON（含 duration_hours）
  → POISearchAgent   纯代码：从 SQLite 数据库按城市/商圈/类别召回 Top-10 候选
  → GeoClusterNode   纯代码：地理聚合过滤离群POI + 根据时间算 max_pois
  → RouteAgent       LLM②：从候选中选出最优路线（max_pois 为参考，±1站弹性）
  → EnrichAgent      纯代码：补充团购/排队/趋势字段
  → OutputAgent      纯代码：步行路径、导航链接、地图 URL、摘要
```

### 局部替换（多轮对话）

```
用户："换一家不排队的餐厅"
  → RefineNode       LLM①：解析替换意图，确定节点和新约束
  → POISearchNode    纯代码：只搜被替换类别的候选
  → RefineSelectNode 纯代码：按约束选最优替换 POI，合并回原路线
  → EnrichNode → OutputNode
```

**首次生成 2 次 LLM 调用，局部替换 1 次**，其余全为纯代码，保证 < 10 秒响应。

---

## 技术栈

| 模块 | 技术 |
|---|---|
| LLM 主力 | DeepSeek（OpenAI 兼容格式） |
| LLM Fallback | Claude Sonnet 4.6（DeepSeek 限流时自动切换） |
| Agent 框架 | LangGraph（StateGraph + 条件路由） |
| 后端 | FastAPI + uvicorn（SSE 流式推送） |
| 数据库 | SQLite（由 `poi.csv` 启动时自动生成） |
| 静态地图 | 高德 Web 服务 API（返回图片 URL） |
| 动态地图 | 高德 JS SDK 2.0（前端交互式地图） |
| 前端 | NoCode（nocode.host） |
| 部署 | Railway（自动 HTTPS，push 即部署） |

---

## 快速开始（本地开发）

### 1. 一键建环境

需要 Python 3.11+（推荐 3.12）。

```bash
bash setup.sh
```

脚本自动完成：创建 `.venv`、安装所有依赖、生成 `.env`、从 `poi.csv` 生成 `poi.db`。

### 2. 填入 API Key

编辑 `.env`：

```env
DEEPSEEK_API_KEY=sk-...        # 主力 LLM（必填）
ANTHROPIC_API_KEY=sk-ant-...   # Fallback LLM（建议填）
AMAP_API_KEY=...               # 高德 Web 服务 Key（静态地图 + 步行路径用）
```

> 高德有两种 Key：`.env` 里填的是**Web 服务 Key**（后端 HTTP 接口调用）；
> 前端动态地图需要单独申请**Web 端 JS Key**，详见 [docs/frontend_guide_for_C.md](docs/frontend_guide_for_C.md)。

### 3. 启动后端

```bash
PYTHONPATH=. .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

访问 `http://localhost:8000/health` 返回 `{"status":"ok"}` 即启动成功。

### 4. 验证流水线（可选）

```bash
# 完整流水线测试
PYTHONPATH=. .venv/bin/python3 scripts/run_pipeline.py

# 自定义输入
PYTHONPATH=. .venv/bin/python3 scripts/run_pipeline.py "帮我找上海新天地周六晚上，预算400元，想吃日料"
```

### 预期输出

```
=== Agent 日志 ===
  • 已解析需求：上海外滩，预算300元，餐饮、文化
  • 找到候选POI：餐饮10个、文化8个
  • 路线生成完成，共3个地点
  • 已补充团购/排队/趋势信息
  • 路线规划完成，已生成地图链接

=== 路线结果 ===
  1. 沈大成（南京东路店） (餐饮) | 评分:4.5 | 等位:中
  2. 豫园 (文化) | 评分:4.5 | 等位:高 | 团购:35元
  3. M50创意园 (文化) | 评分:4.6 | 等位:低

=== 总结 ===
为你安排了3站行程，预计游玩4小时，1处有团购优惠，餐饮消费约128元。
```

---

## 线上部署

后端已部署至 Railway，每次 `git push` 自动重新部署：

```
https://ai-route-planner-production.up.railway.app
```

| 接口 | 说明 |
|---|---|
| `GET /health` | 健康检查 |
| `POST /route/generate` | 首次生成路线（SSE 流式） |
| `POST /route/refine` | 局部替换（SSE 流式） |

---

## API 接口文档

### POST /route/generate

请求：
```json
{
  "user_input": "帮我规划上海外滩附近的周末下午，预算300元，想吃本帮菜，顺便逛文化景点",
  "conversation_history": [],
  "locked_nodes": []
}
```

### POST /route/refine

请求：
```json
{
  "user_input": "换一家不排队的餐厅",
  "conversation_history": [],
  "locked_nodes": [],
  "current_route": [/* 上一次 /route/generate 返回的 route 数组 */]
}
```

### SSE 事件流

```
event: step    → {"message": "已解析需求：上海外滩，预算300元，餐饮、文化"}
event: step    → {"message": "找到候选POI：餐饮10个、文化8个"}
event: step    → {"message": "路线生成完成，共3个地点"}
event: result  → {完整路线 JSON，见下方}
event: done    → {}
event: error   → {"message": "错误信息"}
```

### result 事件数据结构

```json
{
  "route": [
    {
      "order": 1,
      "name": "外婆家（南京西路店）",
      "category": "餐饮",
      "address": "南京西路1038号",
      "lat": 31.2245,
      "lng": 121.4491,
      "rating": 4.8,
      "avg_price_per_person": 128,
      "queue_risk": "高",
      "queue_risk_tip": "晚高峰等位约40分钟，建议17:30前到店",
      "has_group_buy": true,
      "group_buy": {
        "title": "双人尊享套餐",
        "original_price": 380,
        "current_price": 258,
        "discount": "6.8折"
      },
      "stay_minutes": 90,
      "transport_to_next": "步行约8分钟",
      "transport_polyline": "121.4491,31.2245;121.4510,31.2250;...",
      "navigation_url": "https://uri.amap.com/navigation?to=...",
      "trend_tag": "火爆（已售1.2万单）"
    }
  ],
  "map_url": "https://restapi.amap.com/v3/staticmap?...",
  "summary": "为你安排了3站行程，预计游玩4小时，1处有团购优惠，餐饮消费约258元。",
  "agent_steps": ["已解析需求：...", "找到候选POI：...", "路线生成完成"]
}
```

**关键字段说明**：
- `transport_polyline`：从该 POI 到下一个 POI 的步行路径坐标串（`"lng,lat;lng,lat;..."`），前端 JS 地图用此字段绘制蓝线；最后一个 POI 为 `null`
- `navigation_url`：高德导航 URI，手机点击直接跳转导航 App
- `map_url`：后端生成的静态地图图片 URL，可作为动态地图的备用方案

---

## 项目结构

```
ai-route-planner/
├── route_planner/             # 核心业务包
│   ├── state.py               # RouteState TypedDict（全局状态）
│   ├── node.py                # BaseNode 基类
│   ├── graph.py               # LangGraph 流水线（build_graph + build_refine_graph）
│   ├── llm.py                 # DeepSeek + Claude fallback，指数退避重试
│   ├── nodes/
│   │   ├── intent.py          # IntentAgent：意图解析（LLM）
│   │   ├── poi_search.py      # POISearchAgent：候选召回（SQLite 查询）
│   │   ├── geo_cluster.py     # GeoClusterNode：地理聚合 + 时间约束（纯代码）
│   │   ├── route.py           # RouteAgent：路线规划（LLM）
│   │   ├── enrich.py          # EnrichAgent：数据补充（纯代码）
│   │   ├── output.py          # OutputAgent：步行路径 + 导航链接 + 地图 URL
│   │   ├── refine.py          # RefineNode：解析"换一家"意图（LLM）
│   │   └── refine_select.py   # RefineSelectNode：选最优替换 POI（纯代码）
│   └── data/
│       ├── poi.csv            # POI 数据源（100条，GitHub 直接查看，Excel 可编辑）
│       └── poi.db             # SQLite 运行时数据库（setup.sh 自动生成，不提交 git）
├── app/
│   ├── main.py                # FastAPI 路由 + SSE 流式 + 内存缓存
│   └── schemas.py             # Pydantic 请求/响应模型
├── scripts/
│   ├── run_pipeline.py        # 完整流水线测试
│   ├── run_intent.py          # IntentAgent 单测
│   └── migrate_to_sqlite.py   # poi.csv → poi.db（setup.sh 自动调用）
├── docs/
│   ├── ARCHITECTURE.md        # 系统架构详解
│   └── frontend_guide_for_C.md # 前端接入指南（成员 C 专用）
├── setup.sh                   # 一键建环境（创建 .venv，装依赖，生成 poi.db）
├── Procfile                   # Railway 启动命令
├── railway.toml               # Railway 部署配置
├── requirements.txt           # Python 依赖列表（供 Railway/pip 使用）
├── pyproject.toml             # 项目元数据和依赖定义
├── .env.example               # 环境变量模板
└── README.md
```

---

## POI 数据维护

数据存储在 `route_planner/data/poi.csv`，提交到 git，GitHub 上可直接查看表格。

**添加/修改 POI**：
1. 编辑 `poi.csv`（Excel 或任意编辑器）
2. `git push` 后 Railway 自动重新部署，`poi.db` 同步更新
3. 本地开发重新运行 `bash setup.sh` 或 `python scripts/migrate_to_sqlite.py` 刷新 `poi.db`

---

## 环境变量说明

| 变量 | 用途 | 必填 |
|---|---|---|
| `DEEPSEEK_API_KEY` | 主力 LLM | ✅ |
| `ANTHROPIC_API_KEY` | Fallback LLM | 建议 |
| `AMAP_API_KEY` | 高德 Web 服务 Key（静态地图 + 步行路径） | ✅ |

> 前端动态地图所需的高德 **Web 端 JS Key** 不在此处配置，直接写入前端 HTML。

Railway 部署时在项目 Variables 面板填写，不进代码。

---

## 开发进度

- [x] 项目骨架 + IntentAgent + DeepSeek API 调通
- [x] 完整 LangGraph 流水线（5 个节点全部接通，SQLite POI 数据库 100 条）
- [x] FastAPI + SSE 流式输出（/route/generate、/route/refine、/health，内存缓存）
- [x] RefineAgent 局部替换（RefineNode LLM + RefineSelectNode 纯代码，1 次 LLM 调用）
- [x] OutputAgent 新增：真实步行路径蓝线（高德 Walking Directions API）+ 一键导航链接 + 动态地图坐标字段
- [x] Railway 部署上线，自动 HTTPS，push 即部署
- [ ] 前后端联调（成员 C 接入 NoCode）
- [ ] 优化加分项（小红书风格输出、用户记忆）+ 录制 Demo
- [ ] 文档整理 + 提交

---

## 团队分工

| 成员 | 负责内容 |
|---|---|
| 成员 A | LangGraph Agent 框架、LLM Prompt、FastAPI 后端、Railway 部署 |
| 成员 B | 高德地图 API、POI 数据（poi.csv）、路线评分 |
| 成员 C | NoCode 前端（参见 docs/frontend_guide_for_C.md）、PPT、Demo 视频 |
