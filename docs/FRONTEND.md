# 前端接入指南


后端地址（Railway 线上）：`https://ai-route-planner-production.up.railway.app`

## 一、SSE 读取（JS 代码）

所有接口均返回 SSE 流，需用 `fetch + ReadableStream`（不能用原生 `EventSource`，不支持 POST）。

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
            showProgress(data.message);               // 进度条追加一行
          } else if (lastEvent === 'result') {
            showResult(data);                         // 渲染路线（此时 xiaohongshu_post 为空，显示 loading）
          } else if (lastEvent === 'xiaohongshu_update') {
            showXiaohongshu(data.xiaohongshu_post);  // 更新小红书区域（约 result 后 11s 到达）
          }
        } catch (e) {}
      }
    }
  }
}
```

## 二、请求字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `user_input` | string | 用户的自然语言输入，中英文均可 |
| `language` | string | `"zh-TW"`（繁体，默认）/ `"zh-CN"`（简体）/ `"en"` |
| `conversation_history` | array | 多轮对话历史，首次传 `[]` |
| `locked_nodes` | array | 不替换的站点序号，首次传 `[]` |
| `user_id` | string | 可选；传了才有用户偏好记忆 |

`/route/refine` 额外需要 `current_route`（上一次 result 事件中的完整 route 数组）。

## 三、result 事件顶层字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `route` | array | POI 列表（见第四节） |
| `map_url` | string | 高德静态地图图片 URL，含标记点和步行蓝线，`<img src=map_url>` 直接用 |
| `summary` | string | 一句话总结，如「为你安排了6站行程，预计游玩5小时，餐饮消费约410元」 |
| `fulfillment_notes` | object | `{satisfied:[], unmatched:[], tips:[]}` 需求满足报告 |
| `agent_steps` | array | 所有 step 事件的消息合集 |
| `weather` | object | 天气信息，城市不支持时为 `{}` |
| `xiaohongshu_post` | string | **`result` 事件中此字段为空**；LLM 生成完成后通过 `xiaohongshu_update` 独立推送 |

**weather 结构**：
```json
{
  "date": "2026-06-07",
  "weather": "阴",
  "temperature": 28.0,
  "condition": "clear",      // clear / rain / hot / cold / storm
  "prefer_indoor": false,
  "is_rainy": false,
  "is_hot": false,
  "is_cold": false
}
```

## 四、route 每个 POI 的完整字段

> 所有文字字段（name / category / sub_category / address / city / area / queue_risk / queue_risk_tip / trend_tag / transport_to_next / tags / risk_tags / scenario_tags / group_buy.discount）均已按 `language` 参数翻译，前端**直接展示**即可。

```json
{
  "order": 1,
  "poi_id": "hk_660400",

  "name": "Tai Kwun",          // en→name_en；zh-CN→简体；zh-TW→繁体原始值
  "name_en": "Tai Kwun",       // 英文名原始值，前端可独立使用

  "category": "Culture",       // 已翻译：zh-TW=文化/餐飲  en=Culture/Dining
  "sub_category": "Historic Site",  // 已翻译：zh-TW=歷史建築  en=Historic Site

  "address": "10 Hollywood Road, Central",  // en→address_en；zh-CN→简体
  "address_en": "10 Hollywood Road, Central",
  "city": "Hong Kong",
  "area": "Central",
  "lat": 22.278519,
  "lng": 114.159074,

  "rating": 4.7,
  "taste_rating": 0.0,         // 口味（餐饮类有值，文化/自然类为 0）
  "decor_rating": 4.5,
  "service_rating": 5.0,
  "hygiene_rating": 5.0,
  "avg_price_per_person": 30.0,
  "half_year_sales": 2707,
  "recommend_count": 637,

  "queue_risk": "High",        // 已翻译
  "queue_risk_tip": "Peak hours wait ~30 min, arrive early",

  "has_group_buy": 1,
  "group_buy": {
    "title": "精選和食雙人宴",
    "original_price": 500.0,
    "current_price": 400.0,
    "discount": "20% off"      // en="20% off"；zh="8.0折"
  },

  "trend_tag": "Classic (400+ sold)",
  "business_hours": "11:30-14:30;18:00-22:30",

  "stay_minutes": 90,
  "transport_to_next": "Walk ~5 min",
  "transport_polyline": "114.15,22.27;114.16,22.28;...",  // JS地图画蓝线；最后一站为 null
  "navigation_url": "https://uri.amap.com/navigation?...", // 手机点击跳高德导航

  "tags": ["Great Deal", "Local Fav"],    // 正向标签，已按 language 翻译
  "risk_tags": ["Long Queue"],            // 风险标签，已按 language 翻译
  "scenario_tags": "Friends;Birthdays",  // 场合标签，已翻译；null 表示无

  "pref_matched": true,   // true=匹配用户偏好；false=近似替代

  // 评论信号（餐厅类有值，景点/文化类为 null）
  "risk_mention_rate": 0.12,
  "queue_mention_rate": 0.33,
  "photo_mention_rate": 0.0,
  "local_mention_rate": 1.0,
  "accessibility_mention_rate": 0.0,
  "year_max": 2025,
  "risk_signal_level": "Low",
  "queue_signal_level": "High",
  "local_authenticity_level": "High",
  "photo_hotness_level": "Low"
}
```

**tags / risk_tags 翻译对照**：

| zh-TW | zh-CN | en |
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

**scenario_tags 翻译对照**：

| zh-TW | zh-CN | en |
|---|---|---|
| 情侶約會 | 情侣约会 | Couples |
| 朋友聚餐 | 朋友聚餐 | Friends |
| 家庭親子 | 家庭亲子 | Families |
| 慶生 | 庆生 | Birthdays |
| 商務接待 | 商务接待 | Business |
| 一人食 | 一人食 | Solo Dining |
| 打卡拍照 | 打卡拍照 | Photo Lovers |

## 五、动态地图（高德 JS SDK）

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

    var gbHtml = poi.has_group_buy && poi.group_buy
      ? '<div style="color:#e55;font-size:12px;">🎟 ' + poi.group_buy.current_price + ' (' + poi.group_buy.discount + ')</div>'
      : '';

    var tagsHtml = poi.tags && poi.tags.length
      ? '<div style="margin:4px 0;">' + poi.tags.slice(0,3).map(function(t){
          return '<span style="background:#e8f4ff;color:#1677ff;font-size:11px;padding:1px 6px;border-radius:8px;margin-right:4px;">' + t + '</span>';
        }).join('') + '</div>'
      : '';

    var infoContent = [
      '<div style="padding:10px;min-width:200px;font-family:sans-serif;">',
      '<b style="font-size:14px;">' + labels[i] + '. ' + poi.name + '</b>',
      '<div style="color:#888;font-size:12px;margin:4px 0;">' + poi.category + ' · ⭐' + poi.rating + '</div>',
      tagsHtml,
      '<div style="font-size:12px;">' + poi.queue_risk_tip + '</div>',
      gbHtml,
      '<div style="font-size:12px;margin-top:4px;">停留约 ' + (poi.stay_minutes || 60) + ' 分钟</div>',
      poi.transport_to_next ? '<div style="font-size:12px;color:#666;">→ ' + poi.transport_to_next + '</div>' : '',
      '<a href="' + poi.navigation_url + '" target="_blank" style="display:inline-block;margin-top:8px;padding:4px 12px;background:#1677ff;color:#fff;border-radius:6px;font-size:12px;text-decoration:none;">📍 导航</a>',
      '</div>'
    ].join('');

    var infoWindow = new AMap.InfoWindow({ content: infoContent, offset: new AMap.Pixel(0, -30) });
    marker.on('click', function() { infoWindow.open(map, marker.getPosition()); });

    if (poi.transport_polyline) {
      var pts = poi.transport_polyline.split(';').map(function(p) {
        var c = p.split(','); return [parseFloat(c[0]), parseFloat(c[1])];
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
<script>window._AMapSecurityConfig = { securityJsCode: 'YOUR_AMAP_SECURITY_CODE' };</script>
<script src="https://webapi.amap.com/maps?v=2.0&key=YOUR_AMAP_JS_KEY&callback=onAmapLoaded"></script>
```

