# AI 本地路线智能规划

> 美团 Hackathon 第五题「现在就出发：AI本地路线智能规划」
> 提交 DDL：2026年6月7日 24:00

**线上地址**：`https://ai-route-planner-production.up.railway.app`

---

## 项目简介

用 LLM × POI 数据 × 用户偏好，自动生成「直接用、不踩雷」的个性化本地路线方案。

用户一句话描述需求（城市、商圈、时间、预算、口味偏好），系统自动规划合理路线，标注排队风险、团购优惠、实时趋势，并在地图上展示完整行程。

---

## 核心能力

- **自然语言理解**：解析"旺角附近、预算400、想吃日本料理"等自由格式输入；CoT 推理过程可见，代码层自动校验修正
- **三语支持**：前端传 `language` 字段（`zh-TW` / `zh-CN` / `en`），所有用户可见文字（进度消息、摘要、排队提示、交通说明、类别名称、履约报告）随语言切换；CoT 推理行自动语言检测 + 后处理保证繁体输出；POI 含双语字段（`name_en`、`address_en`）
- **真实 POI 数据**：18,248 条香港 POI（18,075 家餐厅 + 173 个文化/娱乐/自然景点），餐厅来源 OpenRice 2021–2025 真实评论数据；景点含博物館、泳灘、郊野公園、主要景點、歷史建築、宗教古蹟、觀景地標等；75 个中文类别标签，88% 餐厅带多标签，LIKE 查询可命中任意标签
- **时间感知规划**：根据行程时长自动决定站点数（3-8站）
- **地理聚合**：以意图 area 的真实坐标为锚点（90+ 香港/上海社区对照表），半径 2km 过滤，确保所有站点在合理步行范围内，避免"两头跑"
- **营业时间过滤**：POI 召回阶段自动过滤与用户时间段不重叠的场所；候选不足时 soft fallback 保留原始结果
- **天气感知路线**：调用高德天气 API 获取用户出行日期/时段的实时天气预报，识别晴/雨/高温/寒冷/恶劣 5 种天气；雨天/高温自动注入 `prefer_indoor=true` 到 intent，RouteNode 系统提示随之切换为室内优先策略；SSE 步骤流实时推送天气提示（三语）
- **实时 POI 搜索**：大陆城市（非香港）优先调用高德 Place Search API 获取实时数据，按 `food_pref`/`culture_pref` 关键词精准搜索（如"日本料理|壽司"），多关键词并发搜索后去重合并；香港城市保持 SQLite 优先（本地数据更丰富），高德作为兜底
- **高德 POI 兜底**：本地数据库候选 < 3 条时，自动调用高德 Place Search API 补充候选，并在 SSE 步骤流中提示
- **评论信号驱动**：11 个来自真实 OpenRice 评论的信号字段（risk/queue/photo/local/accessibility mention rate + year_max + 四个 level 标签 + scenario_tags）参与 SQL 预排序和 LLM 决策；低风险优先、近年仍活跃优先；prefer_local / 打卡拍照 / 家庭親子等场合需求精准匹配
- **POI 标签体系**：每个 POI 自动生成结构化正向标签（高口碑/團購划算/性價比高/本地人常去/拍照出片/低排隊/冷門寶藏/適合情侶/親子友好/雨天友好）和风险标签（踩雷風險/排隊較高/網紅打卡），基于评论信号字段计算，天气感知可动态追加「雨天友好」；三语全覆盖（zh-TW繁体/zh-CN简体/en英文）
- **小红书式攻略导出**：路线结果发送后，异步调用 LLM 生成 `xiaohongshu_post`，格式为社媒分享风格（路线摘要、时长、预算、适合人群、天气提醒、团购亮点、避坑提示、话题标签），通过独立 `xiaohongshu_update` SSE 事件推送，不阻塞路线结果；三语各有专属格式要求，模板兜底保障稳定性
- **多维度决策**：综合评分、性价比、排队峰值/非峰值、口味评分、销量热度，选出最优路线
- **用户记忆**：传入 `user_id` 即自动加载历史偏好（菜系、忌口、消费习惯），注入 RouteAgent 作为软约束；已访问 POI 自动从候选中排除，避免重复推荐；路线生成后异步更新记忆
- **词汇对齐**：IntentNode 将用户自然语言（"壽司"、"下午茶"、"打邊爐"）规范化为数据库 sub_category 标准词，SQL LIKE 精准命中
- **排队风险预警**：高峰等位提示 + 错峰安排建议
- **静态地图**：高德静态地图打点 + 真实步行路径蓝线（后端生成图片 URL）
- **动态地图**：前端嵌入高德 JS SDK，可缩放交互，点击 POI 弹出详情
- **一键导航**：每个 POI 附带高德导航链接，手机点击直接跳转导航 App
- **精确餐次规划**：提取用户明确说明的餐饮活动数量（`dining_count`），RouteAgent 按数量安排对应站点
- **自我检查**：RouteAgent 输出后代码验证合理性，不通过则携带纠正说明重试一次
- **多轮对话**：支持"换一家不排队的餐厅"等局部调整，1 次 LLM 调用；替换时从现有路线提取地理上下文，保证替换结果在同一区域内
- **履约报告**：每次规划后输出 satisfied / unmatched / tips，告知哪些需求满足了、用了什么替代、如何调整；不满足项同步推入 SSE 步骤流和 summary 字符串，前端无需额外处理

---

## 系统架构

### 首次生成（2次 LLM 调用）

