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
         │ intent: {city, area, duration_hours, budget, food_pref, prefer_local, scenarios, ...}
         ▼
  ┌─────────────┐
  │ WeatherNode │  纯代码：高德天气 API，注入 weather + prefer_indoor 到 intent
  └──────┬──────┘
         │ intent.weather: {condition, temperature, prefer_indoor, ...}
         ▼
  ┌──────────────────┐
  │ POISearchNode    │  纯代码：香港=SQLite优先；其他城市=高德API优先（pref关键词精准搜索）
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
  │ EnrichNode  │  纯代码：poi_id → 完整字段，计算排队提示/团购/趋势/POI标签（三语）
  └──────┬──────┘
         │ route: [{name, rating, queue_risk_tip, group_buy, tags, risk_tags, ...}, ...]
         ▼
  ┌─────────────┐
  │ OutputNode  │  纯代码：步行路径、导航链接、静态地图URL、摘要、小红书导出
  └──────┬──────┘
         ▼
  最终路线 JSON + 静态地图 URL + xiaohongshu_post + weather
```

**首次生成仅 2 次 LLM 调用**（IntentNode + RouteNode），小红书 LLM 在结果发出后异步生成不阻塞主流程，配合 SSE 流式推送保证路线结果约 6s 内到达。

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
    intent: dict                  # IntentNode 写入；WeatherNode 追加 weather 子键；_refine 子键由 RefineNode 写入
    candidates: dict              # POISearchNode 写入，GeoClusterNode 过滤后更新
    route: list                   # RouteNode 写入骨架，Enrich/Output 逐步丰富
    locked_nodes: list            # 多轮对话中用户满意不替换的节点索引（0-based）
    map_url: str                  # OutputNode 写入
    summary: str                  # OutputNode 写入
    fulfillment_notes: dict       # OutputNode 写入：satisfied / unmatched / tips
    conversation_history: list    # 跨轮保留，传入 IntentNode
    stream_updates: list          # 每个节点追加，FastAPI 层实时推 SSE
    user_memory: dict             # app 层从 user_id 加载，空 dict 表示匿名；路线完成后异步更新
    weather: dict                 # WeatherNode 写入：{condition, temperature, prefer_indoor, is_rainy, ...}
    xiaohongshu_post: str         # main.py 异步写入：LLM 生成的小红书式攻略文本（三语，通过 xiaohongshu_update SSE 推送）
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
  "prefer_local": false,
  "scenarios": [],
  "must_include_categories": ["餐饮"]
}
```

`dining_count`：仅当用户明确指定餐饮次数时才 > 0（"包括午饭和晚饭"→2）；菜系偏好不计入次数，输出 0。

`prefer_local`：检测"地道/本地/老字号/authentic"等词，为 true 时 POISearchNode 用 `local_mention_rate DESC` 排序。

`scenarios`：从用户话语提取场合列表（情侶約會/朋友聚餐/家庭親子/慶生/商務接待/一人食/打卡拍照），影响 SQL 排序和 LLM 决策。

---

### 4.2 WeatherNode（纯代码）

**职责**：在 IntentNode 之后，通过高德天气 API 获取用户出行日期/时段的实际天气预报，并将天气信息注入 intent，供 RouteNode 做天气感知路线调整。

**天气 API**：`https://restapi.amap.com/v3/weather/weatherInfo?extensions=all`（未来 4 天预报）。城市名 → adcode 内建映射（香港/上海/北京/广州/深圳等 20 城）。

**日期解析**：将 intent.date 的自然语言描述（"今天"/"明天"/"周末"/"today"/"weekend"）转换为 YYYY-MM-DD，按用户时段选择白天/夜间天气。

**天气分类**：

| condition | 触发条件 | prefer_indoor |
|---|---|---|
| `storm` | 包含"暴"/"台風" | true |
| `rain` | 包含"雨"的任意描述 | true |
| `hot` | 白天气温 ≥ 33°C | true |
| `cold` | 气温 ≤ 10°C | false |
| `clear` | 其余 | false |

