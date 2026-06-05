# 系统架构设计文档

---

## 一、设计理念

路线规划是一个**多约束组合优化问题**：在 N 个候选 POI 中，找出满足预算、时间、偏好、地理距离、排队风险等约束的最优子集，并按合理顺序串联。

**核心设计决策：LLM 做理解与决策，纯代码做数据处理与约束保障。**

- LLM 擅长软约束（"文艺气息"、"不想排队"、"带小孩"）和自然语言理解
- 纯代码擅长硬约束（预算上限、营业时间、地理距离）和数据格式化
- LLM 输出不可靠时由代码兜底自动修正（IntentAgent 自校验）

---

## 二、整体架构

### 首次生成流水线

```
用户自然语言输入
        │
        ▼
  ┌─────────────┐
  │ IntentNode  │  LLM①：CoT推理 + 结构化意图 JSON，代码层自动校验
  └──────┬──────┘
         │ intent: {city, area, duration_hours, budget, food_pref, ...}
         ▼
  ┌──────────────────┐
  │ POISearchNode    │  纯代码：SQLite 查询，按城市/商圈/类别召回 Top-10 候选
  └────────┬─────────┘
           │ candidates: {"餐饮": [...], "文化": [...]}
           ▼
  ┌──────────────────┐
  │ GeoClusterNode   │  纯代码：地理聚合过滤 + 时间预算→max_pois
  └────────┬─────────┘
           │ candidates（已过滤）+ intent.max_pois
           ▼
  ┌─────────────┐
  │  RouteNode  │  LLM②：多维度决策，选出最优路线（max_pois ±1 弹性）
  └──────┬──────┘
         │ route: [{poi_id, order, stay_minutes}, ...]
         ▼
  ┌─────────────┐
  │ EnrichNode  │  纯代码：poi_id → 完整字段，计算展示用派生字段
  └──────┬──────┘
         │ route: [{name, rating, queue_risk_tip, group_buy, ...}, ...]
         ▼
  ┌─────────────┐
  │ OutputNode  │  纯代码：步行路径、导航链接、静态地图 URL、摘要
  └──────┬──────┘
         ▼
  最终路线 JSON（含 transport_polyline）+ 高德静态地图 URL
```

**首次生成仅 2 次 LLM 调用**，其余均为纯代码，配合 SSE 流式推送保证 < 10 秒响应。

### 局部替换流水线（多轮对话）

```
用户："换一家不排队的餐厅"
        │
        ▼
  ┌────────────┐
  │ RefineNode │  LLM①：解析替换意图，确定节点和新约束
  └──────┬─────┘
         │ intent._refine: {replace_order, category, new_constraints}
         │ locked_nodes: [其余节点索引]
         ▼
  ┌──────────────────┐
  │ POISearchNode    │  纯代码：只搜被替换类别的候选
  └────────┬─────────┘
           ▼
  ┌──────────────────────┐
  │ RefineSelectNode     │  纯代码：按约束筛选 + 合并回原路线
  └──────────┬───────────┘
             ▼
  EnrichNode → OutputNode
```

**局部替换仅 1 次 LLM 调用**。

---

## 三、共享状态（RouteState）

```python
class RouteState(TypedDict):
    user_input: str               # 原始用户输入，全程不变
    language: str                 # "zh-TW" | "zh-CN" | "en"，由请求注入，全程传递
    intent: dict                  # IntentNode 写入；_refine 子键由 RefineNode 写入
    candidates: dict              # POISearchNode 写入，GeoClusterNode 过滤后更新
    route: list                   # RouteNode 写入骨架，Enrich/Output 逐步丰富
    locked_nodes: list            # 多轮对话中用户满意不替换的节点索引（0-based）
    map_url: str                  # OutputNode 写入
    summary: str                  # OutputNode 写入
    fulfillment_notes: dict       # OutputNode 写入：satisfied / unmatched / tips
    conversation_history: list    # 跨轮保留，传入 IntentNode
    stream_updates: list          # 每个节点追加，FastAPI 层实时推 SSE
```

每个节点只写自己关心的字段，其余透传（`{**state, "key": new_value}`）。

---

## 四、各节点详解

### 4.1 IntentNode（LLM）