```
用户输入
  → IntentNode       LLM①：CoT推理 + 结构化意图 JSON，代码层自动校验
  → WeatherNode      纯代码：高德天气 API，注入天气感知字段到 intent
  → POISearchNode    纯代码：HK用SQLite优先，大陆城市用高德API优先（pref关键词搜索）
  → GeoClusterNode   纯代码：地理聚合 + 时间→站点数
  → RouteNode        LLM②：多维度决策，天气感知路线选择
  → EnrichNode       纯代码：poi_id→完整字段，计算排队/团购/趋势/POI标签
  → OutputNode       纯代码：步行路径、导航链接、地图URL、摘要、小红书导出
```

### 局部替换（1次 LLM 调用）

```
用户："换一家不排队的餐厅"
  → RefineNode       LLM①：解析替换意图，确定节点和约束
  → POISearchNode    纯代码：只搜被替换类别的候选
  → RefineSelectNode 纯代码：按约束选最优替换 POI，合并回原路线
  → EnrichNode → OutputNode
```

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

脚本自动完成：创建 `.venv`、安装依赖、生成 `.env`。`poi.db` 在首次启动服务时自动从 `poi.csv` 生成。

### 2. 填入 API Key

编辑 `.env`：

```env
DEEPSEEK_API_KEY=sk-...        # 主力 LLM（必填）
ANTHROPIC_API_KEY=sk-ant-...   # Fallback LLM（建议填）
AMAP_API_KEY=...               # 高德 Web 服务 Key（静态地图 + 步行路径）
```

> `.env` 里的是**Web 服务 Key**（后端 HTTP 调用）。前端动态地图需要单独申请**Web 端 JS Key**，详见 [docs/frontend_guide_for_C.md](docs/frontend_guide_for_C.md)。

### 3. 启动后端

```bash
PYTHONPATH=. .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

访问 `http://localhost:8000/health` 返回 `{"status":"ok"}` 即启动成功。

### 4. 验证流水线

```bash
# 默认输入
PYTHONPATH=. .venv/bin/python3 scripts/run_pipeline.py

# 繁体中文（香港数据）
PYTHONPATH=. .venv/bin/python3 scripts/run_pipeline.py "旺角附近下午，想吃日本料理，預算400港幣"

# 多餐次行程
PYTHONPATH=. .venv/bin/python3 scripts/run_pipeline.py "中環一整天，包括午餐和晚餐，預算600"
```

### 预期输出

```
=== Agent 日志 ===
  • 💡 用户指定上海外滩，预算300元，提到本帮菜，时间14:00-21:00，约7小时...
  • 已解析需求：上海外滩，14:00-21:00（7小时），2人，预算300元，餐饮、文化
  • 找到候选POI：餐饮10个、文化8个
  • 地理聚合完成：中心半径3.0km，时间预算7小时→最多6站（餐饮≤2，文化/娱乐≥3）
  • 路线生成完成，共4个地点
  • 已补充团购/排队/趋势信息
  • 路线规划完成，已生成地图链接

=== 路线结果 ===
  1. 外滩源（圆明园路） (文化) | 评分:4.6 | 等位:低
  2. 建投书局（外滩店） (文化) | 评分:4.6 | 等位:低
  3. 上海老饭店（豫园店） (餐饮) | 评分:4.5 | 等位:中 | 团购:238元
  4. 豫园 (文化) | 评分:4.5 | 等位:高 | 团购:35元
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

详细接口规范、SSE 事件流、完整字段说明及前端接入代码见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) 第十三章。

---


## 项目结构

```
ai-route-planner/
├── route_planner/
│   ├── i18n.py                # 三语翻译模块（文字模板 + 字段级翻译）
│   ├── area_coords.py         # area 名 → (lat, lng) 对照表（90+ 香港/上海社区）
│   ├── user_memory.py         # 用户偏好记忆（load/save/update，JSON 文件持久化）
│   ├── state.py               # RouteState TypedDict（全局状态，含 language/user_memory 字段）
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
│   ├── ARCHITECTURE.md        # 系统架构详解
│   └── frontend_guide_for_C.md # 前端接入指南（成员 C 专用）
├── setup.sh                   # 一键建环境
├── Procfile                   # Railway 启动命令
├── railway.toml               # Railway 部署配置
├── requirements.txt           # Python 依赖（供 Railway/Railpack 使用）
├── pyproject.toml             # 项目元数据和依赖定义
└── .env.example               # 环境变量模板
```

---

## POI 数据维护

数据存储在 `route_planner/data/poi.csv`（18,248 条香港 POI，GitHub 上可直接查看表格）。

**直接编辑 poi.csv**（小幅修改）：
1. 编辑 `poi.csv`（Excel 或任意编辑器）
2. `git push` → Railway 自动重新部署，`poi.db` 同步更新

---

## 环境变量

| 变量 | 用途 | 必填 |
|---|---|---|
| `DEEPSEEK_API_KEY` | 主力 LLM | ✅ |
| `ANTHROPIC_API_KEY` | Fallback LLM | 建议 |
| `AMAP_API_KEY` | 高德 Web 服务 Key（静态地图 + 步行路径） | ✅ |

Railway 部署时在项目 Variables 面板填写，不进代码。


## 团队分工

| 成员 | 负责内容 |
|---|---|
| 成员 A | LangGraph Agent 框架、LLM Prompt、FastAPI 后端、Railway 部署 |
| 成员 B | 高德地图 API、POI 数据（poi.csv）、路线评分 |
| 成员 C | NoCode 前端（参见 docs/frontend_guide_for_C.md）、PPT、Demo 视频 |
