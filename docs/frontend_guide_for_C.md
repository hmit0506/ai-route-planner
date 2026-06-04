# 前端接入指南（成员 C）

本文档说明如何在 NoCode（nocode.host）前端对接后端 API，包括 SSE 流式接收、动态地图渲染、POI 卡片展示和多轮对话。

---

## 一、准备工作

### 1.1 后端地址

本地开发时，后端运行在 `http://localhost:8000`。  
但 NoCode 页面是 HTTPS，直接请求 HTTP 后端会被浏览器拦截（Mixed Content）。

**解决方案**：
- 开发阶段：成员 A 用 ngrok 将本地后端暴露为 HTTPS，会给你一个类似 `https://xxxx.ngrok-free.app` 的地址
- 正式演示：部署到 Railway，得到固定 HTTPS 地址

把后端地址存为一个变量，方便切换：

```javascript
const API_BASE = 'https://xxxx.ngrok-free.app';  // 替换为实际地址
```

### 1.2 高德 JS API Key

动态地图需要一个**Web 端 JS Key**（与后端用的 REST Key 不同）。

申请步骤：
1. 登录 [高德开放平台](https://lbs.amap.com/) → 控制台 → 应用管理 → 创建新应用
2. 添加 Key → 服务平台选 **Web端(JS API)**
3. 允许域名填 `*.nocode.host`（或你的部署域名）
4. 把申请到的 Key 填入下方代码的 `YOUR_AMAP_JS_KEY`

---

## 二、调用后端 API

### 2.1 首次生成路线

```javascript
async function generateRoute(userInput) {
  const response = await fetch(`${API_BASE}/route/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      user_input: userInput,
      conversation_history: [],
      locked_nodes: []
    })
  });

  // SSE 流式读取
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // 按行解析 SSE 事件
    const lines = buffer.split('\n');
    buffer = lines.pop(); // 保留未完成的行

    for (const line of lines) {
      if (line.startsWith('event: step')) continue; // 跳过，下一行是 data
      if (line.startsWith('data: ')) {
        const raw = line.slice(6).trim();
        if (!raw) continue;
        try {
          const parsed = JSON.parse(raw);

          if (parsed.message) {
            // 进度消息：更新加载状态文字
            showProgress(parsed.message);
          } else if (parsed.route) {
            // 最终结果
            showResult(parsed);
          }
        } catch (e) {}
      }
    }
  }
}
```

### 2.2 局部替换（换一家）

```javascript
async function refineRoute(userInput, currentRoute) {
  const response = await fetch(`${API_BASE}/route/refine`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      user_input: userInput,          // 如："换一家不排队的餐厅"
      conversation_history: [],
      locked_nodes: [],
      current_route: currentRoute     // 上一次返回的 route 数组
    })
  });

  // SSE 读取方式同上，最终返回更新后的完整路线
}
```

---

## 三、后端返回的数据结构

`result` 事件中的完整数据：

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
  "summary": "为你安排了3站行程，预计游玩4小时，2处有团购优惠，餐饮消费约258元。",
  "agent_steps": ["已解析需求：...", "找到候选POI：...", "路线生成完成"]
}
```

**关键字段说明**：

| 字段 | 说明 |
|---|---|
| `transport_polyline` | 从该 POI 到下一个 POI 的步行路径坐标串，格式 `"lng,lat;lng,lat;..."`，最后一个 POI 为 null |
| `navigation_url` | 高德导航链接，手机上点击直接跳转高德 App 导航 |
| `map_url` | 后端生成的静态地图图片 URL（含步行蓝线），可作为备用 |

---

## 四、动态地图（高德 JS SDK）

在 NoCode 中添加**自定义 HTML 块**，粘贴以下完整代码：

