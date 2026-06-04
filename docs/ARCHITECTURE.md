# 系统架构设计文档

---

## 一、整体设计理念

### 核心问题

路线规划本质上是一个**多约束组合优化问题**：在 N 个候选 POI 中，找出满足预算、时间、偏好、地理距离、排队风险等约束的最优子集，并按合理顺序串联。

纯规则/搜索算法能处理硬约束（预算上限、营业时间），但无法处理软约束（"本帮菜口味"、"文艺气息"、"避开人太多的地方"）。纯 LLM 能理解软约束，但上下文长度有限、延迟高、成本贵，不适合处理大量 POI 数据的枚举。

**设计决策：混合架构——LLM 做理解和决策，纯代码做数据处理。**

---

## 二、整体架构

### 首次生成流水线

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
  │ POISearchAgent   │  纯代码：从 SQLite (poi.db) 按条件召回候选
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
  │ OutputAgent │  纯代码：步行路径、导航链接、地图 URL、摘要
  └──────┬──────┘
         │
         ▼
  最终路线 JSON（含 transport_polyline）+ 高德静态地图 URL
```

**LLM 仅调用 2 次**，其余节点为纯 Python，整体响应 < 10 秒（配合 SSE 流式推送体感更快）。

### 局部替换流水线（多轮对话）

```
用户："换一家不排队的餐厅"
        │
        ▼
  ┌────────────┐
  │ RefineNode │  LLM调用①：解析替换意图，确定要替换的节点和新约束
  └──────┬─────┘
         │ intent._refine: {replace_order, category, new_constraints}
         │ locked_nodes: [其余节点索引]
         ▼
  ┌──────────────────┐
  │ POISearchNode    │  纯代码：只搜被替换类别的候选
  └────────┬─────────┘
           ▼
  ┌──────────────────────┐
  │ RefineSelectNode     │  纯代码：按新约束选最优替换 POI，合并回原路线
  └──────────┬───────────┘
             ▼
  EnrichNode → OutputNode
```

**局部替换仅 1 次 LLM 调用**，其余全为纯代码。

---

## 三、LangGraph StateGraph 机制

### 为什么用 LangGraph

LangGraph 将 Agent 流水线建模为**有向图（DAG）**，每个节点是一个状态变换函数：

```
state_new = node(state_old)
```

相比直接写串行函数调用，LangGraph 的优势：
- **状态统一管理**：所有节点共享同一个 `RouteState`，无需手动传参
- **条件路由**：支持"用户满意则结束，不满意则进 RefineAgent"等分支逻辑
- **可观测性**：每个节点的输入输出自动可追踪，便于调试
- **局部重跑**：多轮对话时只需从 `RefineNode` 开始，不重跑整条流水线

### RouteState 状态流转

```python
class RouteState(TypedDict):
    user_input: str               # 不变，原始输入
    intent: dict                  # IntentAgent 写入；_refine 子键由 RefineNode 写入
    candidates: dict              # POISearchAgent 写入
    route: list                   # RouteAgent 写入初版，Enrich/Output 逐步丰富
    locked_nodes: list            # 多轮对话用：用户满意不替换的节点索引
    map_url: str                  # OutputAgent 写入（高德静态地图 URL）
    summary: str                  # OutputAgent 写入
    conversation_history: list    # 跨轮保留
    stream_updates: list          # 每个节点追加一条，用于 SSE 推送