**注入 intent**：
- `intent["weather"]`：完整天气信息字典（date/weather/temperature/condition/prefer_indoor/is_rainy/is_hot/is_cold）
- `intent["prefer_indoor"]`：布尔值，RouteNode system prompt 中有对应的天气感知规则（condition=rain/storm 时减少户外 POI，condition=hot 时优先商场/咖啡厅/室内文化）

**SSE 输出**：三语天气步骤消息（🌧雨天/☀️高温/🌤晴朗/🧥寒冷/⛈恶劣），告知用户路线已做天气调整。

---

### 4.3 POISearchNode（纯代码）

**职责**：根据 intent 召回 POI 候选，香港优先使用本地高质量数据，其他城市使用实时搜索。

**双路召回策略**：

| 城市类型 | 主路径 | 备路径 |
|---|---|---|
| 香港（`_is_hk_city`返回 True） | SQLite 本地库（18,075条丰富香港数据） | 高德 API 兜底（<3条时触发） |
| 其他城市 | 高德 Place Search API（实时数据） | SQLite（高德失败时） |

**高德搜索增强（`_amap_search`）**：
- `pref_keywords` 参数：将 `food_pref`/`culture_pref` 直接作为搜索关键词（如 `["日本料理","壽司"]`），比宽泛的"餐厅|美食"精准得多
- 合并搜索：先用 `"日本料理|壽司"` 一次 OR 查询；若结果 < 3 条且关键词多于 1 个，再逐关键词单独搜索并去重合并
- `citylimit=true`：结果严格限制在目标城市内

**数据源**：`poi.csv`（提交到 git，人工维护）→ `setup.sh` 迁移为 `poi.db`（不提交 git）。

**类别名规范化（`_normalize_cat`）**：`must_include_categories` 中的值可能来自英文模式路线的翻译结果（如 `"Dining"`、`"Culture"`）或繁体（`"餐飲"`），统一规范化为数据库内部的简体值（`"餐饮"`、`"文化"`、`"娱乐"`）再查询，避免 refine 时出现 0 候选。

**SQLite 召回策略（香港 / 高德失败时）**：
0. **信号驱动预排序**（ORDER BY 最高优先级，基于真实评论数据）：
   - `risk_mention_rate ASC`：始终生效，低负面评论优先
   - `year_max DESC`：始终生效，近年仍有评论的（可能仍营业）优先
   - `local_mention_rate DESC`：仅当 `prefer_local=true` 时追加
   - `photo_mention_rate DESC`：仅当 `scenarios` 含"打卡拍照"时追加
   - `accessibility_mention_rate DESC`：仅当 `scenarios` 含"家庭親子"时追加
   - `scenario_tags LIKE` 匹配顺序：仅当 `scenarios` 非空时追加
1. `city LIKE ?` + `area LIKE ?` 模糊匹配
2. `avg_price_per_person <= budget_per_person × 1.2`（20% 弹性，避免过度截断）
3. `avoid` 中的子类别通过 `sub_category NOT IN (...)` 过滤排除
4. 偏好排序：餐饮类用 `food_pref`（全部项），文化/娱乐类用 `culture_pref`，匹配 `sub_category` 的 POI 优先
5. 按 `rating DESC`，每类取 Top-10
6. Fallback：若命中 < 3 个，退化为仅 city 过滤（放宽商圈限制）
7. **营业时间过滤**：按 `intent.time_range` 对 `business_hours` 字段做时间段重叠检查（解析 `HH:MM-HH:MM` 格式），过滤后不足 3 条则 soft fallback 保留原始结果
8. **已访问 POI 过滤**：从 `user_memory.visited_poi_ids` 中排除已去过的 POI，避免重复推荐
9. **高德 Place Search 兜底**：两种触发条件：① 上述过滤后仍 < 3 条；② 城市级 fallback 后结果中无一条的 `area` 匹配目标区域（`area_mismatch`，说明本地库对该区域无覆盖，如偏远小岛），此时以 Amap 结果替代错误区域的 DB 结果。`AMAP_API_KEY` 未设置时跳过。