```html
<div id="amap-container" style="width:100%;height:420px;border-radius:12px;overflow:hidden;"></div>

<script>
var _amapReady = false;
var _pendingRoute = null;

function renderRouteMap(route) {
  if (!route || route.length === 0) return;

  if (!_amapReady) {
    _pendingRoute = route;
    return;
  }

  var map = new AMap.Map('amap-container', {
    zoom: 15,
    center: [route[0].lng, route[0].lat],
    mapStyle: 'amap://styles/whitesmoke',
  });

  var labels = ['A', 'B', 'C', 'D', 'E'];
  var bounds = new AMap.Bounds();

  route.forEach(function(poi, i) {
    bounds.extend([poi.lng, poi.lat]);

    // 标记点
    var marker = new AMap.Marker({
      position: [poi.lng, poi.lat],
      map: map,
    });

    // 弹窗内容
    var groupBuyHtml = '';
    if (poi.has_group_buy && poi.group_buy) {
      groupBuyHtml = '<div style="color:#e55;font-size:12px;">🎟 团购 ¥' 
        + poi.group_buy.current_price + '（' + poi.group_buy.discount + '）</div>';
    }

    var infoContent = [
      '<div style="padding:10px;min-width:200px;font-family:sans-serif;">',
      '<b style="font-size:14px;">' + (labels[i] || (i+1)) + '. ' + poi.name + '</b>',
      '<div style="color:#888;font-size:12px;margin:4px 0;">' + poi.category + ' · ⭐' + poi.rating + '</div>',
      '<div style="font-size:12px;">排队：' + poi.queue_risk + (poi.queue_risk_tip ? '（' + poi.queue_risk_tip + '）' : '') + '</div>',
      groupBuyHtml,
      '<div style="font-size:12px;margin-top:4px;">停留约 ' + (poi.stay_minutes || 60) + ' 分钟</div>',
      poi.transport_to_next ? '<div style="font-size:12px;color:#666;">→ ' + poi.transport_to_next + '</div>' : '',
      '<a href="' + poi.navigation_url + '" target="_blank" ',
      'style="display:inline-block;margin-top:8px;padding:4px 12px;background:#1677ff;',
      'color:#fff;border-radius:6px;font-size:12px;text-decoration:none;">📍 导航到这里</a>',
      '</div>'
    ].join('');

    var infoWindow = new AMap.InfoWindow({
      content: infoContent,
      offset: new AMap.Pixel(0, -30),
    });
    marker.on('click', function() {
      infoWindow.open(map, marker.getPosition());
    });

    // 步行路径蓝线
    if (poi.transport_polyline) {
      var pts = poi.transport_polyline.split(';').map(function(p) {
        var parts = p.split(',');
        return [parseFloat(parts[0]), parseFloat(parts[1])];
      });
      new AMap.Polyline({
        path: pts,
        strokeColor: '#0065FF',
        strokeWeight: 5,
        strokeOpacity: 0.85,
        map: map,
      });
    }
  });

  // 自动缩放到所有 POI 的范围
  map.setBounds(bounds, false, [80, 80, 80, 80]);
}

function onAmapLoaded() {
  _amapReady = true;
  if (_pendingRoute) {
    renderRouteMap(_pendingRoute);
    _pendingRoute = null;
  }
}
</script>

<!-- 替换 YOUR_AMAP_JS_KEY -->
<script src="https://webapi.amap.com/maps?v=2.0&key=YOUR_AMAP_JS_KEY&callback=onAmapLoaded"></script>
```

---

## 五、建议的页面布局

```
┌─────────────────────────────────────────────┐
│  🗺  AI 本地路线规划                          │
├─────────────────────────────────────────────┤
│  输入框：["帮我规划..."]  [出发 →]            │
├─────────────────────────────────────────────┤
│  加载进度：✅ 已解析需求  ✅ 找到候选POI  ⏳ 规划中 │
├─────────────────────────────────────────────┤
│                                             │
│           高德动态地图（可缩放）              │
│   A●───────────────B●──────────C●          │
│                                             │
├─────────────────────────────────────────────┤
│  A  外婆家  ⭐4.8  排队:高  🎟¥258    [导航] │
│  B  豫园    ⭐4.5  排队:中  🎟¥35     [导航] │
│  C  M50    ⭐4.6  排队:低              [导航] │
├─────────────────────────────────────────────┤
│  💬 继续对话：["换一家餐厅"]  [发送]          │
└─────────────────────────────────────────────┘
```

---

## 六、多轮对话实现要点

每次收到 `result` 事件后，把 `route` 数组存起来：

```javascript
let currentRoute = [];

// 收到结果时
currentRoute = data.route;
renderRouteMap(currentRoute);
showPOICards(currentRoute);

// 用户点"换一家"时
refineRoute(userInput, currentRoute);
```

---

## 七、注意事项

1. **HTTPS 限制**：NoCode 页面是 HTTPS，后端必须也是 HTTPS，否则请求被浏览器拦截
2. **两种高德 Key 不同**：后端 `.env` 里的 `AMAP_API_KEY` 是 Web 服务 Key（REST 接口用），前端 JS SDK 需要单独申请 Web 端 JS Key
3. **静态地图备用**：如果 JS SDK 加载失败，可用后端返回的 `map_url` 显示静态地图图片（`<img src={map_url} />`）
4. **`navigation_url` 在移动端有效**：点击后跳转高德 App 导航，桌面浏览器会打开高德网页版