```

每个节点只负责写自己关心的字段，其余字段透传（`{**state, "xxx": new_value}`）。

---

## 四、各节点设计详解

### 4.1 IntentAgent

**职责**：将自由格式自然语言映射为固定 Schema 的结构化 JSON。

**为什么用 LLM**：用户输入极度多样——"两个人，想吃辣的，不超过200"、"带娃逛上海，全家出行"——规则解析无法覆盖长尾表达。

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

**职责**：纯代码，从 SQLite 数据库（`route_planner/data/poi.db`）召回每个类别的 Top-10 候选。

**数据源**：`poi.csv`（提交到 git，GitHub 可直接查看）→ 启动时由 `setup.sh` 迁移为 `poi.db`（不提交 git）。成员 B 维护数据只需编辑 CSV，重新运行迁移脚本即可。

**召回策略（按优先级）**：

1. **地理过滤**：city 精确匹配 + area 模糊子串匹配
2. **Fallback**：若命中 < 3 个，退化为仅过滤 city，扩大范围
3. **偏好提升**：餐饮类中，`food_pref` 匹配 `sub_category` 的 POI 排在前面
4. **预算软过滤**：`avg_price_per_person <= budget_per_person × 1.2`
5. **按评分降序**，每类取 Top-10 传给 RouteAgent

---

### 4.3 RouteAgent

**职责**：LLM 在候选集中做多约束最优选择，返回 POI ID 列表 + 停留时间。

**为什么用 LLM**：需要综合判断地理相邻性、软约束匹配、时间窗口合理性、排队避峰。

**Prompt 策略**：
- 传入 compact 版候选（省略不必要字段，节省 token）
- 明确约束：≥1 餐饮 + ≥1 文化/娱乐；地理相邻；总价格不超预算
- 要求输出只含 `poi_id / order / stay_minutes`

**输出示例**：
```json
[
  {"poi_id": "poi_005", "order": 1, "stay_minutes": 30},
  {"poi_id": "poi_013", "order": 2, "stay_minutes": 90},
  {"poi_id": "poi_084", "order": 3, "stay_minutes": 90}
]
```

---

### 4.4 EnrichAgent

**职责**：纯代码，将 RouteAgent 输出的 POI ID 映射回完整 POI 数据，并计算展示字段。

**计算逻辑**：

| 字段 | 计算方式 |
|---|---|
| `queue_risk_tip` | 高风险 → "晚高峰等位约N分钟，建议提前到店"；中 → "高峰期约N分钟"；低 → "基本无需等位" |
| `group_buy.discount` | `current_price / original_price × 10`，格式"6.8折" |
| `trend_tag` | 销量 ≥ 1万 → "火爆（已售1.2万单）"；否则拼接实际数字 |

---

### 4.5 OutputAgent

**职责**：纯代码，补全最终展示字段，生成地图相关数据和文字摘要。

**新增字段（每个 POI）**：

| 字段 | 来源 |
|---|---|
| `transport_to_next` | Haversine 距离估算（≤1.5km步行，≤5km骑行，>5km打车） |
| `transport_polyline` | 高德步行路径规划 API，格式 `"lng,lat;lng,lat;..."`，供前端 JS 地图绘制蓝线；最后一个 POI 为 null |
| `navigation_url` | 高德导航 URI Scheme，手机点击直接跳转导航 App |

**地图 URL 构造**：调用高德静态地图 API，同时包含：
- `markers=`：每个 POI 的标记点（A/B/C...）
- `paths=`：步行路径蓝线（weight:4;color:0x0065FF）
- 若步行 API 超时/失败，优雅降级为仅标记点

---

### 4.6 RefineNode + RefineSelectNode

**职责**：处理多轮对话中的局部替换请求（"换一家"、"换掉第二个"）。

**RefineNode（LLM）**：
- 输入：用户话语 + 当前路线 JSON
- 解析出：要替换的节点编号、替换类别、新约束（如 `queue_risk != "高"`）
- 写入 `intent["_refine"]`，设置 `locked_nodes`

**RefineSelectNode（纯代码）**：
- 从 POISearchNode 召回的候选中，按新约束过滤
- 选评分最高的替换 POI
- 将替换结果合并回原路线，locked_nodes 对应位置保持不变

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

**DeepSeek**：成本低（约为 GPT-4 的 1/20）、中文理解强、OpenAI 兼容格式。

**Claude Fallback**：稳定性高、JSON 格式遵循性好，作为最后保障。

### JSON 解析容错

LLM 有时输出 Markdown 代码块（`` ```json ... ``` ``），`_extract_json` 函数用正则剥离 fence 后再解析，避免整条流水线崩溃。

---

## 六、< 10 秒响应策略

| 手段 | 效果 |
|---|---|
| LLM 调用仅 2 次 | 减少最大延迟来源 |
| POI 搜索纯代码（SQLite） | < 5ms |
| SSE 流式推送 | 每完成一个节点立即推进度，用户体感"秒开" |
| 路线缓存（已实现） | 相同城市+商圈+预算区间命中缓存 < 1 秒 |

---

## 七、地图方案

### 静态地图（后端生成）

调用高德静态地图 REST API，返回 PNG 图片 URL：
- 标记点：每个 POI 用字母（A/B/C）标注
- 步行路径：蓝色折线（真实路径，非直线）
- 使用 **Web 服务 Key**（服务器端 HTTP 调用）

### 动态地图（前端渲染）

前端使用高德 JS SDK 2.0，基于后端返回的坐标数据渲染交互式地图：
- 可缩放/平移
- 点击 POI 弹窗（评分、团购、导航按钮）
- 步行路径蓝线（使用 `transport_polyline` 字段）
- 使用 **Web 端 JS API Key**（浏览器端 SDK 加载，与 Web 服务 Key 不同，需单独申请）

详见 [前端接入指南](./frontend_guide_for_C.md)。

---

## 八、数据层设计

### POI 数据管理

| 文件 | 用途 | 是否提交 git |
|---|---|---|
| `route_planner/data/poi.csv` | 数据源，人工维护，GitHub 可直接查看 | ✅ 是 |
| `route_planner/data/poi.db` | SQLite 运行时数据库，由 `setup.sh` 自动从 CSV 生成 | ❌ 否 |

**维护流程**：编辑 `poi.csv` → 提交 git → 队友 `git pull` 后重新运行 `bash setup.sh` 即可。

### POI 字段说明（25+ 字段）

覆盖上海主要商圈（外滩、南京路、新天地、淮海路、静安、陆家嘴、徐汇等），五大类别：

| 类别 | 数量 | 子类举例 |
|---|---|---|
| 餐饮 | ~50 | 本帮菜、江浙菜、火锅、粤菜、日料、咖啡、下午茶 |
| 文化 | ~30 | 博物馆、历史建筑、创意街区、寺庙、书店、纪念馆 |
| 娱乐 | ~10 | 游船、主题乐园、水族馆、剧院 |
| 自然 | ~5 | 城市公园、滨江景观 |
| 购物 | ~5 | 商业街、艺术商场 |

每条 POI 包含评分、客单价、排队数据、团购信息、销量趋势等，为 RouteAgent 提供足够的决策信息。

---

## 九、FastAPI 接口

### 接口列表

```
POST /route/generate   首次生成路线（SSE 流式）
POST /route/refine     局部替换（SSE 流式）
GET  /health           健康检查
```

### SSE 事件格式

```
event: step    → {"message": "已解析需求：上海外滩，预算300元"}
event: step    → {"message": "找到候选POI：餐饮10个、文化8个"}
event: step    → {"message": "路线生成完成，共3个地点"}
event: result  → {完整路线 JSON，见下方}
event: done    → {}
event: error   → {"message": "错误信息"}
```

### result 事件完整结构

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
      "navigation_url": "https://uri.amap.com/navigation?to=121.4491,31.2245,外婆家&mode=walk&coordinate=gaode&callnative=1",
      "trend_tag": "火爆（已售1.2万单）"
    }
  ],
  "map_url": "https://restapi.amap.com/v3/staticmap?...",
  "summary": "为你安排了3站行程，预计游玩4小时，1处有团购优惠，餐饮消费约258元。",
  "agent_steps": ["已解析需求：...", "找到候选POI：...", "路线生成完成"]
}
```