**职责**：将自由格式自然语言映射为固定 Schema 的结构化 JSON，并做自我校验。

**两层保障**：

| 层次 | 方式 | 示例 |
|---|---|---|
| LLM 层（CoT） | 先输出推理过程，再输出 JSON | "用户提到一整天，因此 duration=12，需含文化类" |
| 代码层（自动修正） | 解析后校验并修复常见错误 | duration_hours 为空→从 time_range 计算；budget_per_person 对不上→自动修正 |

**代码层校验规则**：
- `duration_hours` 缺失 → 从 `time_range` 推算，兜底默认 4
- `budget_per_person` 与 `budget_total / party_size` 偏差 > 5 元 → 修正
- `must_include_categories` 为空 → 补 "餐饮"
- `dining_count` 非整数或负数 → 修正为 0
- 不自动追加"文化"类别——数据库可能无文化类 POI，强加会导致搜索失败

**多语言支持**：
- `language` 字段注入到 system prompt 首行 + user message 前缀，推理文字随用户语言输出
- zh-TW 模式下，CoT 格式指令在 system prompt 中即指定繁体中文格式（`__COT_FORMAT__` 占位符），从提示词层保证语言；`i18n.to_traditional()` 基于 OpenCC 作为兜底
- `city/area/food_pref` 字段统一输出繁体中文（数据库以繁体索引），避免简体/英文导致 LIKE 查询失效
- `must_include_categories` 代码层规范化为固定简体词（`餐饮/文化/娱乐`），确保数据库精确匹配

**food_pref 词汇对齐**：prompt 内嵌标准词对照表，将用户自然语言（"壽司"、"下午茶"、"打邊爐"）归一化为数据库实际 sub_category 词汇，确保 SQL LIKE 能命中。

**输出示例**：
```json
{
  "city": "香港", "area": "旺角",
  "time_range": {"start": "14:00", "end": "21:00"},
  "duration_hours": 7,
  "budget_total": 400, "budget_per_person": 200, "party_size": 2,
  "food_pref": ["日本料理"],
  "culture_pref": [],
  "avoid": [],
  "dining_count": 0,
  "must_include_categories": ["餐饮"]
}
```

`dining_count` 是整数：仅当用户明确指定餐饮次数时才 > 0（"包括午饭和晚饭"→2）；菜系偏好（"想吃日本料理"）不计入次数，输出 0。

---

### 4.2 POISearchNode（纯代码）

**职责**：从 SQLite 数据库（`poi.db`，由 `poi.csv` 启动时生成）按条件召回候选。

**数据源**：`poi.csv`（提交到 git，人工维护）→ `setup.sh` 迁移为 `poi.db`（不提交 git）。

**召回策略**：
1. `city LIKE ?` + `area LIKE ?` 模糊匹配
2. `avg_price_per_person <= budget_per_person × 1.2`（20% 弹性，避免过度截断）
3. `avoid` 中的子类别通过 `sub_category NOT IN (...)` 过滤排除
4. 偏好排序：餐饮类用 `food_pref`（全部项），文化/娱乐类用 `culture_pref`，匹配 `sub_category` 的 POI 优先
5. 按 `rating DESC`，每类取 Top-10
6. Fallback：若命中 < 3 个，退化为仅 city 过滤（放宽商圈限制）

---

### 4.3 GeoClusterNode（纯代码）

**职责**：地理聚合过滤 + 根据时间预算计算推荐站点数。

**逻辑**：
1. 合并所有类别候选，计算地理质心（lat/lng 均值）
2. 过滤掉距质心 > 3km 的 POI（防止选出跨区域组合）；若某类剩余 < 3 个则回退保留原始候选
3. 计算 `max_pois = max(3, min(8, floor(duration_hours × 60 / 65)))`
4. 将 `max_pois` 写入 intent，传递给 RouteNode 作为参考

**注意**：GeoClusterNode 只做地理和时间的约束，**不做类别配比**。餐饮数量和文化/娱乐数量由 RouteAgent 根据 `meal_plan` 自主决定，避免硬性规则覆盖用户的真实意图。

**为什么需要**：RouteNode 不知道真实交通时间，依赖 LLM 对坐标的直觉估算。GeoClusterNode 提前过滤离群 POI，从根本上避免"选了两头跑"的路线。

