# AI 本地路线智能规划

> 美团 Hackathon 第五题「现在就出发：AI本地路线智能规划」
> DDL：2026年6月7日 24:00

**线上地址**：`https://ai-route-planner-production.up.railway.app`

---

## 项目简介

用 LLM × POI 数据 × 用户偏好，自动生成「直接用、不踩雷」的个性化本地路线方案。用户一句话描述需求（城市、商圈、时间、预算、口味偏好），系统自动规划路线，标注排队风险、团购优惠、实时天气，并在地图上展示完整行程。

---

## 核心能力

### 智能理解与三语支持
- **自然语言解析**：CoT 推理 + 代码层自动校验，提取城市、时段、预算、餐次、偏好等结构化字段
- **三语全覆盖**：`zh-TW` / `zh-CN` / `en`，进度消息、摘要、排队提示、类别名称等所有用户可见文字随 `language` 字段切换
- **词汇对齐**：将"壽司"、"下午茶"、"打邊爐"等自然语言规范化为数据库 sub_category 标准词，SQL LIKE 精准命中

### 数据与 POI 召回
- **18,248 条香港真实 POI**：18,075 家餐厅（OpenRice 2021–2025 真实评论）+ 173 个文化/娱乐/自然景点；75 个中文类别标签，88% 餐厅带多标签
- **大陆城市实时搜索**：优先调用高德 Place Search API，按 food_pref/culture_pref 关键词并发搜索去重，香港城市 SQLite 优先（本地数据更丰富）
- **11 个评论信号字段**：risk/queue/photo/local/accessibility mention rate + year_max + 四个 level 标签 + scenario_tags，驱动 SQL 预排序和 LLM 决策；低风险、近年活跃优先

### 路线规划
- **地理聚合**：以意图 area 真实坐标为锚点（90+ 香港/上海社区对照表），半径 2km 过滤，避免"两头跑"
- **天气感知**：调用高德天气 API，识别晴/雨/高温/寒冷/恶劣 5 种天气；雨天/高温自动注入 `prefer_indoor=true`，RouteNode 切换为室内优先策略
- **多维度决策**：综合评分、性价比、排队峰值/非峰值、口味评分、销量热度、团购价；精确餐次规划（`dining_count` 字段）
- **自我检查**：RouteNode 输出后代码验证合理性，不通过则携带纠正说明重试一次
- **营业时间过滤**：POI 召回阶段过滤与用户时间段不重叠的场所，候选不足时 soft fallback

### 展示与分享
- **静态地图**：高德 Web 服务 API，POI 打点 + 真实步行路径蓝线（后端生成图片 URL）
- **动态地图**：高德 JS SDK 2.0，前端可缩放交互，点击 POI 弹出详情
- **一键导航**：每个 POI 附带高德导航链接，手机点击直接跳转
- **小红书式攻略**：路线发送后异步 LLM 生成，含路线摘要、团购亮点、避坑提示、话题标签，通过独立 `xiaohongshu_update` SSE 事件推送（三语各有专属模板）

### 多轮对话与用户记忆
- **局部替换**：支持"换一家不排队的餐厅"等，仅 1 次 LLM 调用；替换结果保持在同一地理区域内；替换后同步生成新小红书攻略
- **用户记忆**：传入 `user_id` 自动加载历史偏好（菜系、忌口），已访问 POI 自动排除，路线生成后异步更新记忆
- **履约报告**：每次规划输出 satisfied / unmatched / tips，说明哪些需求满足、哪些用了替代方案

---

## 系统架构

### 首次生成（2 次 LLM 调用）

```
用户输入
  → IntentNode       LLM①：CoT推理 + 结构化意图 JSON，代码层自动校验
  → WeatherNode      纯代码：高德天气 API，注入天气感知字段到 intent
  → POISearchNode    纯代码：HK用SQLite优先，大陆城市用高德API优先
  → GeoClusterNode   纯代码：地理聚合 + 时间→站点数
  → RouteNode        LLM②：多维度决策，天气感知路线选择，自我检查重试
  → EnrichNode       纯代码：poi_id→完整字段，计算排队/团购/趋势/POI标签
  → OutputNode       纯代码：步行路径、导航链接、地图URL、摘要、小红书导出
```

### 局部替换（1 次 LLM 调用）

```
用户："换一家不排队的餐厅"
  → RefineNode       LLM①：解析替换意图，确定节点和约束
  → POISearchNode    纯代码：只搜被替换类别的候选
  → RefineSelectNode 纯代码：按约束选最优替换 POI，合并回原路线
  → EnrichNode → OutputNode
```