---

### 4.4 GeoClusterNode（纯代码）

**职责**：地理聚合过滤 + 根据时间预算计算推荐站点数。

**逻辑**：
1. **锚点选取**：优先从 `area_coords.py`（90+ 香港商圈坐标对照表）查找 `intent.area` 的真实中心坐标作为锚点；查不到时退化为候选 POI 的几何质心（lat/lng 均值）
2. 过滤掉距锚点 > 2km 的 POI（原来用质心导致"自洽偏移"，改为真实区域中心后过滤才真正有效）；若某类剩余 < 3 个则回退保留原始候选
3. 计算 `max_pois = max(3, min(8, floor(duration_hours × 60 / 65)))`
4. 将 `max_pois` 写入 intent，传递给 RouteNode 作为参考

**为什么之前锚点有问题**：旧版用候选自身的质心——候选本来就分散时，质心落在两者中间，大部分 POI 都在 3km 内，过滤形同虚设。改为区域中心坐标后，"中環"的候选只保留真正在中環附近的 POI。

**注意**：GeoClusterNode 只做地理和时间的约束，**不做类别配比**。餐饮数量和文化/娱乐数量由 RouteAgent 根据 `meal_plan` 自主决定，避免硬性规则覆盖用户的真实意图。

---

### 4.5 RouteNode（LLM）

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
| `risk_mention_rate` | 0~1，负面短语占比，均值0.6；低于0.4优秀，高于0.8有踩雷风险 |
| `queue_mention_rate` | 0~1，排队抱怨占比，均值0.3；高于0.5意味明显排队问题 |
| `local_mention_rate` | 0~1，地道/本土感短语占比，均值0.39；prefer_local时应优先高值 |
| `photo_mention_rate` | 0~1，打卡/拍照短语占比，均值0.23；打卡场景应优先高值 |
| `accessibility_mention_rate` | 0~1，无障碍/可达性短语占比，均值0.24；家庭親子场景适当偏好高值 |
| `year_max` | 最近评论年份（2021-2025）；<=2022 的 POI 可能已关/口碑下滑，降低权重 |
| `scenario_tags` | 场合标签（如"情侶約會;朋友聚餐"），与用户 scenarios 匹配时加分 |
| `risk_signal_level` / `queue_signal_level` | 三等分位标签（Low/Medium/High），辅助确认 float 相对位置 |

**餐饮数量决策**：
- 若 `dining_count > 0` → 餐饮站点数量必须恰好等于 `dining_count`
- 若 `dining_count == 0` → 合理安排即可，保证行程有非餐饮类站点

**自我检查机制**：LLM 输出后，代码验证：① 站点数 ≥ 3；② 餐饮数量匹配 dining_count；③ 非全餐饮。若不通过：
1. 生成精确的纠正 prompt（dining_count 违反时会列出所有餐饮候选 POI ID，要求精确选 N 个）
2. 携带纠正说明触发一次重试，SSE stream 显示 `⚠️ 自检发现问题`
3. 重试后若 dining_count 仍不符，**代码强制截断**（`_force_dining_count`）：保留评分最高的 N 个餐饮站，移除多余的，确保最终结果与约束一致

**用户记忆注入**：若 `user_memory` 非空，`build_route_hint()` 生成简短软约束提示附加到 user message（历史菜系偏好、历史忌口补充、历史人均消费参考）；不强制覆盖当前 intent，仅在 intent 未指定时起作用。

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

### 4.6 EnrichNode（纯代码）

**职责**：将 RouteNode 输出的 POI ID 骨架映射回完整字段，计算展示用派生字段，并按 `language` 翻译字段值。