---

### 4.4 RouteNode（LLM）

**职责**：在地理过滤后的候选集中，综合多维度信息选出最优路线骨架。

**传入候选字段（compact 版）**：

| 字段 | 决策用途 |
|---|---|
| `rating` | 综合评分 |
| `review_count` | 评分可信度参考，越高越可信 |
| `recommend_count` | 口碑代理（5年评论总数，真实数据，范围 3-50） |
| `value_rating` | 性价比；预算有限时优先高性价比 |
| `hygiene_rating` | 卫生评分；所有 POI 均参考 |
| `taste_rating`（仅餐饮） | 食客最核心关注点 |
| `decor_rating` + `service_rating`（非餐饮） | 文化/娱乐类体验质量 |
| `avg_price_per_person` / `group_buy_price` | 实际花费；有团购时用团购价计算预算 |
| `queue_minutes_peak` / `queue_minutes_offpeak` | 峰值与非峰值等位时间，用于安排时段 |
| `half_year_sales` | 热门程度，同等条件下优先高销量 |
| `trend_tag` | 火爆 > 经典 > 新晋，辅助热度决策 |
| `business_hours` | 营业时间硬约束 |
| `lat/lng` | 地理相邻性判断 |

**餐饮数量决策**：
- 若 `dining_count > 0` → 餐饮站点数量必须恰好等于 `dining_count`
- 若 `dining_count == 0` → 合理安排即可，保证行程有非餐饮类站点

**自我检查机制**：LLM 输出后，代码验证：① 站点数 ≥ 3；② 餐饮数量匹配 dining_count；③ 非全餐饮。若不通过，携带纠正说明触发一次重试，并在 SSE stream 中显示 `⚠️ 自检发现问题`。

**intent 传递**：只传用户原始意图字段，剔除 GeoCluster 内部字段（`max_pois` 等），避免污染 LLM 的上下文理解。

**站点数量**：以 `max_pois` 为参考，可弹性调整，最少 3 站（赛题硬性要求）。

**输出**（最精简骨架，节省 token）：
```json
[
  {"poi_id": "poi_013", "order": 1, "stay_minutes": 90},
  {"poi_id": "poi_028", "order": 2, "stay_minutes": 45},
  {"poi_id": "poi_084", "order": 3, "stay_minutes": 90}
]
```

---

### 4.5 EnrichNode（纯代码）

**职责**：将 RouteNode 输出的 POI ID 骨架映射回完整字段，计算展示用派生字段，并按 `language` 翻译字段值。

| 派生字段 | 计算逻辑 |
|---|---|
| `queue_risk_tip` | 按语言输出：高→"晚高峰等位约N分钟"／"Peak hours wait ~N min"；支持三种语言 |
| `group_buy.discount` | `current_price / original_price × 10`，格式"6.8折" |
| `trend_tag` | 销量 ≥ 1万→"火爆（已售1.2万单）"；英文模式→"Trending (1.2k+ sold)" |

**字段级翻译**（`language="en"` 时）：
- `sub_category`："日本料理、壽司" → "Japanese / Sushi"（75 个标准词对照表）
- `category`："餐饮" → "Dining"
- `queue_risk`："高" → "High"
- `trend_tag`："火爆" → "Trending"

多轮对话时，已丰富的 locked POI 直接透传，不重复查找。

EnrichNode 输出的每个 POI 包含 `city`、`area`、`name_en`、`address_en` 和 `pref_matched` 字段：
- `city` / `area`：供 RefineNode 提取地理上下文
- `name_en` / `address_en`：双语展示，前端英文模式直接使用
- `pref_matched`：True = 该 POI 的 sub_category 匹配用户的 food_pref/culture_pref；False = 最优近似替代；供 OutputNode 生成履约报告

---

### 4.6 OutputNode（纯代码）

**职责**：补全最终展示字段，生成地图相关数据和摘要。

**每个 POI 新增字段**：

| 字段 | 来源 |
|---|---|
| `transport_to_next` | Haversine 距离估算：≤1.5km→步行，≤5km→骑行，>5km→打车 |
| `transport_polyline` | 高德步行路径 API（`"lng,lat;lng,lat;..."`），供前端 JS 地图绘制蓝线；最后一个 POI 为 null |
| `navigation_url` | 高德 URI Scheme，手机点击跳转导航 App |

