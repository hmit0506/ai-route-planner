# 前端接入指南（成员 C）

后端地址（Railway 线上）：`https://ai-route-planner-production.up.railway.app`

---

## 一、接口概览

| 接口 | 方法 | 说明 |
|---|---|---|
| `/route/generate` | POST | 首次生成路线（SSE 流式） |
| `/route/refine` | POST | 局部替换 POI，如"换一家不排队的餐厅"（SSE 流式） |
| `/health` | GET | 健康检查，返回 `{"status":"ok"}` |

所有接口均返回 **Server-Sent Events (SSE)** 流，需用 `fetch` + `ReadableStream` 读取，不能用 `EventSource`（不支持 POST）。

---

## 二、请求格式

### 2.1 首次生成（POST /route/generate）

```json
{
  "user_input": "中環下午，情侶，預算400，想吃廣東菜，逛文化景點",
  "language": "zh-TW",
  "conversation_history": [],
  "locked_nodes": [],
  "user_id": "user_abc123"
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `user_input` | string | 用户的自然语言输入，中英文均可 |
| `language` | string | `"zh-TW"`（繁体，默认）/ `"zh-CN"`（简体）/ `"en"`（英文） |
| `conversation_history` | array | 多轮对话历史，首次传 `[]` |
| `locked_nodes` | array | 不替换的站点序号，首次传 `[]` |
| `user_id` | string | 可选；传了才有用户偏好记忆 |

### 2.2 局部替换（POST /route/refine）

```json
{
  "user_input": "换一家不排队的餐厅",
  "language": "zh-TW",
  "conversation_history": [],
  "locked_nodes": [],
  "current_route": [ /* 上一次 result 事件中的 route 数组 */ ]
}
```

---

## 三、SSE 事件流结构

每次请求返回多个 SSE 事件，顺序如下：

```
event: step    {"message": "💡 用户想在中環..."}         ← 推理说明（LLM 生成）
event: step    {"message": "已解析需求：中環，7小時..."}  ← 意图解析完成
event: step    {"message": "🌤 天气晴朗（28°C）..."}      ← 天气（有时才有）
event: step    {"message": "找到候選POI：餐飲10個、文化8個"}
event: step    {"message": "地理聚合完成：..."}
event: step    {"message": "✅ 路線自檢通過"}
event: step    {"message": "路線規劃完成，共6個地點"}
event: step    {"message": "已補充團購/排隊/趨勢資訊"}
event: step    {"message": "路線規劃完成，已生成地圖連結"}
event: step    {"message": "⚠️ 未找到 廣東菜..."}         ← 替代说明（有时才有）
event: step    {"message": "💡 可說「換一家廣東菜」"}      ← 调整建议（有时才有）
event: result  { 完整路线 JSON }                          ← 只有这一条是最终结果
event: done    {}                                         ← 流结束标志
```

**读取代码（NoCode 自定义 JS）**：

```javascript
async function generateRoute(userInput, language) {
  const res = await fetch('https://ai-route-planner-production.up.railway.app/route/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      user_input: userInput,
      language: language || 'zh-TW',
      conversation_history: [],
      locked_nodes: []
    })
  });

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let lastEvent = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const lines = buffer.split('\n');
    buffer = lines.pop();

    for (const line of lines) {
      if (line.startsWith('event: ')) {
        lastEvent = line.slice(7).trim();
      } else if (line.startsWith('data: ')) {
        const raw = line.slice(6).trim();
        if (!raw) continue;
        try {
          const data = JSON.parse(raw);
          if (lastEvent === 'step' && data.message) {
            showProgress(data.message);          // 显示进度
          } else if (lastEvent === 'result') {
            showResult(data);                    // 渲染最终结果
          }
        } catch (e) {}
      }
    }
  }
}
```

---

## 四、result 事件完整字段

### 4.1 顶层字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `route` | array | POI 列表（见 4.2） |
| `map_url` | string | 高德静态地图图片 URL，含标记点和步行蓝线，`<img src=map_url>` 直接用 |
| `summary` | string | 一句话总结，如「为你安排了6站行程，预计游玩5小时，餐饮消费约410元」|
| `fulfillment_notes` | object | `{satisfied:[], unmatched:[], tips:[]}` 需求满足报告 |
| `agent_steps` | array | 所有 step 事件的消息合集（与流式推送的内容相同）|
| `weather` | object | 天气信息，城市不支持时为 `{}`（见 4.3）|
| `xiaohongshu_post` | string | LLM 生成的小红书式攻略全文，可直接展示并提供「复制」按钮 |

### 4.2 route 每个 POI 的字段

```json
{
  "order": 1,
  "name": "大館",              // 显示名：en→英文名，zh-CN→简体，zh-TW→繁体
  "name_en": "Tai Kwun",      // 英文名原始值（name_en 为空时 name 保留中文）
  "category": "文化",          // 餐飲/文化/娛樂/自然（已按 language 翻译）
  "sub_category": "歷史建築",  // 细分类别（已按 language 翻译）
  "address": "中環荷里活道10號",
  "address_en": null,
  "city": "香港",
  "area": "中環",
  "lat": 22.278519,
  "lng": 114.159074,           // ← 地图打点用

  "rating": 4.7,
  "taste_rating": 0.0,         // 餐饮类有值，文化/娱乐类为 0
  "decor_rating": 4.5,
  "service_rating": 5.0,
  "hygiene_rating": 5.0,
  "avg_price_per_person": 30.0,
  "half_year_sales": 2707,
  "recommend_count": 637,

  "queue_risk": "高",                              // 高/中/低（已按 language 翻译）
  "queue_risk_tip": "晚高峰等位約30分鐘，建議提前到店",  // 可直接展示

  "has_group_buy": 0,
  "group_buy": null,           // 有团购时: {title, original_price, current_price, discount}
  "trend_tag": "文化深度遊｜高口碑（已售2707单）",
  "business_hours": "08:00-23:00",

  "stay_minutes": 90,
  "transport_to_next": "步行約5分鐘",    // 最后一站为空字符串
  "transport_polyline": "114.15,22.27;...", // 步行路径坐标，JS 地图画蓝线用；最后一站为 null
  "navigation_url": "https://uri.amap.com/navigation?...", // 手机点击跳高德导航 App

  "tags": ["高口碑", "性價比高"],         // 正向标签（已按 language 翻译，见下表）
  "risk_tags": [],                       // 风险标签（已按 language 翻译，见下表）
  "pref_matched": true                   // 该 POI 是否匹配用户偏好
}
```

**tags / risk_tags 翻译对照**：

| zh-TW（数据库） | zh-CN | en |
|---|---|---|
| 高口碑 | 高口碑 | Highly Rated |
| 團購划算 | 团购划算 | Great Deal |
| 性價比高 | 性价比高 | Value for Money |
| 本地人常去 | 本地人常去 | Local Fav |
| 拍照出片 | 拍照出片 | Photo-worthy |
| 低排隊 | 低排队 | Low Queue |
| 冷門寶藏 | 冷门宝藏 | Hidden Gem |
| 適合情侶 | 适合情侣 | Couple-Friendly |
| 親子友好 | 亲子友好 | Family-Friendly |
| 雨天友好 | 雨天友好 | Indoor-Friendly |
| 踩雷風險 | 踩雷风险 | Risky |
| 排隊較高 | 排队较高 | Long Queue |
| 網紅打卡 | 网红打卡 | Instagrammable |

### 4.3 weather 字段

```json
{
  "date": "2026-06-07",
  "weather": "阴",
  "temperature": 28.0,
  "condition": "clear",      // clear / rain / hot / cold / storm
  "prefer_indoor": false,    // true 时路线已调整为室内优先
  "is_rainy": false,
  "is_hot": false,
  "is_cold": false
}
```

天气不支持或获取失败时为 `{}`，前端做空判断即可。

---

## 五、三个真实例子

### 例子 1：zh-TW — 香港中環情侶約會

**请求**：
```json
{
  "user_input": "中環下午，情侶，預算400，想吃廣東菜，逛文化景點",
  "language": "zh-TW"
}
```

**step 事件流**：
```
💡 用户想在中環下午活動，是情侶出行，預算400元，想吃廣東菜，並想逛文化景點。
已解析需求：香港中環，14:00-21:00（7小時），2人，預算400元，餐飲、文化
找到候選POI：餐飲10個、文化8個
地理聚合完成：中心半徑2.0km，時間預算7小時→參考6站
✅ 路線自檢通過
路線規劃完成，共6個地點
已補充團購/排隊/趨勢資訊
路線規劃完成，已生成地圖連結
⚠️ 未找到 廣東菜 餐廳，以 港式、甜品、日本料理替代
💡 此商圈暫無 廣東菜；可說「換一家 廣東菜 餐廳」
```

**result.route（精简）**：
```
1. 大館         文化  ⭐4.7  高  tags:["高口碑","性價比高"]
2. 茶具文物館   文化  ⭐4.6  中  tags:["高口碑","性價比高"]
3. 中環街市     文化  ⭐4.7  中  tags:["高口碑","性價比高"]
4. 小正大福     餐飲  ⭐4.7  中  tags:["高口碑","性價比高","低排隊"]  group_buy:HKD410
5. PMQ元創方    文化  ⭐4.5  中  tags:["高口碑","性價比高"]
6. 希鳥         餐飲  ⭐4.7  低  tags:["高口碑","性價比高","低排隊"]  group_buy:HKD330
```

**result.summary**：
```
為你安排了6站行程，預計遊玩5小時20分鐘，1處有團購優惠，餐飲消費約410元。
```

**result.fulfillment_notes**：
```json
{
  "satisfied": ["文化偏好 歷史建築、文化景點 ✓ （大館）"],
  "unmatched": ["未找到 廣東菜 餐廳，以 港式、甜品、日本料理替代"],
  "tips": ["此商圈暫無 廣東菜；可說「換一家 廣東菜 餐廳」"]
}
```

**result.xiaohongshu_post**（节选）：
```
# 中環情侶約會半天攻略❤️！400元玩5.3小時也太浪漫了吧！