| 派生字段 | 计算逻辑 |
|---|---|
| `queue_risk_tip` | 按语言输出：高→"晚高峰等位约N分钟"／"Peak hours wait ~N min"；支持三种语言 |
| `group_buy.discount` | `current_price / original_price × 10`，格式"6.8折" |
| `trend_tag` | 销量 ≥ 1万→"火爆（已售1.2万单）"；英文模式→"Trending (1.2k+ sold)"；自定义多标签→"Family-Friendly · Accessible (1273+ sold)" |
| `tags` | 正向标签列表（见下表），按 language 翻译后输出 |
| `risk_tags` | 风险标签列表（见下表），按 language 翻译后输出 |

**POI 标签体系**：

| 标签（zh-TW 规范形）| zh-CN | en | 触发条件 |
|---|---|---|---|
| 高口碑 | 高口碑 | Highly Rated | rating ≥ 4.5 且 review_count > 200 |
| 團購划算 | 团购划算 | Great Deal | has_group_buy 且折扣 ≥ 20% |
| 性價比高 | 性价比高 | Value for Money | value_rating ≥ 4.5 或 rating ≥ 4.3 且人均 < 80 |
| 本地人常去 | 本地人常去 | Local Fav | local_authenticity_level=High 或 local_mention_rate ≥ 0.55 |
| 拍照出片 | 拍照出片 | Photo-worthy | photo_hotness_level=High 或 photo_mention_rate ≥ 0.35 |
| 低排隊 | 低排队 | Low Queue | queue_signal_level=Low 或 queue_mention_rate ≤ 0.12 |
| 冷門寶藏 | 冷门宝藏 | Hidden Gem | half_year_sales < 800 且 rating ≥ 4.3 且 review_count > 30 |
| 適合情侶 | 适合情侣 | Couple-Friendly | scenario_tags 含"情侶約會" |
| 親子友好 | 亲子友好 | Family-Friendly | scenario_tags 含"家庭親子" |
| 雨天友好 | 雨天友好 | Indoor-Friendly | weather.prefer_indoor=True 且 POI 为餐饮/室内场馆 |
| 踩雷風險 | 踩雷风险 | Risky | risk_signal_level=High 或 risk_mention_rate ≥ 0.75 |
| 排隊較高 | 排队较高 | Long Queue | queue_signal_level=High 或 queue_mention_rate ≥ 0.45 |
| 網紅打卡 | 网红打卡 | Instagrammable | half_year_sales ≥ 5000 且 year_max ≥ 2024 |

**字段级翻译**（`language="en"` 时）：
- `sub_category`："日本料理、壽司" → "Japanese / Sushi"（餐饮 75 词 + 文化类 20+ 词，如 博物館→Museum、歷史建築→Historic Site、文化→Culture）
- `category`："餐饮" → "Dining"
- `queue_risk`："高" → "High"
- `trend_tag`：标准词("火爆")→"Trending"；自定义多标签("亲子友好｜交通便利")→逐标签翻译

多轮对话时，已丰富的 locked POI 直接透传，不重复查找。

EnrichNode 对所有文字字段做语言本地化，前端直接展示无需再转换：

| 字段 | zh-TW | zh-CN | en |
|---|---|---|---|
| `name` | 繁体原始值 | `to_simplified(name)` | `name_en`（空时回退繁体） |
| `category` | 餐飲/文化/… | 餐饮/文化/… | Dining/Culture/… |
| `sub_category` | 日本料理/歷史建築/… | `to_simplified` | Japanese/Historic Site/… |
| `address` | 繁体原始 | `to_simplified` | `address_en`（空时回退繁体） |
| `city` / `area` | 繁体原始 | `to_simplified` | Hong Kong / Central / … |
| `queue_risk` | 高/中/低 | 高/中/低 | High/Medium/Low |
| `queue_risk_tip` | 繁体 | 简体 | English |
| `trend_tag` | 繁体+销量 | 简体+销量 | English+sold count |
| `group_buy.discount` | 8.0折 | 8.0折 | 20% off |
| `group_buy.title` | 繁体商家名 | `to_simplified` | 保留原文（专有名词） |
| `tags` / `risk_tags` | 繁体标签 | 简体标签 | English tags |
| `scenario_tags` | 情侶約會;朋友聚餐 | 情侣约会;朋友聚餐 | Couples;Friends |
| `transport_to_next` | 步行約N分鐘 | 步行约N分钟 | Walk ~N min |