**步行路径并行获取**：对每对相邻 POI 同时发出高德步行 API 请求（ThreadPoolExecutor，最多 5 个并发），最坏耗时 = 单段 3s 超时，而非原来的 N×3s。

**静态地图 URL 构造**：高德 REST API，含标记点（A/B/C...）+ 步行路径蓝线（每段限 40 个坐标点，防 URL 超长）。步行 API 失败时降级为仅标记点。

**履约报告（fulfillment_notes）**：基于 EnrichNode 的 `pref_matched` 标记和 intent 字段，生成：
- `satisfied`：哪些需求完全满足
- `unmatched`：哪些没找到及用了什么替代
- `tips`：多轮对话调整建议（如「换一家川菜餐厅」）

---

### 4.7 RefineNode + RefineSelectNode（多轮对话）

**RefineNode（LLM）**：
- 输入：用户话语 + 当前路线摘要（name/category/rating/queue_risk/price）
- 解析：要替换的节点编号、替换类别、新约束（queue_risk 上限、max_price、avoid_sub_category）
- 从现有路线 POI 中提取 `city`/`area`（EnrichNode 已写入这两个字段），推算预算上限
- 写入 `intent["_refine"]` + `intent["must_include_categories"]`（仅含被替换类别），让 POISearchNode 只搜目标类别
- 设置 `locked_nodes`

**RefineSelectNode（纯代码）**：
- 候选池排除**所有当前路线中的 POI**（包括被替换的，避免"替换"回原来那家）
- 按 new_constraints 过滤后选评分最高者
- 若无符合条件的替换，保留原 POI 并提示

---

## 五、LLM 调用层

### DeepSeek 主力 + Claude Fallback

```
call_llm(messages)
  ├─ 尝试 DeepSeek（最多3次，指数退避：1s → 2s → 4s）
  │    ├─ 成功 → 返回结果
  │    └─ RateLimitError / APIError → 重试
  └─ 3次全失败 → 自动切换 Claude Sonnet 4.6
```

**DeepSeek**：成本约为 GPT-4 的 1/20，中文理解强，OpenAI 兼容格式。  
**Claude Fallback**：稳定性高，JSON 格式遵循性好。

### JSON 解析容错

LLM 有时输出 Markdown 代码块（`` ```json ... ``` ``），`_extract_json` 用正则剥离 fence 后再解析，避免整条流水线崩溃。CoT 模式下额外用正则定位 JSON 块，推理文字单独提取。

---

## 六、< 10 秒响应策略

| 手段 | 效果 |
|---|---|
| LLM 通常调用 2 次（IntentNode + RouteNode） | 减少最大延迟来源；RouteNode 自检失败时触发 1 次重试，最坏 3 次 |
| GeoClusterNode 纯代码 | < 1ms |
| POISearchNode SQLite 查询 | < 5ms |
| EnrichNode 纯代码 | < 10ms |
| OutputNode 步行路径（并行） | 所有段同时发出，最坏情况 = 单段超时 3s（原来是 N×3s） |
| SSE 流式推送 | 每完成一个节点立即推进度，用户体感"秒开" |
| 内存缓存（两级） | 1. 原始输入精确匹配；2. IntentNode 后按 city+area+budget_tier+cats+dining_count 检查；命中则 < 1s | 

高德步行路径 API 失败时自动降级为仅标记点地图，不阻塞主流程。

---

## 七、地图方案

### 静态地图（后端生成，当前使用）

调用高德 Web 服务 REST API，返回 PNG 图片 URL：
- 标记点：A/B/C... 字母标注
- 步行路径：蓝色折线（真实路径，非直线），每段限 40 坐标点防 URL 过长
- 使用 **Web 服务 Key**（服务器端 HTTP 调用）

### 动态地图（前端渲染，接入方案已提供）

前端使用高德 JS SDK 2.0，基于后端返回的 `lat/lng` 和 `transport_polyline` 渲染：
- 可缩放/平移
- 点击标记弹出 POI 详情（评分、排队、团购、导航按钮）
- 蓝色步行路径折线
- 使用 **Web 端 JS Key**（浏览器加载 SDK，与 Web 服务 Key 不同，需单独申请并绑定域名）

