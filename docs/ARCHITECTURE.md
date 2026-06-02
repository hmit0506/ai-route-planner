# 系统架构设计文档

---

## 一、整体设计理念

### 核心问题

路线规划本质上是一个**多约束组合优化问题**：在 N 个候选 POI 中，找出满足预算、时间、偏好、地理距离、排队风险等约束的最优子集，并按合理顺序串联。

纯规则/搜索算法能处理硬约束（预算上限、营业时间），但无法处理软约束（"本帮菜口味"、"文艺气息"、"避开人太多的地方"）。纯 LLM 能理解软约束，但上下文长度有限、延迟高、成本贵，不适合处理大量 POI 数据的枚举。

**设计决策：混合架构——LLM 做理解和决策，纯代码做数据处理。**

---

## 二、整体架构

### 流水线总览

```
用户自然语言输入
        │
        ▼
  ┌─────────────┐
  │ IntentAgent │  LLM调用①：自然语言 → 结构化意图 JSON
  └──────┬──────┘
         │ intent: {city, area, budget, food_pref, ...}
         ▼
  ┌──────────────────┐
  │ POISearchAgent   │  纯代码：从 POI 数据库按条件召回候选
  └────────┬─────────┘
           │ candidates: {"餐饮": [...10个], "文化": [...8个]}
           ▼
  ┌─────────────┐
  │  RouteAgent │  LLM调用②：从候选中选出最优 3-5 站
  └──────┬──────┘
         │ route: [{poi_id, order, stay_minutes}, ...]
         ▼
  ┌─────────────┐
  │ EnrichAgent │  纯代码：补全团购/排队/趋势字段
  └──────┬──────┘
         │ route: [{name, rating, queue_risk_tip, group_buy, ...}, ...]
         ▼
  ┌─────────────┐
  │ OutputAgent │  纯代码：计算交通时间、生成地图 URL、写摘要
  └──────┬──────┘
         │
         ▼
  最终路线 JSON + 高德静态地图 URL
```

**LLM 仅调用 2 次**，其余节点为纯 Python，整体响应 < 10 秒（配合 SSE 流式推送体感更快）。

---

## 三、LangGraph StateGraph 机制

### 为什么用 LangGraph

LangGraph 将 Agent 流水线建模为**有向图（DAG）**，每个节点是一个状态变换函数：

```
state_new = node(state_old)
```

相比直接写串行函数调用，LangGraph 的优势：
- **状态统一管理**：所有节点共享同一个 `RouteState`，无需手动传参
- **条件路由**：后续支持"用户满意则结束，不满意则进 RefineAgent"等分支逻辑
- **可观测性**：每个节点的输入输出自动可追踪，便于调试
- **局部重跑**：多轮对话时只需从 `RefineAgent` 开始，不重跑整条流水线

### RouteState 状态流转

```python
class RouteState(TypedDict):
    user_input: str               # 不变，原始输入
    intent: dict                  # IntentAgent 写入
    candidates: dict              # POISearchAgent 写入
    route: list                   # RouteAgent 写入初版，Enrich/Output 逐步丰富
    locked_nodes: list            # 多轮对话用：用户满意不替换的节点索引
    map_url: str                  # OutputAgent 写入
    summary: str                  # OutputAgent 写入
    conversation_history: list    # 跨轮保留
    stream_updates: list          # 每个节点追加一条，用于 SSE 推送
```

每个节点只负责写自己关心的字段，其余字段透传（`{**state, "xxx": new_value}`）。`stream_updates` 是一个追加列表，FastAPI 层可以实时 diff 并推送新增条目。

---

## 四、各节点设计详解

### 4.1 IntentAgent

**职责**：将自由格式自然语言映射为固定 Schema 的结构化 JSON。

**为什么用 LLM**：用户输入极度多样——"两个人，想吃辣的，不超过200"、"带娃逛上海，全家出行"、"就外滩附近随便逛逛"——规则解析无法覆盖长尾表达。

**Prompt 策略**：
- System prompt 给出完整 JSON Schema，包含字段类型和默认值规则
- 要求 LLM 直接输出 JSON，不输出任何解释文字
- 对未提及字段给出明确 fallback（未指定人数默认2人，未指定时间默认14:00-21:00）

**输出示例**：
```json
{
  "city": "上海", "area": "外滩",
  "budget_total": 300, "budget_per_person": 150, "party_size": 2,
  "food_pref": ["本帮菜"],
  "must_include_categories": ["餐饮", "文化"],
  "time_range": {"start": "14:00", "end": "21:00"}
}
```

---

### 4.2 POISearchAgent

**职责**：纯代码，从 POI 数据库召回每个类别的 Top-10 候选。

**为什么不用 LLM**：POI 搜索是纯数据过滤排序，无歧义，用 LLM 既慢又贵。

**召回策略（按优先级）**：

1. **地理过滤**：city 精确匹配 + area 模糊子串匹配（`"外滩" in poi["area"]`）
2. **Fallback**：若命中 < 5 个，退化为仅过滤 city，扩大范围
3. **偏好提升**：餐饮类中，`food_pref` 匹配 `sub_category` 的 POI 排在前面
4. **预算软过滤**：`avg_price_per_person <= budget_per_person × 1.2`（允许 20% 弹性，避免过度截断）
5. **按评分降序**，每类取 Top-10 传给 RouteAgent

**设计取舍**：软预算而非硬截断，是因为高质量低客单价 POI 可能很少，硬截断会让 LLM 无法选出好路线。

---

### 4.3 RouteAgent

**职责**：LLM 在候选集中做多约束最优选择，返回 POI ID 列表 + 停留时间。

