# 前端动态地图接入指南（成员 C）

## 准备工作

1. 在[高德开放平台](https://lbs.amap.com/)申请一个 **Web端(JS API)** 类型的 Key（和后端用的 REST API Key 不同，需要单独申请）
2. 把 Key 填入下方代码的 `YOUR_AMAP_JS_KEY` 处

---

## 后端返回的数据结构

调用 `POST /route/generate` 后，SSE 流的 `result` 事件中 `route` 数组每个元素包含：

```json
{
  "order": 1,
  "name": "外婆家（南京西路店）",
  "category": "餐饮",
  "lat": 31.2245,
  "lng": 121.4491,
  "transport_to_next": "步行约8分钟",
  "transport_polyline": "121.4491,31.2245;121.4510,31.2250;...",
  "navigation_url": "https://uri.amap.com/navigation?to=...",
  "rating": 4.8,
  "queue_risk": "高",
  "has_group_buy": true,
  "group_buy": { "title": "双人套餐", "current_price": 258 }
}
```

- `transport_polyline`：从当前 POI 到下一个 POI 的步行路径，格式为 `"lng,lat;lng,lat;..."`，最后一个 POI 为 `null`
- `navigation_url`：点击直接跳转高德导航 App

---

## 地图嵌入代码

在 NoCode 中添加一个**自定义 HTML 块**，粘贴以下代码：

```html
<!-- 高德地图容器 -->
<div id="amap-container" style="width:100%;height:400px;border-radius:12px;overflow:hidden;"></div>

<script>
// 接收后端返回的 route 数组后调用此函数
function renderRouteMap(route) {
  if (!route || route.length === 0) return;

  // 初始化地图，以第一个 POI 为中心
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
      label: {
        content: '<div style="background:#FF5722;color:#fff;padding:2px 7px;border-radius:4px;font-size:12px;">'
                 + (labels[i] || (i+1)) + ' ' + poi.name + '</div>',
        direction: 'top',
      },
      map: map,
    });

    // 点击弹窗
    var infoContent = '<div style="padding:8px;min-width:200px;">'
      + '<b>' + poi.name + '</b><br>'
      + '评分：' + poi.rating + ' ⭐<br>'
      + '排队风险：' + poi.queue_risk + '<br>'
      + (poi.has_group_buy ? '🎟 团购：¥' + poi.group_buy.current_price + '<br>' : '')
      + '<a href="' + poi.navigation_url + '" target="_blank" '
      + 'style="color:#1677ff;text-decoration:none;">📍 导航到这里</a>'
      + '</div>';

    var infoWindow = new AMap.InfoWindow({ content: infoContent, offset: new AMap.Pixel(0, -30) });
    marker.on('click', function() { infoWindow.open(map, marker.getPosition()); });

    // 步行路径折线
    if (poi.transport_polyline) {
      var pts = poi.transport_polyline.split(';').map(function(p) {
        var parts = p.split(',');
        return [parseFloat(parts[0]), parseFloat(parts[1])];
      });
      new AMap.Polyline({
        path: pts,
        strokeColor: '#0065FF',
        strokeWeight: 4,
        strokeOpacity: 0.8,
        map: map,
      });
    }
  });

  // 自动缩放到所有 POI 范围
  map.setBounds(bounds, false, [60, 60, 60, 60]);
}
</script>

<!-- 加载高德 JS API（替换 YOUR_AMAP_JS_KEY） -->
<script src="https://webapi.amap.com/maps?v=2.0&key=YOUR_AMAP_JS_KEY&callback=onAmapLoaded"></script>
<script>
function onAmapLoaded() {
  // 高德 SDK 加载完毕，等待后端数据
  window._amapReady = true;
}
</script>
```

---

## 与 SSE 对接

```javascript
const evtSource = new EventSource('/route/generate'); // 或用 fetch POST

evtSource.addEventListener('result', function(e) {
  const data = JSON.parse(e.data);
  if (window._amapReady) {
    renderRouteMap(data.route);
  } else {
    // SDK 还没加载完，等一下
    var t = setInterval(function() {
      if (window._amapReady) {
        clearInterval(t);
        renderRouteMap(data.route);
      }
    }, 100);
  }
});
```

---

## 效果

- 每个 POI 显示标记（A/B/C...）+ 名称标签
- POI 之间连接真实步行路径蓝线
- 点击标记弹出：评分、排队风险、团购价格、导航按钮
- 地图自动缩放到包含所有 POI 的视野