详见 [前端接入指南](./frontend_guide_for_C.md)。

---

## 八、i18n 模块（route_planner/i18n.py）

统一管理所有用户可见的文本翻译，覆盖三种语言（zh-TW / zh-CN / en）：

| 函数 | 作用 |
|---|---|
| `normalize(lang)` | 规范化语言 tag → `"zh-TW"` / `"zh-CN"` / `"en"` |
| `queue_tip(poi, lang)` | 排队提示文字 |
| `transport_text(km, lang)` | 步行/骑行/打车说明 |
| `time_str(mins, lang)` | 时长格式化（3小时30分 / 3h 30min） |
| `summary(n, mins, budget, deals, lang)` | 行程总结语句 |
| `f(key, lang, **kwargs)` | 履约报告模板（satisfied / unmatched / tips） |
| `step(key, lang, **kwargs)` | SSE 进度消息（覆盖全部 8 个节点） |
| `translate_field(field, value, lang)` | 字段级翻译：category / sub_category / trend_tag / queue_risk / city / area |
| `to_traditional(text)` | 简→繁转换，基于 OpenCC（`s2t` 模式，覆盖所有汉字） |
| `to_simplified(text)` | 繁→简转换，基于 OpenCC（`t2s` 模式），用于 zh-CN 模式字段值展示 |

所有节点通过 `state["language"]` 获取语言设置，不再硬编码中文字符串。

---

## 十、数据层

### POI 数据管理

| 文件 | 用途 | 提交 git |
|---|---|---|
| `route_planner/data/poi.csv` | 数据源，人工维护，GitHub 直接查看表格 | ✅ |
| `route_planner/data/poi.db` | SQLite 运行时数据库，`setup.sh` 从 CSV 自动生成 | ❌ |

**维护流程**：编辑 `poi.csv` → `git push` → Railway 自动重新部署，`poi.db` 自动重建。

### POI 数据覆盖（18,248 条，香港全区）

**数据来源**：餐饮 — OpenRice 香港 2021–2025 年真实用户评论数据集；文化/娱乐/自然 — 政府开放数据 + 官方景点资料（136 条）+ 精选地标景点（37 条）。

| 指标 | 数值 |
|---|---|
| 总 POI 数 | 18,248 条（18,075 家餐厅 + 173 个文化/娱乐/自然景点） |
| 景点类型 | 博物館 18、泳灘 42、郊野公園 25、主要景點 13、公園 18、表演場地/露天劇場 8、歷史建築/宗教古蹟/觀景地標/觀光纜車等 37 等 |
| 覆盖地区 | 旺角 / 中環 / 東區 / 灣仔 / 觀塘 / 荃灣 … 共 209 个社区，area 字段 100% 填充（DeepSeek 地理编码） |
| sub_category 标签数 | 75 个（全中文） |
| 多标签 POI 占比 | 88% 餐厅（如"潮州菜、麵食"，LIKE 可命中任意标签） |
| 评分字段 | taste / decor / service / hygiene / value（5年平均值） |
| 双语字段 | `name` 繁体中文 + `name_en` 英文；`address` + `address_en` |
| i18n 覆盖 | _LOCATION_EN 收录全部 209 个社区，英文模式 area 字段 100% 可翻译 |

**缺失字段的填充策略**（OpenRice 数据集无原始数据）：

| 字段 | 填充方式 |
|---|---|
| `avg_price_per_person` | 拆分 sub_category 所有 tag，取最高价映射值（港式茶餐廳 65、日本料理 200、扒房 500…），130 占比从 69% 降至 8.7% |
| `queue_risk` / `queue_minutes` | review_count 达上限(50) 且 taste ≥ 4.0 → 高；≥ 30 且 ≥ 3.8 → 中；其余 低；minutes 在各档内以 poi_id hash 加变化（高: 20-45min，中: 10-25min，低: 0-10min） |
| `trend_tag` | open_since 2023+ → 新晋；2024+2025 评论 ≥ 2× 前期 → 火爆；其余 经典 |
| `half_year_sales` | (2024 + 2025 评论数) × 200（相对代理值） |
| `recommend_count` | 5年评论总数（真实数据，范围 3-50，代理口碑热度） |
| `has_group_buy` | 全部 false（无数据，待补充） |
| `business_hours` | 空（无数据，待补充） |

