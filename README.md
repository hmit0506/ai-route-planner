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
- **小红书式攻略导出**：每次规划后自动生成 `xiaohongshu_post` 文本，格式为社媒分享风格（路线摘要、时长、预算、适合人群、天气提醒、团购亮点、避坑提示、话题标签），无需额外 LLM 调用，三语各有专属模板
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
- 启动时自动执行 `migrate_to_sqlite.py` 生成 `poi.db`

---

## API 接口

### POST /route/generate

```json
{
  "user_input": "旺角附近下午，想吃日本料理，預算400港幣",
  "language": "zh-TW",
  "conversation_history": [],
  "locked_nodes": [],
  "user_id": "user_abc123"
}
```

`language` 可选值：`"zh-TW"`（繁体，默认）、`"zh-CN"`（简体）、`"en"`（English）。  
`user_id` 可选；传入后自动加载/更新用户偏好记忆，不传则匿名无记忆。

### POST /route/refine

```json
{
  "user_input": "换一家不排队的餐厅",
  "conversation_history": [],
  "locked_nodes": [],
  "current_route": [/* 上次 result 事件中的 route 数组 */]
}
```

### SSE 事件流

```
event: step    → {"message": "💡 ...推理过程..."}
event: step    → {"message": "已解析需求：..."}
event: step    → {"message": "找到候选POI：..."}
event: step    → {"message": "地理聚合完成：..."}
event: step    → {"message": "路线生成完成，共N个地点"}
event: step    → {"message": "已补充团购/排队/趋势信息"}
event: step    → {"message": "路线规划完成，已生成地图链接"}
event: result  → {完整路线 JSON}
event: done    → {}
```

### result 事件 route 字段说明

| 字段 | 说明 |
|---|---|
| `transport_polyline` | 步行路径坐标串 `"lng,lat;..."` ，前端 JS 地图绘制蓝线用；最后一个 POI 为 null |
| `navigation_url` | 高德导航 URI，手机点击跳转导航 App |
| `map_url` | 后端生成的静态地图图片 URL（标记点 + 步行蓝线） |
| `queue_risk_tip` | 人性化排队提示，如"晚高峰等位约40分钟，建议17:30前到店" |
| `group_buy.discount` | 团购折扣率，如"6.8折" |
| `trend_tag` | 含销量的趋势标签，如"火爆（已售1.2万单）" |
| `risk_mention_rate` | 负面体验短语占比（0~1，均值0.6）；越低越安全，前端可展示安全评级 |
| `queue_mention_rate` | 排队抱怨占比（0~1，均值0.3）；>0.5 可展示排队警告 |
| `photo_mention_rate` | 拍照打卡短语占比（0~1）；高值可展示"打卡热点"标签 |
| `local_mention_rate` | 地道/本土感短语占比（0~1）；高值可展示"地道老铺"标签 |
| `year_max` | 最近收到评论的年份（2021-2025）；前端可展示"活跃" / "久未更新"提示 |
| `scenario_tags` | 场合标签，如 `"情侶約會;朋友聚餐"`；前端可展示场合适配图标 |
| `tags` | 正向标签列表，如 `["高口碑","團購划算","冷門寶藏"]`；已按 language 翻译（en: `["Highly Rated","Great Deal","Hidden Gem"]`） |
| `risk_tags` | 风险标签列表，如 `["排隊較高","踩雷風險"]`；已按 language 翻译 |

**顶层字段**（与 route 同级）：

| 字段 | 说明 |
|---|---|
| `weather` | WeatherNode 输出：`{"condition":"rain","temperature":22,"prefer_indoor":true,...}` |
| `xiaohongshu_post` | 小红书式攻略文本，含路线/预算/场景/天气/团购/避坑/话题标签，按 language 输出 |

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
│   ├── run_pipeline.py        # 完整流水线测试
│   ├── run_intent.py          # IntentAgent 单测
│   ├── test_20cases.py        # 三语 20 案例系统测试（A组纯逻辑/B组Intent/C组全流水线）
│   ├── migrate_to_sqlite.py   # poi.csv → poi.db（setup.sh 自动调用）
│   └── migrate_hk_to_csv.py  # OpenRice xlsx → poi.csv（本地维护数据用）
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

**从 OpenRice xlsx 重新生成**（更新原始数据集后）：
```bash
PYTHONPATH=. .venv/bin/python3 scripts/migrate_hk_to_csv.py
git add route_planner/data/poi.csv && git push
```
Railway 重新部署时自动重建 `poi.db`。

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

---

## 开发进度