- `name_en` / `address_en`：双语字段，始终保留英文原始值，前端可独立使用
- `pref_matched`：True = 该 POI 的 sub_category 匹配用户的 food_pref/culture_pref；False = 最优近似替代；供 OutputNode 生成履约报告
- 11 个评论信号字段（`risk_mention_rate` 等）：香港餐厅有值，景点/文化类 POI 为 null（无 OpenRice 数据）

---

### 4.7 OutputNode（纯代码）

**职责**：补全最终展示字段，生成地图相关数据和摘要。

**每个 POI 新增字段**：

| 字段 | 来源 |
|---|---|
| `transport_to_next` | Haversine 距离估算：≤1.5km→步行，≤5km→骑行，>5km→打车 |
| `transport_polyline` | 高德步行路径 API（`"lng,lat;lng,lat;..."`），供前端 JS 地图绘制蓝线；最后一个 POI 为 null |
| `navigation_url` | 高德 URI Scheme，手机点击跳转导航 App |

**步行路径并行获取**：对每对相邻 POI 同时发出高德步行 API 请求（ThreadPoolExecutor，最多 5 个并发），最坏耗时 = 单段 3s 超时，而非原来的 N×3s。

**小红书式攻略导出**：OutputNode 本身不生成小红书，`xiaohongshu_post` 初始为空字符串。路线 `result` 事件发送后，`app/main.py` 通过 `loop.run_in_executor` 异步调用 `_llm_xiaohongshu`，完成后通过 `xiaohongshu_update` SSE 独立推送，不阻塞路线结果到达时间。`_llm_xiaohongshu` 生成 200-350 字博主风格贴文（三语独立 prompt）；`_build_xiaohongshu` 模板作为异常兜底。
- **三语独立 prompt 策略**：每种语言有各自的 `lang_inst`（语言强制要求）+ `struct_hint`（精细格式要求）+ `user_msg`（语言匹配的数据标签）
- **en 格式要求**：① 含数字的吸睛标题 → ② 开场 hook → ③ 每站编号（1️⃣2️⃣3️⃣）+ 具体菜品 + 个人感受 + 价格/团购 → ④ 💡Tips（预算/时间/⚠️警告）→ ⑤ 5-8 个英文 hashtag
- **语言纯洁性保障**（LLM 自由文本不可信语言指令，必须在输出端强制转换）：
  - zh-CN：OpenCC `to_simplified()` 无条件后处理（港式内容 LLM 必然输出繁体）
  - en：计算输出 CJK 字符比例；>15% 则发起重试 prompt（"rewrite entirely in English"）；重试后仍 >10% 则回退 template

**静态地图 URL 构造**：高德 REST API，含标记点（A/B/C...）+ 步行路径蓝线（每段限 40 个坐标点，防 URL 超长）。步行 API 失败时降级为仅标记点。

**履约报告（fulfillment_notes）**：基于 EnrichNode 的 `pref_matched` 标记和 intent 字段，生成：
- `satisfied`：哪些需求完全满足
- `unmatched`：哪些没找到及用了什么替代
- `tips`：多轮对话调整建议（如「换一家川菜餐厅」）

**pref 语言一致性**：`food_pref`/`culture_pref`/`avoid` 值在嵌入 fulfillment 模板前先调用 `translate_field("sub_category", val, lang)` 翻译，确保消息语言与 `language` 字段一致（避免 zh-CN 模式输出繁体「火鍋」、英文模式输出中文类别名「藝術館」）。