**迁移脚本**：`scripts/migrate_hk_to_csv.py` → `poi.csv`（提交 git）→ Railway 启动时 `migrate_to_sqlite.py` → `poi.db`（不提交）。

---

## 十一、FastAPI 接口

```
POST /route/generate   首次生成路线（SSE 流式）
POST /route/refine     局部替换（SSE 流式）
GET  /health           健康检查
```

### 请求格式

`POST /route/generate`：
```json
{
  "user_input": "旺角附近下午，想吃日本料理，預算400港幣",
  "language": "zh-TW",
  "conversation_history": [],
  "locked_nodes": []
}
```

`language` 支持 `"zh-TW"`（繁体中文，默认）、`"zh-CN"`（简体中文）、`"en"`（English）。

`POST /route/refine`（需携带上一次返回的完整路线）：
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
event: step    → {"message": "💡 用户提到本帮菜和文化景点，预算300元..."}
event: step    → {"message": "已解析需求：上海外滩，14:00-21:00（7小时），2人，预算300元，餐饮、文化"}
event: step    → {"message": "找到候选POI：餐饮10个、文化8个"}
event: step    → {"message": "地理聚合完成：中心半径3.0km，时间预算7小时→最多6站"}
event: step    → {"message": "路线生成完成，共4个地点"}
event: step    → {"message": "已补充团购/排队/趋势信息"}
event: step    → {"message": "路线规划完成，已生成地图链接"}
event: result  → {完整路线 JSON}
event: done    → {}
event: error   → {"message": "错误信息"}
```

### result 事件数据结构

```json
{
  "route": [
    {
      "order": 1,
      "name": "鐵板燒海賀",
      "name_en": "Teppanyaki Kaika",
      "category": "Dining",
      "sub_category": "Japanese / Sushi",
      "address": "旺角彌敦道某某號",
      "address_en": "G/F, XXX Nathan Road, Mong Kok",
      "lat": 22.3144, "lng": 114.1724,
      "rating": 4.8,
      "avg_price_per_person": 200,
      "queue_risk": "High",
      "queue_risk_tip": "Peak hours wait ~30 min, arrive early",
      "has_group_buy": false,
      "group_buy": null,
      "stay_minutes": 90,
      "transport_to_next": "Walk ~8 min",
      "transport_polyline": "114.1724,22.3144;...",
      "navigation_url": "https://uri.amap.com/navigation?to=...",
      "trend_tag": "Trending (1.2k+ sold)",
      "pref_matched": true
    }
  ],
  "map_url": "https://restapi.amap.com/v3/staticmap?...",
  "summary": "Planned 3 stops, est. 4h, dining ~HKD 600.",
  "fulfillment_notes": {
    "satisfied": ["Japanese ✓ (鐵板燒海賀)"],
    "unmatched": [],
    "tips": []
  },
  "agent_steps": ["💡 ...", "Parsed: Hong Kong Mong Kok...", "Found 10 dining candidates", "Route ready"]
}
```

> 示例为 `language="en"` 时的输出。`zh-TW` 模式下 category/sub_category/queue_risk/trend_tag/summary 均为繁体中文。

---

## 十二、部署

### 线上环境（Railway）

**地址**：`https://ai-route-planner-production.up.railway.app`

- `git push` 到 main 自动触发重新部署
- 环境变量在 Railway Variables 面板填写，不进代码
- 启动时自动执行 `migrate_to_sqlite.py` 生成 `poi.db`
- 自带 HTTPS，满足 NoCode 前端的 Mixed Content 限制要求

### 本地开发

```bash
bash setup.sh   # 创建 .venv、装依赖、生成 poi.db、复制 .env
# 填入 .env 中的 API Key
PYTHONPATH=. .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 高德 API Key 说明

| Key 类型 | 用途 | 配置位置 |
|---|---|---|
| Web 服务 Key | 静态地图、步行路径规划（服务器 HTTP 调用） | Railway Variables / `.env` |
| Web 端 JS Key | 前端动态交互地图（浏览器加载 SDK） | 前端 HTML，绑定域名 `*.nocode.host` |