你是不是也覺得中環約會又貴又無聊？錯！這條私藏路線讓你倆用400元預算，
走訪6個高質景點，還能吃到超好味的廣東菜！全程約5.3小時，保證感情升溫🔥

1️⃣ **大館**（4.7分）— 免費入場的歷史建築群，舊監獄改造得超有味道，
拍文青照完全不用濾鏡！📸

...（共约350字）

#中環約會 #香港情侶景點 #中環美食 #大館打卡 #香港免費景點 #情侶旅遊 #香港周末
```

---

### 例子 2：zh-CN — 上海外滩本帮菜

**请求**：
```json
{
  "user_input": "上海外滩周末两人，预算300，吃本帮菜逛文化景点",
  "language": "zh-CN"
}
```

**step 事件流**（含天气）：
```
💡 用户计划周末去上海外滩，两人同行，预算300元，想吃本帮菜并游览文化景点。
已解析需求：上海外滩，14:00-21:00（7小时），2人，预算300元，餐饮、文化
🌤 天气晴朗（28°C），适合户外活动
找到候选POI：餐饮12个、文化12个
📍 本地数据不足，已补充高德地图数据：餐饮, 文化
地理聚合完成：中心半径2.0km，时间预算7小时→参考6站
✅ 路线自检通过
路线生成完成，共5个地点
已补充团购/排队/趋势信息
路线规划完成，已生成地图链接
```

**result.weather**：
```json
{
  "date": "2026-06-07",
  "weather": "阴",
  "temperature": 28.0,
  "condition": "clear",
  "prefer_indoor": false
}
```

**result.route（精简）**：
```
1. 上海市历史博物馆       文化  ⭐4.7  低  tags:["性价比高"]
2. 外滩往事民国上海菜     餐饮  ⭐4.5  低  （高德实时数据）
3. 上海震旦博物馆         文化  ⭐4.6  低  tags:["性价比高"]
4. 外滩家宴·上海菜        餐饮  ⭐4.6  低  （高德实时数据）
5. 上海笔墨博物馆         文化  ⭐3.9  低
```

**result.summary**：
```
为你安排了5站行程，预计游玩5小时45分钟，餐饮消费约181元。
```

> 注意：高德 API 返回的 POI（大陆城市实时搜索）tags 较少，因为没有 OpenRice 评论信号数据。

---

### 例子 3：en — Mong Kok English

**请求**：
```json
{
  "user_input": "Mong Kok afternoon, 2 people, HKD 500, Japanese food and sightseeing",
  "language": "en"
}
```

**step 事件流**：
```
💡 The user wants to spend an afternoon in Mong Kok with 2 people, a budget of HKD 500...
Parsed: Hong Kong Mong Kok | 14:00-21:00 (7h) | 2 pax | budget HKD 500 | Dining / Culture
Found candidates: 10 Dining, 4 Culture
Geo-cluster: radius 2.0km, 7h → up to 6 stops
✅ Route validated
Route ready: 5 stop(s)
Queue / deals / trend info added
Route complete, map generated
⚠️ No Japanese found, substituted with Hong Kong Style / Western (The Artisan)
💡 No Japanese in this area; say 'swap a Japanese restaurant'
```

**result.route（精简）**：
```
1. hana-musubi          Dining   ⭐3.9  High  tags:["Great Deal","Local Fav"]  risk_tags:["Long Queue"]  group_deal:HKD400
2. Ladies' Market       Culture  ⭐4.3  Med   tags:["Value for Money"]
3. Hong Kong Museum of Art  Culture  ⭐4.6  High  tags:["Highly Rated","Value for Money"]
4. Hong Kong Space Museum   Culture  ⭐4.5  High  tags:["Highly Rated","Value for Money"]
5. The Artisan          Dining   ⭐4.6  Low   tags:["Great Deal","Value for Money","Local Fav","Low Queue","Family-Friendly"]  group_deal:HKD355
```

**result.summary**：
```
Planned 5 stops, est. 6h 45min, 2 with group deals, dining ~HKD 755.
```

**result.xiaohongshu_post**（节选）：
```
5.5 hrs, HKD 500 — the perfect Mong Kok date! 💑