**POI 名字本地化（`_name()`）**：fulfillment 消息中嵌入的 POI 名字通过 `_name(poi)` 输出——zh-CN 模式调用 `to_simplified()` 将繁体专有名词转简体（如「香港歷史博物館」→「香港历史博物馆」），其他语言保持原始名字不变（英文界面保留中文专有名词，属预期行为）。

**dining_excess 分支**：`dining_count` 约束在两个方向均有专属消息——实际餐饮站点少于预期触发 `dining_mismatch`（"不足"），多于预期触发 `dining_excess`（"过多"），提示方向相反，均覆盖三语。

---

### 4.8 RefineNode + RefineSelectNode（多轮对话）

**RefineNode（LLM）**：
- 输入：用户话语 + 当前路线摘要（name/category/rating/queue_risk/price）
- 解析：要替换的节点编号、替换类别、新约束（queue_risk 上限、max_price、avoid_sub_category、**prefer_sub_category**）
- `prefer_sub_category`：用户指定的菜系/类型（如"日本料理"、"博物館"），写入 `intent["food_pref"]`（餐饮）或 `intent["culture_pref"]`（文化），使 POISearchNode 对偏好类型优先排序
- 从现有路线 POI 中提取 `city`/`area`（EnrichNode 已写入这两个字段），推算预算上限
- 写入 `intent["_refine"]` + `intent["must_include_categories"]`（仅含被替换类别），让 POISearchNode 只搜目标类别
- 设置 `locked_nodes`

**RefineSelectNode（纯代码）**：
- 候选池排除**所有当前路线中的 POI**（包括被替换的，避免"替换"回原来那家）
- `replace_category` 同样经 `_normalize_cat` 规范化（英文模式路线的 `"Dining"` → `"餐饮"`），确保与候选字典的 key 一致
- 按 new_constraints 过滤（queue_risk / max_price / avoid_sub_category）
- 若 `prefer_sub_category` 非空，偏好菜系 POI 优先排在前列（匹配→非匹配，再按 rating 降序）
- 若无符合条件的替换，保留原 POI 并提示（三语 SSE 消息）

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
| 小红书异步生成 | OutputNode 不再阻塞等 LLM 小红书；路线结果约 6s 先发；小红书约 +11s 后通过 xiaohongshu_update 推送 |
| SSE 逐条刷新 | 每次 yield 后加 `await asyncio.sleep(0)`，强制 uvicorn 在下一个同步阻塞前刷新 TCP 缓冲区，事件逐条到达，不再批量堆积 |
| 首条事件即时推送 | 请求进入立即发出 `planning_start` 事件（< 50ms），消除初始空白等待 |
| 内存缓存（两级） | 1. 原始输入精确匹配（含 language）；2. IntentNode 后按 language+city+area+budget_tier+cats+dining_count 检查；命中则 < 1s；两级命中均同步更新 user_memory | 

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

详见 [前端接入指南](./FRONTEND.md)。

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
| `f(key, lang, **kwargs)` | 履约报告模板（dining_ok / dining_mismatch / dining_excess / food_ok / food_miss / culture_ok / culture_miss / avoid_violated 等，含双向 dining 判断） |
| `step(key, lang, **kwargs)` | SSE 进度消息（覆盖全部节点，含 WeatherNode 5 条天气消息） |
| `weather_step(weather_info, lang)` | 天气步骤消息（🌧雨天/☀️高温/🌤晴朗/🧥寒冷/⛈恶劣），三语 |
| `translate_field(field, value, lang)` | 字段级翻译：category / sub_category / trend_tag / queue_risk / city / area；trend_tag 支持自定义多标签分解翻译 |
| `translate_tag(tag, lang)` | 单个 POI 标签翻译（繁体规范形 → zh-CN 简体 / en 英文） |
| `translate_tags(tags, lang)` | 批量翻译标签列表 |
| `translate_scenario_tags(raw, lang)` | 翻译分号分隔的 scenario_tags 字符串（"情侶約會;朋友聚餐" → en "Couples;Friends"） |
| `to_traditional(text)` | 简→繁转换，基于 OpenCC（`s2t` 模式，覆盖所有汉字） |
| `to_simplified(text)` | 繁→简转换，基于 OpenCC（`t2s` 模式），用于 zh-CN 模式字段值展示 |