> **Web 端 JS Key** 与后端 `.env` 里的 Web 服务 Key 不同，需在高德控制台单独申请并绑定域名 `*.nocode.host`。

## 六、多轮对话（换一家）

```javascript
let currentRoute = [];

// 收到 result 事件时保存路线
source.addEventListener('result', (e) => {
  currentRoute = JSON.parse(e.data).route;
});

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
  // 同样用 SSE 读取，result 事件是更新后的完整路线，xiaohongshu_update 随后到达
}
```

## 七、建议页面布局

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
│  （result 到达时显示 loading，xiaohongshu_update │
│   到达后替换为完整帖文）                          │
├─────────────────────────────────────────────────┤
│  💬 继续对话：["换一家不排队的餐厅"]  [发送]      │
└─────────────────────────────────────────────────┘
```

## 八、真实运行示例

**例 1：zh-TW — 香港中環情侶約會**

```json
{ "user_input": "中環下午，情侶，預算400，想吃廣東菜，逛文化景點", "language": "zh-TW" }
```

step 流：`💡 用户想在中環下午活動...` → `已解析需求：香港中環，7小時` → `找到候選POI：餐飲10個、文化8個` → `✅ 路線自檢通過` → `路線規劃完成，共6個地點` → `⚠️ 未找到 廣東菜，以 港式、甜品替代`

result.route（精简）：
```
1. 大館         文化 ⭐4.7 高  [高口碑][性價比高]
2. 茶具文物館   文化 ⭐4.6 中  [高口碑]
3. 中環街市     文化 ⭐4.7 中  [高口碑]
4. 小正大福     餐飲 ⭐4.7 中  🎟 HKD 410
5. PMQ元創方    文化 ⭐4.5 中  [高口碑]
6. 希鳥         餐飲 ⭐4.7 低  🎟 HKD 330  [低排隊]
```

---

**例 2：zh-CN — 香港尖沙咀（简体中文）**

```json
{ "user_input": "尖沙咀下午，两人，预算500港币，想吃港式茶餐厅，逛文化景点", "language": "zh-CN" }
```

step 流：`💡 用户想在尖沙咀下午活动...` → `已解析需求：香港尖沙咀，5小时` → `找到候选POI：餐饮12个、文化9个` → `✅ 路线自检通过` → `路线规划完成，共4个地点`

result.route（精简）：
```
1. 香港历史博物馆     文化 ⭐4.7 低  [高口碑][性价比高]
2. 星光大道           文化 ⭐4.5 中  [拍照出片]
3. 翠华餐厅（尖沙咀） 餐饮 ⭐4.3 高  [本地人常去]  🎟 HKD 180
4. 香港艺术馆         文化 ⭐4.6 低  [高口碑]
```

---

**例 3：en — Mong Kok English**

```json
{ "user_input": "Mong Kok afternoon, 2 people, HKD 500, Japanese food and sightseeing", "language": "en" }
```

result.route（精简）：
```
1. hana-musubi          Dining  ⭐3.9 High  [Great Deal][Local Fav]  group_deal HKD 400
2. Ladies' Market       Culture ⭐4.3 Med   [Value for Money]
3. Hong Kong Museum of Art  Culture ⭐4.6 High  [Highly Rated]
4. Hong Kong Space Museum   Culture ⭐4.5 High  [Highly Rated]
5. The Artisan          Dining  ⭐4.6 Low   [Great Deal][Low Queue][Family-Friendly]
```

## 九、注意事项

1. **HTTPS**：NoCode 页面是 HTTPS，Railway 后端也是 HTTPS，可直接请求，无 Mixed Content 问题
2. **两种高德 Key**：后端 `.env` 里的 `AMAP_API_KEY` = Web 服务 Key（REST 接口）；前端 JS SDK 需单独申请 Web 端 JS Key，绑定域名 `*.nocode.host`
3. **静态地图备用**：JS SDK 加载失败时，`result.map_url` 是现成的静态图片 URL，`<img src={map_url}>` 即可
4. **SSE 解析**：不能用原生 `EventSource`（不支持 POST），必须用 `fetch + ReadableStream`
5. **xiaohongshu_post**：`result` 事件中为空字符串；`xiaohongshu_update` 事件到达后更新；`done` 事件后若仍为空则 loading 隐藏
6. **weather 为空对象**：`{}` 表示天气获取失败或城市不支持，做 `if (weather && weather.condition)` 判断
7. **非香港城市 POI**：tags/risk_tags 可能较少，因为没有 OpenRice 评论信号数据