What if I told you that an epic afternoon in Mong Kok — with Japanese food,
art museums, and market vibes — costs under HKD 500?

1️⃣ **hana-musubi** ⭐3.9 — Snag the HKD 400 group deal (normally HKD 560!)
for a gorgeous Japanese rice ball platter...
⚠️ Heads up: queues can get long after 7 PM — arrive by 5:30 PM!

...（共约350字，全英文）

#MongKokDateNight #HongKongOnABudget #JapaneseFoodHK #HKMuseumDate
```

---

## 六、动态地图（高德 JS SDK）

在 NoCode 添加**自定义 HTML 块**：

```html
<div id="amap-container" style="width:100%;height:420px;border-radius:12px;overflow:hidden;"></div>

<script>
var _amapReady = false;
var _pendingRoute = null;

function renderRouteMap(route) {
  if (!route || route.length === 0) return;
  if (!_amapReady) { _pendingRoute = route; return; }

  var map = new AMap.Map('amap-container', {
    zoom: 15,
    center: [route[0].lng, route[0].lat],
    mapStyle: 'amap://styles/whitesmoke',
  });

  var labels = 'ABCDEFGH';
  var bounds = new AMap.Bounds();

  route.forEach(function(poi, i) {
    bounds.extend([poi.lng, poi.lat]);

    var marker = new AMap.Marker({ position: [poi.lng, poi.lat], map: map });

    // 团购信息
    var gbHtml = '';
    if (poi.has_group_buy && poi.group_buy) {
      gbHtml = '<div style="color:#e55;font-size:12px;">🎟 团购 ¥'
        + poi.group_buy.current_price + '（' + poi.group_buy.discount + '）</div>';
    }

    // 标签
    var tagsHtml = '';
    if (poi.tags && poi.tags.length) {
      tagsHtml = '<div style="margin:4px 0;">'
        + poi.tags.slice(0,3).map(function(t){
            return '<span style="background:#e8f4ff;color:#1677ff;font-size:11px;padding:1px 6px;border-radius:8px;margin-right:4px;">' + t + '</span>';
          }).join('') + '</div>';
    }

    var infoContent = [
      '<div style="padding:10px;min-width:200px;font-family:sans-serif;">',
      '<b style="font-size:14px;">' + labels[i] + '. ' + poi.name + '</b>',
      '<div style="color:#888;font-size:12px;margin:4px 0;">' + poi.category + ' · ⭐' + poi.rating + '</div>',
      tagsHtml,
      '<div style="font-size:12px;">排队：' + poi.queue_risk + '</div>',
      '<div style="font-size:12px;color:#666;">' + (poi.queue_risk_tip || '') + '</div>',
      gbHtml,
      '<div style="font-size:12px;margin-top:4px;">停留约 ' + (poi.stay_minutes || 60) + ' 分钟</div>',
      poi.transport_to_next ? '<div style="font-size:12px;color:#666;">→ ' + poi.transport_to_next + '</div>' : '',
      '<a href="' + poi.navigation_url + '" target="_blank"',
      ' style="display:inline-block;margin-top:8px;padding:4px 12px;background:#1677ff;',
      'color:#fff;border-radius:6px;font-size:12px;text-decoration:none;">📍 导航</a>',
      '</div>'
    ].join('');

    var infoWindow = new AMap.InfoWindow({ content: infoContent, offset: new AMap.Pixel(0, -30) });
    marker.on('click', function() { infoWindow.open(map, marker.getPosition()); });

    // 步行蓝线
    if (poi.transport_polyline) {
      var pts = poi.transport_polyline.split(';').map(function(p) {
        var parts = p.split(',');
        return [parseFloat(parts[0]), parseFloat(parts[1])];
      });
      new AMap.Polyline({ path: pts, strokeColor: '#0065FF', strokeWeight: 5, strokeOpacity: 0.85, map: map });
    }
  });

  map.setBounds(bounds, false, [80, 80, 80, 80]);
}