所有节点通过 `state["language"]` 获取语言设置，不再硬编码中文字符串。

---

## 九、用户记忆系统（route_planner/user_memory.py）

每个用户（由 `user_id` 标识）在 `route_planner/data/users/{user_id}.json` 维护一份记忆文件：

```json
{
  "food_pref": ["日本料理", "壽司"],
  "avoid": ["辣"],
  "budget_history": [200, 250, 180],
  "visited_poi_ids": ["poi_0023", "poi_1147"]
}
```

| 字段 | 更新时机 | 使用方式 |
|---|---|---|
| `food_pref` | 每次生成后追加 intent.food_pref | RouteNode 软约束提示（intent 无偏好时参考） |
| `avoid` | 每次生成后追加 intent.avoid | RouteNode 软约束提示（补充当前 intent 未涵盖的忌口） |
| `budget_history` | 每次生成后记录 budget_per_person | RouteNode 提示历史人均（仅供参考） |
| `visited_poi_ids` | 每次生成后追加路线 POI id | POISearchNode 从候选中直接过滤排除 |

**隐私说明**：记忆文件存于服务器本地文件系统，Railway 重新部署后清空（ephemeral filesystem）。不传 `user_id` 则完全匿名，无任何记忆写入。

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
| `queue_risk` / `queue_minutes` | 餐饮：review_count ≥ 50 且 taste ≥ 4.0 → 高；≥ 30 且 ≥ 3.8 → 中；其余 低；minutes 在各档内以 poi_id hash 加变化。文化/自然类：公園、泳灘、郊野公园、海濱花園、觀景地標 → 固定为 低（无排队系统）；創意街區、露天劇場 → 中；其余按评论量计算。共修正 103 条景点 POI。|
| `trend_tag` | open_since 2023+ → 新晋；2024+2025 评论 ≥ 2× 前期 → 火爆；其余 经典 |
| `half_year_sales` | (2024 + 2025 评论数) × 200（相对代理值） |
| `recommend_count` | 5年评论总数（真实数据，范围 3-50，代理口碑热度） |
| `has_group_buy` | 按 `avg_price_per_person` 档位概率（≥200元→55%，≥100元→45%，≥60元→35%，≥30元→20%，其余5%）+ poi_id hash；8,512 家（47%）有团购；`group_buy_title` 按 sub_category 生成对应套餐名，`group_buy_original_price` = avg×2，折扣 0.65–0.84 |
| `business_hours` | 按 sub_category 分四类生成：all_day（港式/快餐/咖啡，08:00-22:00 变体）、full（粤菜/火锅，11:00-23:00 变体）、split（日本料理/西餐，午市+晚市）、evening（居酒屋/酒吧，17:30-23:30）、brunch（早午餐，08:00-15:00）；hash 变化 ±0-60min |

**迁移流程**：`scripts/migrate_hk_to_csv.py` → `poi.csv`（提交 git）→ 服务启动时 `app/main.py` lifespan `_ensure_db()` → `poi.db`（不提交）。`poi.db` 仅在 CSV 比 DB 新时重建。

### 评论信号系统

`poi.csv` 中 18,075 家餐厅额外携带 11 个来自真实评论分析的信号字段，来源为 `POI_profile_extra_keywords.csv`（由 `POI_profile_data_dictionary.docx` 记录生成逻辑）：