- [x] 项目骨架 + IntentAgent + DeepSeek API 调通
- [x] 完整 LangGraph 流水线（5个节点全部接通，SQLite POI 数据库 100条）
- [x] FastAPI + SSE 流式输出（/route/generate、/route/refine、/health，内存缓存）
- [x] RefineAgent 局部替换（RefineNode LLM + RefineSelectNode 纯代码，1次 LLM 调用）
- [x] 步行路径蓝线（高德 Walking Directions API）+ 一键导航链接 + 动态地图坐标字段
- [x] Railway 部署上线，自动 HTTPS，push 即部署
- [x] GeoClusterNode：地理聚合 + 时间感知站点数（去掉硬性类别配比，交给 LLM 决策）
- [x] IntentNode：CoT 推理可见 + 代码层自动校验 + meal_plan 精确餐次提取
- [x] RouteAgent：决策维度增强（性价比/非峰等位/口味评分/评价数）+ dining_count 约束 + 自我检查重试（结果可见）
- [x] POISearchNode：接入 culture_pref / avoid / 全部 food_pref 偏好字段
- [x] RefineNode：修复多轮对话 bug（从路线 POI 提取地理上下文，设置搜索类别）
- [x] RefineSelectNode：修复重复替换 bug（排除所有当前路线 POI）
- [x] OutputNode：步行路径并行请求（ThreadPoolExecutor，最坏 3s vs 原来 N×3s）
- [x] 缓存升级：两级 key（原始输入 + intent 结构化 key），不同说法相同意图可共享缓存
- [x] fulfillment_notes：履约报告推入 SSE 步骤流和 summary，前端零改动可见
- [x] 三语支持（zh-TW / zh-CN / en）：所有用户可见字段随 language 切换
- [x] 真实 HK 数据集：18,089 家香港餐厅（OpenRice 2021–2025），75 个中文类别标签，双语字段
- [x] 多标签 sub_category（88% 餐厅，LIKE 命中任意标签）+ IntentNode food_pref 词汇对齐
- [x] 字段级翻译：sub_category / category / trend_tag / queue_risk 英文模式自动翻译
- [x] 全链路三语一致性：所有步骤消息（IntentNode / POISearch / GeoCluster / RouteNode / EnrichNode / OutputNode）均走 i18n；CoT 推理行后处理确保繁体输出；验证错误消息三语化
- [x] 语言自动检测：run_pipeline.py 根据输入字符集自动判断 zh-CN / zh-TW / en
- [x] i18n 重构：引入 OpenCC 替换手写字符对（85条→库），繁简互转覆盖所有汉字；CoT 格式指令语言化（__COT_FORMAT__ 占位符），无需后处理；city/area 三语显示翻译
- [x] 数据质量修复：avg_price 改为多 tag 取最高价（130占比 69%→8.7%）；queue_minutes 加哈希变化（原3个固定值→均匀分布）；过滤 taste_rating=0 无效行
- [x] 全字段利用：RouteAgent 新增 hygiene/decor/service_rating、trend_tag、review_count、recommend_count；EnrichNode 输出全部细分评分；recommend_count 改为真实评论总数（原 total×150）
- [x] 文化/娱乐/自然景点数据：136 条香港景点（博物館 18、泳灘 42、郊野公園 25、主要景點 13、公園 18、表演場地 / 露天劇場 8 等），与餐饮库合并为 18,211 条统一 poi.csv
- [x] area 字段全量填充：用 DeepSeek 批量地理编码，将所有"香港"通用区名替换为精确社区名（旺角/中環/灣仔/柴灣…），18,211 行 100% 覆盖
- [x] i18n 地名英文翻译全覆盖：_LOCATION_EN 扩展至 204 个香港社区，18,211 行 100% 可英文输出
- [x] 补充 37 条地标景点（天壇大佛、大館、PMQ、山頂纜車、赤松黃大仙祠、志蓮淨苑、天星小輪等），去重后合并为 18,248 条；i18n 扩展至 209 个社区，100% 覆盖
- [x] GeoClusterNode 升级：area 真实坐标锚点（area_coords.py，90+ 社区），半径 3km→2km，锚点准确后过滤才真正有效
- [x] 用户记忆系统（user_memory.py）：user_id 持久化菜系/忌口/预算/已访问 POI；路线生成后异步更新；历史偏好注入 RouteAgent 软约束
- [x] 营业时间过滤（POISearchNode）：按 intent.time_range 过滤候选，soft fallback 避免结果过少
- [x] 高德 POI 兜底（POISearchNode）：候选 < 3 条时自动调用高德 Place Search API 补充
- [x] 数据补全：business_hours 按 sub_category 为 18,075 家餐厅生成合理营业时间（all_day / split / evening / brunch 四类）；has_group_buy 按价格档位为 8,512 家（47%）餐厅生成团购套餐数据
- [x] 缓存 key 加入 language 字段，防止跨语言缓存污染；缓存命中路径补 user_memory 更新；缓存命中 SSE 消息走 i18n
- [x] RefineNode 新增 prefer_sub_category 约束（支持"换一家日本料理"等带菜系的替换）；prefer_sub_category 传入 POISearchNode 偏好排序 + RefineSelectNode 优先筛选
- [x] refine 流程结束后补 user_memory.update()；call_llm 异常捕获扩至 Exception（覆盖 JSONDecodeError）；高德兜底/替换成功/失败 SSE 消息全部 i18n 化；enrich_done 三语补全
- [x] 三语 20 案例系统测试 + 4 项 bug 修复：① 英文 refine 0 候选（_normalize_cat 规范化 Dining→餐饮）；② zh-CN fulfillment 含繁体（pref 值在 _build_fulfillment 中先翻译）；③ 英文模式文化类 sub_category 无英译（扩展 _SUB_CATEGORY_EN 15+ 词条）；④ dining_excess 消息方向反（新增 dining_excess/dining_excess_tip 三语 key）
- [x] 三语 50 案例深度测试 + 4 项修复：① RouteNode dining_count 强制执行（代码级截断 + 精确 correction prompt）；② zh-CN fulfillment POI 名字繁→简（`_name()` 辅助函数）；③ 文化类 POI 排队风险修正（103 条公园/海滩/郊野公园从"高"修为"低"）；④ POISearch 区域无覆盖时对所有类别触发 Amap（area_mismatch 检测）
- [x] 评论信号系统：从 OpenRice 5年评论分析（POI_profile_extra_keywords.csv，23,541 POI）提取 11 个信号字段写入 poi.csv 和 poi.db；queue_risk 字段由 queue_signal_level 真实数据覆盖（原为 hash mock）
- [x] 信号驱动 SQL 排序（POISearchNode）：risk_mention_rate ASC + year_max DESC 始终生效；prefer_local→local_mention_rate DESC；打卡拍照场景→photo_mention_rate DESC；家庭親子场景→accessibility_mention_rate DESC；场合 scenario_tags LIKE 匹配排序
- [x] IntentAgent 新增 prefer_local（检测"地道/本地/老字号"）+ scenarios（情侶約會/朋友聚餐/家庭親子/慶生/商務接待/一人食/打卡拍照）字段
- [x] RouteNode LLM 决策扩展：compact 传入 8 个信号字段，system prompt 提供均值基线（risk均值0.6/queue均值0.3）和阈值指引，LLM 可精确推理踩雷风险、排队建议、地道偏好
- [x] 所有信号字段流经 EnrichNode → 最终路线输出，前端可直接使用
- [x] 天气感知路线（WeatherNode）：高德天气API，5类天气条件（晴/雨/高温/寒冷/恶劣），雨天/高温自动注入 prefer_indoor，RouteNode 天气感知路线策略，SSE 三语天气提示
- [x] 实时 POI 搜索增强：大陆城市高德API优先（pref关键词精准搜索）+ 香港SQLite优先；_is_hk_city 城市识别；多关键词并发搜索去重合并
- [x] POI 标签体系：10个正向标签 + 3个风险标签，基于评论信号字段计算，天气感知动态追加"雨天友好"，三语全覆盖（translate_tag/translate_tags）
- [x] 小红书式攻略导出（xiaohongshu_post）：LLM 生成（200-350字，emoji 丰富，第一人称博主语气，编号站点+具体菜品+实用 tips+话题标签）；与步行路径并行生成零额外延迟；出错时 template 兜底；三语各有独立格式要求
- [x] 三语 20 案例深度测试 + 6 项 bug 修复：① zh-TW 小红书 body 含简体（city/area 未转繁体）；② dining_count=1 全餐饮候选时路线空白（_validate 条件放宽 + _force_dining_count 回退保护）；③ tags/risk_tags 固定繁体（新增 translate_tag/translate_tags 三语翻译）；④ trend_tag 自定义标签未翻译（拆解多标签逐词翻译）；⑤ culture_pref 文化/藝術等词未翻译（补充 _SUB_CATEGORY_EN）；⑥ 测试检测字符串设计缺陷（排除简繁同码字符）
- [x] 英文模式 POI 名称显示修复：EnrichNode 在 en 模式优先使用 name_en，zh-CN 模式对 name 调用 to_simplified()，zh-TW 保持原始繁体；run_pipeline.py 新增小红书贴文区块 + POI 标签显示
- [x] 三语语言纯洁性系统修复（共 3 处根本问题）：① zh-CN 路线 POI name 仍为繁体（EnrichNode 加 to_simplified）；② zh-CN 小红书 LLM 生成港式内容时忽略简体指令（生成后强制 OpenCC 转换）；③ en 小红书输出中文（CJK 比例检测 + 重试机制 + format prompt 细化为编号结构）；原则：LLM 自由文本不可信任语言指令，必须在输出端 OpenCC 兜底
- [ ] 前后端联调（成员 C 接入 NoCode）
- [ ] 录制 Demo + 提交

---

## 团队分工

| 成员 | 负责内容 |
|---|---|
| 成员 A | LangGraph Agent 框架、LLM Prompt、FastAPI 后端、Railway 部署 |
| 成员 B | 高德地图 API、POI 数据（poi.csv）、路线评分 |
| 成员 C | NoCode 前端（参见 docs/frontend_guide_for_C.md）、PPT、Demo 视频 |