function onAmapLoaded() {
  _amapReady = true;
  if (_pendingRoute) { renderRouteMap(_pendingRoute); _pendingRoute = null; }
}
</script>

<script>
  window._AMapSecurityConfig = { securityJsCode: 'YOUR_AMAP_SECURITY_CODE' };
</script>
<script src="https://webapi.amap.com/maps?v=2.0&key=YOUR_AMAP_JS_KEY&callback=onAmapLoaded"></script>
```

---

## 七、建议的页面布局

```
┌─────────────────────────────────────────────────┐
│  🗺  AI 本地路线规划                              │
│  language: [繁體] [简体] [English]               │
├─────────────────────────────────────────────────┤
│  ["中環情侶約會，預算400..."]       [出发 →]      │
├─────────────────────────────────────────────────┤
│  进度：✅ 已解析  ✅ 找到POI  🌤 天气晴朗  ⏳ 规划 │
├─────────────────────────────────────────────────┤
│                                                 │
│         高德动态地图（可缩放，点POI弹详情）        │
│   A●────────B●──────C●──────D●──────E●          │
│                                                 │
├─────────────────────────────────────────────────┤
│  A  大館    ⭐4.7  高  [高口碑][性價比高]  [导航] │
│  B  茶具文物館  ⭐4.6  中  [高口碑]        [导航] │
│  C  中環街市   ⭐4.7  中                   [导航] │
│  D  小正大福   ⭐4.7  中  🎟¥410           [导航] │
│  E  希鳥       ⭐4.7  低  🎟¥330  [低排隊] [导航] │
├─────────────────────────────────────────────────┤
│  ⚠️ 未找到廣東菜，以港式替代                      │
│  💡 可說「換一家廣東菜餐廳」                      │
├─────────────────────────────────────────────────┤
│  📋 小紅書攻略  [复制全文]                        │
│  # 中環情侶約會❤️！400元玩5小時...               │
├─────────────────────────────────────────────────┤
│  💬 继续对话：["换一家不排队的餐厅"]  [发送]      │
└─────────────────────────────────────────────────┘
```

---

## 八、多轮对话（换一家）

```javascript
let currentRoute = [];