| 字段 | 来源字段 | 说明 |
|---|---|---|
| `queue_signal_level` | `Queue_Phrases_MentionRate` 三等分位 | Low/Medium/High |
| `risk_signal_level` | `Risk_Phrases_MentionRate` 三等分位 | Low/Medium/High；High=较多负面评论 |
| `photo_hotness_level` | `Photo_Checkin_Phrases_MentionRate` 三等分位 | Low/Medium/High |
| `local_authenticity_level` | `Local_Authenticity_Phrases_MentionRate` 三等分位 | Low/Medium/High |
| `scenario_tags` | `Scenario_Phrases_Top20` 关键词提取 | "情侶約會;朋友聚餐;家庭親子;慶生;商務接待;一人食" |
| `queue_risk` | `queue_signal_level` 映射 | 覆盖原 hash mock 值；Low→低、Medium→中、High→高 |
| `risk_mention_rate` | `Risk_Phrases_MentionRate` 原始值 | 0~1 float，均值0.6 |
| `queue_mention_rate` | `Queue_Phrases_MentionRate` 原始值 | 0~1 float，均值0.3 |
| `photo_mention_rate` | `Photo_Checkin_Phrases_MentionRate` 原始值 | 0~1 float，均值0.23 |
| `local_mention_rate` | `Local_Authenticity_Phrases_MentionRate` 原始值 | 0~1 float，均值0.39 |
| `accessibility_mention_rate` | `Accessibility_Phrases_MentionRate` 原始值 | 0~1 float，均值0.24 |
| `year_max` | `year_max` 直接复制 | 最近一次收到评论的年份（2021-2025）；11,197 家为2025 |

**三等分位说明**：Low/Medium/High 是全量 23,541 家餐厅按 MentionRate 值均分三组，代表相对排名而非绝对质量。float 原始值比 level 标签更精确，两者同时使用。

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
event: step    → {"message": "正在為您規劃路線，請稍候..."}           ← 请求进入立即（< 50ms）
event: step    → {"message": "💡 用户提到本帮菜和文化景点，预算300元..."}
event: step    → {"message": "已解析需求：香港中環，14:00-21:00（7小时），2人，预算400港幣，餐饮、文化"}
event: step    → {"message": "🌤 天气晴朗（22°C），适合户外活动"}
event: step    → {"message": "找到候选POI：餐饮10个、文化8个"}
event: step    → {"message": "地理聚合完成：中心半径3.0km，时间预算7小时→最多6站"}
event: step    → {"message": "路线生成完成，共4个地点"}
event: step    → {"message": "已补充团购/排队/趋势信息"}
event: step    → {"message": "路线规划完成，已生成地图链接"}
event: result  → {完整路线 JSON，xiaohongshu_post 为空}               ← 路线结果先发（约 6s）
event: step    → {"message": "正在生成小红书攻略贴文..."}
event: xiaohongshu_update → {"xiaohongshu_post": "📍 中環一日遊..."}  ← LLM 小红书后发（约 +11s）
event: step    → {"message": "小红书攻略贴文已生成"}
event: done    → {}
event: error   → {"message": "错误信息"}
```

> 每条 `step` 事件单独刷新（`asyncio.sleep(0)` 保障），不再批量堆积。`xiaohongshu_update` 为独立事件类型，前端用 `source.addEventListener('xiaohongshu_update', fn)` 接收。

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
- 启动时 `app/main.py` lifespan 自动执行 `_ensure_db()` 生成 `poi.db`（CSV 比 DB 新时重建）
- 自带 HTTPS，满足 NoCode 前端的 Mixed Content 限制要求

### 本地开发

```bash
bash setup.sh   # 创建 .venv、装依赖、复制 .env（poi.db 首次启动自动生成）
# 填入 .env 中的 API Key
PYTHONPATH=. .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 高德 API Key 说明

| Key 类型 | 用途 | 配置位置 |
|---|---|---|
| Web 服务 Key | 静态地图、步行路径规划（服务器 HTTP 调用） | Railway Variables / `.env` |
| Web 端 JS Key | 前端动态交互地图（浏览器加载 SDK） | 前端 HTML，绑定域名 `*.nocode.host` |

---