架构细节、Agent 状态定义、LLM Prompt 设计见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。前端接入指南（SSE 读法、字段说明、地图代码）见 [docs/ARCHITECTURE.md §13](docs/ARCHITECTURE.md#13-前端接入指南)。

---

## 技术栈

| 模块 | 技术 |
|---|---|
| LLM 主力 | DeepSeek（OpenAI 兼容格式，成本低、中文强） |
| LLM Fallback | Claude Sonnet 4.6（DeepSeek 限流时自动切换，指数退避） |
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

脚本自动完成：创建 `.venv`、安装依赖、生成 `.env.example`。`poi.db` 在首次启动服务时自动从 `poi.csv` 生成。

### 2. 填入 API Key

编辑 `.env`：

| 变量 | 用途 | 必填 |
|---|---|---|
| `DEEPSEEK_API_KEY` | 主力 LLM | ✅ |
| `ANTHROPIC_API_KEY` | Fallback LLM | 建议 |
| `AMAP_API_KEY` | 高德 Web 服务 Key（静态地图 + 天气 + 步行路径） | ✅ |

> 后端 `AMAP_API_KEY` 是 **Web 服务 Key**（HTTP 调用）。前端动态地图需单独申请 **Web 端 JS Key**，详见 [docs/ARCHITECTURE.md §13.9](docs/ARCHITECTURE.md#139-注意事项)。

### 3. 启动后端

```bash
PYTHONPATH=. .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

访问 `http://localhost:8000/health` 返回 `{"status":"ok"}` 即启动成功。

### 4. 验证流水线

```bash
PYTHONPATH=. .venv/bin/python3 scripts/run_pipeline.py
PYTHONPATH=. .venv/bin/python3 scripts/run_pipeline.py "旺角附近下午，想吃日本料理，預算400港幣"
PYTHONPATH=. .venv/bin/python3 scripts/run_pipeline.py "中環一整天，包括午餐和晚餐，預算600"
```

---

## 线上部署

**Railway 地址**：`https://ai-route-planner-production.up.railway.app`

- `git push` 到 main 自动触发重新部署
- API Key 在 Railway Variables 面板填写，不进代码
- 启动时 `app/main.py` lifespan 自动生成 `poi.db`（CSV 比 DB 新时重建）

---

## API 接口

```
POST /route/generate   首次生成路线（SSE 流式）
POST /route/refine     局部替换（SSE 流式）
GET  /health           健康检查
```

详细接口规范、SSE 事件流、完整字段说明及前端接入代码见 [docs/ARCHITECTURE.md §13](docs/ARCHITECTURE.md#13-前端接入指南)。

---

## 项目结构

```
ai-route-planner/
├── route_planner/
│   ├── i18n.py                # 三语翻译模块（文字模板 + 字段级翻译）
│   ├── area_coords.py         # area 名 → (lat, lng) 对照表（90+ 香港/上海社区）
│   ├── user_memory.py         # 用户偏好记忆（load/save/update，JSON 文件持久化）
│   ├── state.py               # RouteState TypedDict（全局状态）
│   ├── node.py                # BaseNode 基类
│   ├── graph.py               # LangGraph 流水线（build_graph + build_refine_graph）
│   ├── llm.py                 # DeepSeek + Claude fallback，指数退避重试
│   ├── nodes/
│   │   ├── intent.py          # IntentNode：CoT意图解析 + 代码层自动校验（LLM）
│   │   ├── weather.py         # WeatherNode：高德天气API，天气感知路线调整（纯代码）
│   │   ├── poi_search.py      # POISearchNode：HK=SQLite优先，大陆=高德API优先（纯代码）
│   │   ├── geo_cluster.py     # GeoClusterNode：地理聚合 + 时间约束 + 类别配比（纯代码）
│   │   ├── route.py           # RouteNode：多维度路线决策（LLM）
│   │   ├── enrich.py          # EnrichNode：数据补充（纯代码）
│   │   ├── output.py          # OutputNode：步行路径 + 导航链接 + 地图 URL（纯代码）
│   │   ├── refine.py          # RefineNode：解析"换一家"意图（LLM）
│   │   └── refine_select.py   # RefineSelectNode：选最优替换 POI（纯代码）
│   └── data/
│       ├── poi.csv            # POI 数据源（18,248 条，GitHub 直接查看，Excel 可编辑）
│       ├── poi.db             # SQLite 运行时数据库（启动时自动生成，不提交 git）
│       └── users/             # 用户记忆 JSON 文件目录（运行时生成，不提交 git）
├── app/
│   ├── main.py                # FastAPI 路由 + SSE 流式 + 内存缓存
│   └── schemas.py             # Pydantic 请求/响应模型
├── scripts/
│   ├── run_pipeline.py        # 完整流水线本地测试
│   └── run_intent.py          # IntentAgent 单独验证
├── docs/
│   └── ARCHITECTURE.md        # 系统架构详解 + 前端接入指南（§13）
├── setup.sh                   # 一键建环境
├── Procfile                   # Railway 启动命令
├── railway.toml               # Railway 部署配置
├── requirements.txt           # Python 依赖（供 Railway/Railpack 使用）
├── pyproject.toml             # 项目元数据和依赖定义
└── .env.example               # 环境变量模板
```

---

## POI 数据维护

数据存储在 `route_planner/data/poi.csv`（18,248 条香港 POI，GitHub 上可直接查看表格）。编辑 `poi.csv` 后 `git push`，Railway 自动重新部署并重建 `poi.db`。

---

## 团队分工

| 成员 | 负责内容 |
|---|---|
| 成员 A | LangGraph Agent 框架、LLM Prompt、FastAPI 后端、Railway 部署 |
| 成员 B | 高德地图 API、POI 数据（poi.csv）、路线评分 |
| 成员 C | NoCode 前端、PPT、Demo 视频（前端接入见 [docs/ARCHITECTURE.md §13](docs/ARCHITECTURE.md#13-前端接入指南)） |