**为什么用 LLM**：这一步需要综合判断：
- 地理相邻性（LLM 可通过 lat/lng 估算，也能用常识推断"豫园和南京路很近"）
- 软约束匹配（"文艺气息"对应 M50 而非城隍庙）
- 时间窗口合理性（博物馆关门时间是否来得及）
- 排队避峰（高排队风险的餐厅建议午市而非晚高峰）

**Prompt 策略**：
- 传入 compact 版候选（省略不必要字段，节省 token）
- 明确约束：≥1 餐饮 + ≥1 文化/娱乐；地理相邻；总价格不超预算
- 要求输出只含 `poi_id / order / stay_minutes`，不输出解释

**输出示例**：
```json
[
  {"poi_id": "poi_005", "order": 1, "stay_minutes": 30},
  {"poi_id": "poi_013", "order": 2, "stay_minutes": 90},
  {"poi_id": "poi_084", "order": 3, "stay_minutes": 90},
  {"poi_id": "poi_045", "order": 4, "stay_minutes": 45}
]
```

---

### 4.4 EnrichAgent

**职责**：纯代码，将 RouteAgent 输出的 POI ID 映射回完整 POI 数据，并计算展示字段。

**计算逻辑**：

| 字段 | 计算方式 |
|---|---|
| `queue_risk_tip` | 高风险 → "晚高峰等位约N分钟，建议提前到店"；中 → "高峰期约N分钟"；低 → "基本无需等位" |
| `group_buy.discount` | `current_price / original_price × 10`，保留1位小数，格式"6.8折" |
| `trend_tag` | 销量 ≥ 1万 → "火爆（已售1.2万单）"；否则拼接实际数字 |

---

### 4.5 OutputAgent

**职责**：纯代码，补全最终展示字段，生成地图 URL 和文字摘要。

**交通时间估算**（Haversine 球面距离）：

```
距离 ≤ 1.5 km  → 步行约N分钟（15分钟/km）
距离 ≤ 5.0 km  → 骑行/打车约N分钟（4分钟/km）
距离 > 5.0 km  → 打车约N分钟（3分钟/km）
```

**高德静态地图 URL 构造**：

```
https://restapi.amap.com/v3/staticmap
  ?location={中心点经纬度}    ← 所有 POI 的经纬度均值
  &zoom=14
  &size=750*400
  &markers=mid,,A:{lng1},{lat1}|mid,,B:{lng2},{lat2}|...
  &key={AMAP_API_KEY}
```

---

## 五、LLM 调用层设计

### DeepSeek + Claude Fallback

```
call_llm(messages)
  │
  ├─ 尝试 DeepSeek（最多3次，指数退避：1s → 2s → 4s）
  │    ├─ 成功 → 返回结果
  │    └─ RateLimitError / APIError → 下一次重试
  │
  └─ 3次全失败 → 自动切换 Claude Sonnet 4.6
```

**选择 DeepSeek 作为主力**的原因：成本低（约为 GPT-4 的 1/20）、中文理解强、OpenAI 兼容格式便于切换。

**选择 Claude 作为 Fallback**的原因：稳定性高、JSON 输出格式遵循性好，作为最后保障。

### JSON 解析容错

LLM 有时会输出 Markdown 代码块（` ```json ... ``` `），`_extract_json` 函数用正则剥离 fence 后再解析，避免因格式问题导致整条流水线崩溃。

---

## 六、< 10 秒响应策略

| 手段 | 效果 |
|---|---|
| LLM 调用仅 2 次 | 减少最大延迟来源 |
| POI 搜索纯代码 | < 5ms |
| SSE 流式推送 | 每完成一个节点立即推送进度，用户体感"秒开" |
| 路线缓存（待实现） | 相同城市+商圈+预算区间命中缓存 < 1 秒 |
| 餐饮/文化 POI 并行搜索（可选） | IntentAgent 完成后两类并发搜索 |

---

## 七、多轮对话与局部替换

### 问题

用户说"把第二个换掉"时，不应重新跑整条流水线（慢且浪费），而应只替换指定节点。

### 设计方案

```
用户："换一家餐厅，不要排队的"
        │
        ▼
  ┌─────────────┐
  │ RefineAgent │  LLM调用①：理解替换意图，确定要替换的节点索引
  └──────┬──────┘
         │ locked_nodes = [1, 3]（其余节点保持不变）
         ▼
  POISearchAgent（只搜索被替换类别）
         ▼
  RouteAgent（在 locked_nodes 约束下重选）
         ▼
  EnrichAgent → OutputAgent
```

`locked_nodes` 字段记录用户满意不替换的 POI 索引，传入 RouteAgent 后在 Prompt 中明确告知"以下节点已锁定，只替换其余位置"。

---

## 八、数据层设计

### Mock POI 数据库（100条）

覆盖上海主要商圈（外滩、南京路、新天地、淮海路、静安、陆家嘴、徐汇等），五大类别：

| 类别 | 数量 | 子类举例 |
|---|---|---|
| 餐饮 | ~50 | 本帮菜、江浙菜、火锅、粤菜、日料、咖啡、下午茶 |
| 文化 | ~30 | 博物馆、历史建筑、创意街区、寺庙、书店、纪念馆 |
| 娱乐 | ~10 | 游船、主题乐园、水族馆、剧院 |
| 自然 | ~5 | 城市公园、滨江景观 |
| 购物 | ~5 | 商业街、艺术商场 |

每条 POI 包含 25+ 字段，涵盖评分、客单价、排队数据、团购信息、销量趋势等，为 RouteAgent 提供足够的决策信息。