**新增字段说明**（相比原始 POI 数据）：

| 字段 | 来源 | 说明 |
|---|---|---|
| `transport_polyline` | 高德步行路径 API | `"lng,lat;lng,lat;..."` 格式，前端 JS 地图绘制蓝线用；最后一个 POI 为 null |
| `navigation_url` | OutputAgent 生成 | 高德 URI Scheme，手机点击跳转导航 App |
| `queue_risk_tip` | EnrichAgent 生成 | 人性化的排队提示文字 |
| `group_buy.discount` | EnrichAgent 计算 | 折扣率，如"6.8折" |
| `trend_tag` | EnrichAgent 生成 | 含销量数字，如"火爆（已售1.2万单）" |

---

## 十、部署

### 当前线上环境

**Railway**（已上线）：`https://ai-route-planner-production.up.railway.app`

- push 到 main 分支自动触发重新部署
- API Key 在 Railway 控制台 Variables 面板填写，不进代码
- 启动时自动执行 `migrate_to_sqlite.py` 生成 `poi.db`

### 本地开发

```bash
bash setup.sh
PYTHONPATH=. .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### HTTPS 要求

NoCode 前端页面托管在 HTTPS 域名下，后端必须也是 HTTPS，否则浏览器拒绝混合内容请求（Mixed Content）。Railway 自带 HTTPS，本地开发可用 ngrok 临时暴露。

### 高德 API Key 说明

| Key 类型 | 用途 | 配置位置 |
|---|---|---|
| Web 服务 Key | 静态地图图片、步行路径规划（服务器 HTTP 调用） | Railway Variables / 本地 `.env` |
| Web 端 JS Key | 前端动态交互地图（浏览器加载 SDK） | 前端 HTML 代码中，绑定域名 `*.nocode.host` |