// 收到 result 事件时
currentRoute = data.route;

// 用户说"换一家"时
async function refineRoute(userInput) {
  const res = await fetch('https://ai-route-planner-production.up.railway.app/route/refine', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      user_input: userInput,
      language: currentLanguage,
      conversation_history: [],
      locked_nodes: [],
      current_route: currentRoute   // 必须传上一次完整 route 数组
    })
  });
  // 同样用 SSE 读取，最终 result 事件是更新后的完整路线
}
```

---

## 九、注意事项

1. **HTTPS**：NoCode 页面是 HTTPS，后端 Railway 地址也是 HTTPS，可以直接请求
2. **两种高德 Key**：
   - 后端 `.env` 里的 `AMAP_API_KEY` = Web 服务 Key（REST 接口，已由后端使用）
   - 前端 JS SDK 需单独申请 **Web 端 JS Key**，绑定域名 `*.nocode.host`
3. **静态地图备用**：JS SDK 加载失败时，`result.map_url` 是现成的静态图片 URL，`<img src={map_url}>` 即可
4. **SSE 解析**：不能用原生 `EventSource`（不支持 POST），必须用 `fetch + ReadableStream`
5. **xiaohongshu_post** 是纯文本，建议加「复制」按钮；含 `#话题标签` 可高亮显示
6. **weather 为空对象时**：`{}` 表示天气获取失败或城市不支持，前端做 `if (weather && weather.condition)` 判断
