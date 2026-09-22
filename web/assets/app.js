/**
 * app.js — 自研 PerfCollect Web 看板共享逻辑
 * 提供 window.PerfCharts：图表创建 / 渲染 / 统计 / 无数据自动隐藏
 * V0.2：新增帧时间 / 网络 / 电池温度图表
 */
(function () {
  'use strict';

  var _resizeBound = false;

  function makeChart(elId, group) {
    var el = document.getElementById(elId);
    if (!el) return null;
    // v98：ECharts/zrender 会在 canvas 上接管 wheel。即使 dataZoom 禁用了滚轮，
    // 事件仍可能被消费，导致鼠标停在图表时页面无法上下滚动。捕获阶段只阻断
    // 图表自己的 wheel 监听，不 preventDefault，因此浏览器默认页面滚动继续生效。
    if (!el.__pageWheelBound) {
      el.__pageWheelBound = true;
      el.addEventListener('wheel', function (e) {
        if (!e.ctrlKey) e.stopImmediatePropagation();
      }, { capture: true, passive: true });
    }
    var chart = echarts.init(el);
    // 同一 group 的图表可联动（历史看板：鼠标悬停/点击时垂直线贯穿各模块）
    if (group) chart.group = group;
    // 容器初始宽度可能未就绪：立即按实际宽度重绘，防止曲线挤在左半边
    chart.resize();
    // 窗口缩放时统一重绘所有图表（只注册一次监听）
    if (!_resizeBound) {
      _resizeBound = true;
      window.addEventListener('resize', function () {
        document.querySelectorAll('.chart').forEach(function (c) {
          var inst = echarts.getInstanceByDom(c);
          if (inst) inst.resize();
        });
        // v99：图表 resize 后 grid 像素边界会变化；Pin 与历史 Label 必须在
        // 同一帧重新定位，避免窗口宽度变化后再次出现视觉漂移。
        if (_pinIdx !== null) _refreshPinAfterZoom();
        if (_zoomLabelTimeline) {
          renderLabelTimeline(_zoomLabelTimeline.elId, _zoomLabelTimeline.rows,
                              _zoomLabelTimeline.annotations, _zoomLabelTimeline.options);
        }
      });
    }
    return chart;
  }

  // ---------------- 卡片拖拽排序（2026-08-14 优化项①） ----------------
  function initSortable(containerSel) {
    var container = typeof containerSel === 'string'
      ? document.querySelector(containerSel) : containerSel;
    if (!container) return;
    var cards = container.querySelectorAll('.chart-card');
    if (!cards.length) return;
    var storeKey = 'perfcollect_order_' + (location.pathname.replace(/[^a-z0-9]/gi, '_') || 'root');

    // 应用上次保存的顺序
    try {
      var order = JSON.parse(localStorage.getItem(storeKey));
      if (order && order.length) {
        var byId = {};
        cards.forEach(function (c) { byId[c.id] = c; });
        order.forEach(function (id) { if (byId[id]) container.appendChild(byId[id]); });
      }
    } catch (e) {}

    var dragCard = null;
    function saveOrder() {
      var ids = [];
      container.querySelectorAll('.chart-card').forEach(function (c) { ids.push(c.id); });
      try { localStorage.setItem(storeKey, JSON.stringify(ids)); } catch (e) {}
    }
    function clearTargets() {
      container.querySelectorAll('.chart-card').forEach(function (c) { c.classList.remove('drop-target'); });
    }

    cards.forEach(function (card) {
      card.draggable = true;
      card.classList.add('sortable');
      card.addEventListener('dragstart', function () {
        dragCard = card;
        card.classList.add('dragging');
      });
      card.addEventListener('dragend', function () {
        dragCard = null;
        card.classList.remove('dragging');
        clearTargets();
        saveOrder();
      });
      card.addEventListener('dragover', function (e) { e.preventDefault(); });
      card.addEventListener('dragenter', function () { card.classList.add('drop-target'); });
      card.addEventListener('dragleave', function () { card.classList.remove('drop-target'); });
      card.addEventListener('drop', function (e) {
        e.preventDefault();
        if (!dragCard || dragCard === card) return;
        container.insertBefore(dragCard, card.nextSibling);
        clearTargets();
      });
    });
  }

  // ---------------- 图表联动（2026-08-14 优化项②） ----------------
  // 同一 group 的图表在鼠标悬停/点击时同步显示垂直线与 tooltip，便于纵向对比
  function enableLink(group) {
    try { echarts.connect(group); } catch (e) {}
  }

  // ---------------- 点击锁定贯穿线（2026-08-14 优化项④，v18 DOM 覆盖层方案） ----------------
  // 悬停看贯穿（跟随）；点击任意位置 → 像素坐标换算最近类目下标 → 在每张图上放一条
  // 绝对定位的 DOM 竖线（.pin-line）。DOM 线不依赖 ECharts markLine/tooltip 渲染，
  // 必然可见、不会被刷新/鼠标移动覆盖，双击解锁。
  // v18 修复：markLine 可能被 ECharts 内部渲染干扰、convertFromPixel 需兜底——
  // 线用 DOM 元素画（独立于 ECharts），坐标换算失败时按像素比例兜底。
  var _pinCharts = [];
  var _pinIdx = null;
  // v58（性能/所见=所报）：降采样与缓存状态
  //   _dispIdx：降采样显示索引（null=全量）——renderAll 对长报告抽稀后置入，pin/缩放按显示索引换算
  //   _catTimes：缓存类目轴数据，替代点击/定位时的 chart.getOption() 全量深拷贝
  //   _totalDurSec/_zoomWindowSec：缩放窗口时长（秒），替代 axisLabel formatter 里的 getOption().dataZoom
  var _dispIdx = null;
  var _catTimes = [];
  var _totalDurSec = 0;
  var _zoomWindowSec = null;   // null=全范围
  // 长报告降采样阈值：超过此点数的报告，显示类目/系列数据等距抽稀到该点数（统计仍全量）
  var DISP_MAX_POINTS = 3000;
  // v66：像素 ↔ 类目索引换算必须使用与 ECharts grid 相同的左右边距。
  // 单一来源避免 baseOption 与 pin 兜底公式分别写死后再次漂移。
  var GRID_PAD = { left: 56, right: 24 };
  var _probeWarned = false;   // convertFromPixel 失效告警只打印一次，避免每次点击刷屏

  // v43：判断容器内坐标 (x,y) 是否落在 legend（右上角"自选数据"）区域。
  //
  // 前两轮失败的根因（bun + echarts.min.js 5.5.0 SSR 实证，非臆测）：
  //   v41：LegendModel 是 ComponentModel，无 coordinateSystem → rect 恒 null → 拦截恒 false。
  //   v42：`group.transformCoordToGlobal(x, y)` 真实签名是 2 参返回数组（源码：
  //        `transformCoordToGlobal=function(t,e){var n=[t,e];...;return n}`），v42 传 3 参
  //        再读 out → 恒空数组 → gx=NaN → 拦截恒 false；且即便签名对，该方法**只对
  //        transform 矩阵（旋转/缩放）生效**——而 legend group 的位置走 `group.x/y`
  //        （实测 782,9）、`group.transform === undefined` → 原样返回局部坐标(≈-5,-5)。
  //
  // v43 方案：几何包围盒判定，但用**正确公式**——legend 无 transform 时，
  //   全局包围盒 = (group.x + rect.x, group.y + rect.y, rect.width, rect.height)
  // 实证（_diag12）：该公式算出 (777.4, 4, 114.6, 23.2)，与右上角布局精确吻合。
  // 有 transform（旋转/缩放，legend 实际不会发生）时用 2 参签名转 4 个角点兜底。
  //
  // 为何不用"元素树命中判定"（也实证过）：legend 项之间的空隙点击时
  // zrender target 为 null（_diag12：包围盒内 findHover 仅命中 3/15），
  // 元素树判定会漏掉空隙 → 蓝线仍锁到 legend 空隙像素（正是用户报障场景）。
  // 几何包围盒覆盖整个 legend 区域（含空隙），是唯一不漏的判据。
  function _hitLegendBox(chart, x, y) {
    try {
      var lm = chart.getModel().getComponent('legend');
      if (!lm) return false;
      var view = chart.getViewOfComponentModel(lm);
      var group = view && view.group;
      if (!group) return false;
      var rect = group.getBoundingRect();
      if (!rect) return false;
      var gx, gy, gw, gh;
      if (group.transform) {
        // 仅旋转/缩放时走此分支（正确 2 参签名，返回 [gx,gy]）；转 4 角点取外接矩形
        var xs = [], ys = [];
        [[rect.x, rect.y], [rect.x + rect.width, rect.y],
         [rect.x, rect.y + rect.height], [rect.x + rect.width, rect.y + rect.height]]
          .forEach(function (pt) {
            var g = group.transformCoordToGlobal(pt[0], pt[1]);
            if (g && isFinite(g[0]) && isFinite(g[1])) { xs.push(g[0]); ys.push(g[1]); }
          });
        if (!xs.length) return false;
        // 循环归约取极值（不用 Math.min/max.apply：大数组展开参数会抛 RangeError）
        var xMin = xs[0], xMax = xs[0], yMin = ys[0], yMax = ys[0];
        for (var i = 1; i < xs.length; i++) {
          if (xs[i] < xMin) xMin = xs[i]; if (xs[i] > xMax) xMax = xs[i];
          if (ys[i] < yMin) yMin = ys[i]; if (ys[i] > yMax) yMax = ys[i];
        }
        gx = xMin; gy = yMin;
        gw = xMax - gx; gh = yMax - gy;
      } else {
        // 常见情形：legend 仅平移（位置在 group.x/y，无 transform 矩阵）→ 直接相加
        gx = group.x + rect.x; gy = group.y + rect.y;
        gw = rect.width; gh = rect.height;
      }
      // 4px 容差：覆盖 legend 贴边/换行的紧邻空白
      var pad = 4;
      return x >= gx - pad && x <= gx + gw + pad &&
             y >= gy - pad && y <= gy + gh + pad;
    } catch (e) {}
    return false;
  }

  function enableClickPin(groupSel) {
    _pinCharts = [];
    try {
      var g = echarts.getGroup(groupSel) || [];
      _pinCharts = _pinCharts.concat(g);
    } catch (e) {}
    document.querySelectorAll('.chart').forEach(function (el) {
      var inst = echarts.getInstanceByDom(el);
      if (inst && _pinCharts.indexOf(inst) < 0) _pinCharts.push(inst);
    });
    if (!_pinCharts.length) { console.log('[PerfCollect] 点击锁定: 未找到图表'); return; }
    console.log('[PerfCollect] 点击锁定已启用, 图表数=' + _pinCharts.length);

    _pinCharts.forEach(function (chart) {
      if (!chart) return;
      var dom = chart.getDom();
      if (!dom || dom.__pinBound) return;
      dom.__pinBound = true;

      if (getComputedStyle(dom).position === 'static') dom.style.position = 'relative';

      // v43：事件链路不变（DOM click/dblclick，与 v42 及更早版本一致，
      // 保留"点空白也能锁定"等既有行为），仅把 legend 判定从"包围盒坐标变换"
      // 换成几何包围盒公式修正版 _hitLegendBox（v41/v42 均因坐标换算错误而失效）。
      dom.addEventListener('click', function (e) {
        var x = e.clientX, y = e.clientY;
        var r = dom.getBoundingClientRect();
        var localX = x - r.left;      // 容器内像素（所见即所点，与包围盒同一坐标系）
        var localY = y - r.top;
        if (_hitLegendBox(chart, localX, localY)) {
          console.log('[PerfCollect] 点击 legend → 跳过贯穿线锁定');
          return;
        }
        var idx = _pinIndexAtLocal(chart, dom, localX, localY);
        console.log('[PerfCollect] 点击 x=' + Math.round(x) + ' y=' + Math.round(y) +
                    ' → idx=' + idx + ' localX=' + Math.round(localX));
        if (idx === null || idx < 0) return;
        if (_pinIdx === idx) { console.log('[PerfCollect] 再点同点 → 解锁'); _pinUnlockAll(); }
        else { console.log('[PerfCollect] 锁定 index=' + idx); _pinLockAll(idx); }
      });
      dom.addEventListener('dblclick', function () { console.log('[PerfCollect] 双击 → 解锁'); _pinUnlockAll(); });
    });
  }

  // v66：像素 ↔ 类目索引的单一数学实现。ECharts 绘图区不包含 grid 左右留白，
  // 因此不能用 px / 容器宽度直接换算，否则左段索引偏晚、右段索引偏早。
  function pixelToIdx(px, width, n, padL, padR) {
    n = Math.round(n) || 0;
    if (n <= 0) return 0;
    var usable = width - padL - padR;
    if (!(usable > 0)) return 0;
    var i = Math.round((px - padL) / usable * (n - 1));
    return Math.max(0, Math.min(i, n - 1));
  }

  function idxToPixel(idx, width, n, padL, padR) {
    n = Math.round(n) || 0;
    if (n <= 1) return padL;
    var usable = width - padL - padR;
    if (!(usable > 0)) return padL;
    return padL + idx / (n - 1) * usable;
  }

  // v99：毫秒时间 → 类目轴连续索引。ECharts category 轴按采样点等距排布，
  // 因此 Label 边界也必须先映射到“第几个采样点”，不能直接拿全程毫秒百分比。
  // 非等间隔采样时在相邻点间线性插值，得到可与 dataZoom 共用的分数索引。
  function timeToCategoryIndex(timesMs, ms) {
    if (!timesMs || !timesMs.length || ms == null || !isFinite(Number(ms))) return null;
    var value = Number(ms);
    var n = timesMs.length;
    if (n === 1 || value <= Number(timesMs[0])) return 0;
    if (value >= Number(timesMs[n - 1])) return n - 1;
    var lo = 0, hi = n - 1;
    while (lo < hi - 1) {
      var mid = (lo + hi) >> 1;
      if (Number(timesMs[mid]) <= value) lo = mid; else hi = mid;
    }
    var a = Number(timesMs[lo]), b = Number(timesMs[hi]);
    if (!isFinite(a) || !isFinite(b) || b <= a) return lo;
    return lo + (value - a) / (b - a);
  }

  function zoomCategoryWindow(n, startPct, endPct) {
    n = Math.max(0, Math.round(Number(n)) || 0);
    if (n <= 1) return { start: 0, end: 0 };
    var start = Math.max(0, Math.min(100, Number(startPct) || 0));
    var endRaw = Number(endPct);
    var end = isFinite(endRaw) ? Math.max(start, Math.min(100, endRaw)) : 100;
    var last = n - 1;
    return { start: start / 100 * last, end: end / 100 * last };
  }

  function _pinIndexAtLocal(chart, dom, x, y) {
    // x/y 为容器内坐标（v42 前用 clientX-rect.left，本质相同）
    // v58：用缓存的 _catTimes（显示类目）替代 getOption() 深拷贝
    if (!_catTimes.length) return null;
    var n = _catTimes.length;
    // v66：ECharts 5.5.0 正向换算中 {xAxisIndex:0} 会返回 null；
    // series/grid finder 可正确返回类目索引。反向 convertToPixel 仍保持 xAxis finder。
    var finders = [{ seriesIndex: 0 }, { gridIndex: 0 }];
    var probeResults = [];
    for (var i = 0; i < finders.length; i++) {
      try {
        var pt = chart.convertFromPixel(finders[i], [x, y]);
        probeResults.push(JSON.stringify(finders[i]) + ' => ' + JSON.stringify(pt));
        if (pt && typeof pt[0] === 'number' && isFinite(pt[0])) {
          return Math.max(0, Math.min(Math.round(pt[0]), n - 1));
        }
      } catch (e) {
        probeResults.push(JSON.stringify(finders[i]) + ' => throw ' + String(e));
      }
    }
    if (!_probeWarned) {
      _probeWarned = true;
      console.warn('[PerfCollect] convertFromPixel finder 返回无效值，改用 grid 兜底换算：' +
                   probeResults.join('；'));
    }
    var w = dom.clientWidth || dom.getBoundingClientRect().width;
    if (!(w > 0)) return null;
    return pixelToIdx(x, w, n, GRID_PAD.left, GRID_PAD.right);
  }

  // v58：显示索引 → 全量索引（降采样后 _pinRows 仍为全量，快照取数需换算）
  function _pinFullIdx(idx) { return _dispIdx ? _dispIdx[idx] : idx; }

  function _pinLockAll(idx) {
    _pinIdx = idx;
    _pinCharts.forEach(function (c) {
      if (!c) return;
      var dom = c.getDom();
      var line = dom.querySelector('.pin-line');
      if (!line) {
        line = document.createElement('div');
        line.className = 'pin-line';
        dom.appendChild(line);
      }
      // v99：点击只负责确定最近采样索引；蓝线必须回到该采样点的类目坐标。
      // 原先保留原始点击像素会让蓝线与 ECharts 白线命中同一数据却不重叠。
      _pinPlaceLine(c, line, idx);
    });
    _pinShowData(idx, null);   // 浮层与蓝线共同吸附到命中的采样点
    _pinNotify(_buildPinSnapshot(_pinRows[_pinFullIdx(idx)]));   // v52（需求 B）：锁定 → 通知快照条显示
  }

  // 锁定时刻各模块数据浮层（仿 tooltip 样式，DOM 实现，独立于 ECharts tooltip 不与白线冲突）
  var _pinRows = [];
  // v46：按数值降序排数据项——哪个数值更高就显示在上面（用户原话"白线停在哪，哪个数值高就显示在最上面"）。
  // 各图单位不同（FPS/Jank%/CPU%/MB/KB/s/°C/V），统一按"显示数值大小"比较即可，无需单位换算。
  function _pinSortParts(items, suffix) {
    var valid = items.filter(function (it) {
      return it && typeof it[0] === 'number' && isFinite(it[0]);
    });
    valid.sort(function (a, b) { return b[0] - a[0]; });
    var text = valid.map(function (it) { return it[1]; }).join(' · ');
    return text ? (text + (suffix || '')) : '';
  }
  // v76：新数据优先使用 FPS 短窗聚合；旧 JSONL 无 summary 时保持原字段口径。
  function fpsMetric(row, key) {
    var f = row && row.fps ? row.fps : {};
    var s = row && row.fps_window_summary ? row.fps_window_summary : {};
    var summaryKey = null;
    if (key === 'jank_rate') summaryKey = 'jank_rate';
    else if (key === 'frame_p95_ms') summaryKey = 'frame_p95_peak_ms';
    else if (key === 'frame_max_ms') summaryKey = 'frame_max_peak_ms';
    if (summaryKey && typeof s[summaryKey] === 'number' && isFinite(s[summaryKey])) {
      return s[summaryKey];
    }
    return (typeof f[key] === 'number' && isFinite(f[key])) ? f[key] : null;
  }

  function hasFpsWindowSummary(rows) {
    return (rows || []).some(function (r) {
      return r && r.fps_window_summary && r.fps_window_summary.window_count > 0;
    });
  }

  var _PIN_FIELDS = {
    'chart-fps':       function (r) { var f = r.fps || {}; return _pinSortParts([
      [f.fps, 'FPS ' + f.fps],
      [fpsMetric(r, 'jank_rate') != null ? fpsMetric(r, 'jank_rate') * 100 : null,
       fpsMetric(r, 'jank_rate') != null ? 'Jank ' + (fpsMetric(r, 'jank_rate') * 100).toFixed(1) + '%' : null],
    ]); },
    'chart-frametime': function (r) { var f = r.fps || {}; return _pinSortParts([
      [f.frame_p50_ms, 'P50 ' + f.frame_p50_ms + 'ms'],
      [fpsMetric(r, 'frame_p95_ms'), 'P95 ' + fpsMetric(r, 'frame_p95_ms') + 'ms'],
      [fpsMetric(r, 'frame_max_ms'), 'Max ' + fpsMetric(r, 'frame_max_ms') + 'ms'],
    ]); },
    'chart-cpu':       function (r) { var c = r.cpu || {}; var ofTotal = (_cores && c.cpu_proc_pct != null) ? c.cpu_proc_pct / _cores : null; return _pinSortParts([
      [c.cpu_total_pct, '总 ' + c.cpu_total_pct + '%'],
      [c.cpu_proc_pct, '进程 ' + c.cpu_proc_pct + '%'],
      [ofTotal, ofTotal != null ? '占整机 ' + ofTotal.toFixed(1) + '%' : null],
    ]); },
    'chart-mem':       function (r) { var m = r.mem || {}; return _pinSortParts([
      [m.pss_kb != null ? m.pss_kb / 1024 : null, m.pss_kb != null ? 'PSS ' + (m.pss_kb / 1024).toFixed(1) + 'MB' : null],
      [m.vmrss_kb != null ? m.vmrss_kb / 1024 : null, m.vmrss_kb != null ? 'RSS ' + (m.vmrss_kb / 1024).toFixed(1) + 'MB' : null],
    ]); },
    'chart-net':       function (r) { var n = r.net || {}; return _pinSortParts([
      [n.rx_kbps, '↓' + n.rx_kbps],
      [n.tx_kbps, '↑' + n.tx_kbps],
    ], ' KB/s'); },
    'chart-temp':      function (r) { var t = r.therm || {}; return _pinSortParts([
      [t.temp_c, t.temp_c + '°C'],
      [t.voltage_v, t.voltage_v + 'V'],
    ]); },
  };
  function setPinData(rows) { _pinRows = rows || []; }

  // v52（需求 B）：锁定时刻全指标快照条——挂一个可空钩子，锁定/解锁时由 app.js
  // 在**同一处**通知外部（report.html 注册）。锁定传快照对象、解锁传 null。
  // 这样 report.html 顶部快照条与蓝线锁定状态严格同步，不存在两处逻辑分叉。
  var _pinHook = null;
  function setPinHook(fn) { _pinHook = (typeof fn === 'function') ? fn : null; }
  function _pinNotify(snapshot) { if (_pinHook) { try { _pinHook(snapshot); } catch (e) {} } }

  // 拼锁定时刻的全指标快照（口径与 _PIN_FIELDS 完全同源，数值/文本一致）。
  // v54（可读性）：按模块分组多行——groups 顺序固定（FPS→帧时间→CPU→内存→网络→温度），
  // 组内仍按数值降序。返回 { t:'12.3s', groups:[{name,text},...], text:'...'(兼容拼接) }；
  // 无行数据/无任何有效指标返回 null。
  function _buildPinSnapshot(row) {
    if (!row) return null;
    var f = row.fps || {}, c = row.cpu || {}, m = row.mem || {}, n = row.net || {}, th = row.therm || {};
    // 每项 [数值, 文本]；数值仅用于组内排序（与 _pinSortParts 同口径），null 项过滤
    function push(arr, v, text) { if (v != null && isFinite(v)) arr.push([v, text]); }
    function group(name, arr) {
      arr.sort(function (a, b) { return b[0] - a[0]; });
      return arr.length ? { name: name, text: arr.map(function (it) { return it[1]; }).join(' · ') } : null;
    }
    var fpsG = [], ftG = [], cpuG = [], memG = [], netG = [], tempG = [];
    push(fpsG, f.fps, 'FPS ' + f.fps);
    var jankRate = fpsMetric(row, 'jank_rate');
    var frameP95 = fpsMetric(row, 'frame_p95_ms');
    var frameMax = fpsMetric(row, 'frame_max_ms');
    if (jankRate != null) push(fpsG, jankRate * 100, 'Jank ' + (jankRate * 100).toFixed(1) + '%');
    push(ftG, f.frame_p50_ms, 'P50 ' + f.frame_p50_ms + 'ms');
    push(ftG, frameP95, 'P95 ' + frameP95 + 'ms');
    push(ftG, frameMax, 'Max ' + frameMax + 'ms');
    push(cpuG, c.cpu_total_pct, 'CPU总 ' + c.cpu_total_pct + '%');
    push(cpuG, c.cpu_proc_pct, 'CPU进程 ' + c.cpu_proc_pct + '%');
    if (_cores && c.cpu_proc_pct != null) push(cpuG, c.cpu_proc_pct / _cores, '占整机 ' + (c.cpu_proc_pct / _cores).toFixed(1) + '%');
    if (m.pss_kb != null) push(memG, m.pss_kb / 1024, 'PSS ' + (m.pss_kb / 1024).toFixed(1) + 'MB');
    if (m.vmrss_kb != null) push(memG, m.vmrss_kb / 1024, 'RSS ' + (m.vmrss_kb / 1024).toFixed(1) + 'MB');
    push(netG, n.rx_kbps, '↓' + n.rx_kbps + 'KB/s');
    push(netG, n.tx_kbps, '↑' + n.tx_kbps + 'KB/s');
    push(tempG, th.temp_c, th.temp_c + '°C');
    push(tempG, th.voltage_v, th.voltage_v + 'V');

    var groups = [group('FPS', fpsG), group('帧时间', ftG), group('CPU', cpuG),
                  group('内存', memG), group('网络', netG), group('温度', tempG)]
                 .filter(Boolean);
    if (!groups.length) return null;
    var t = row.t_ms != null ? (row.t_ms / 1000).toFixed(1) + 's' : '';
    return { t: t, t_ms: row.t_ms, groups: groups,
             text: groups.map(function (g) { return g.text; }).join(' · ') };
  }
  function _pinShowData(idx, localX) {
    var row = _pinRows[_pinFullIdx(idx)];   // v58：显示索引 → 全量行
    if (!row) return;
    _pinCharts.forEach(function (c) {
      if (!c) return;
      var dom = c.getDom();
      var fn = _PIN_FIELDS[dom.id];
      var t = row.t_ms != null ? (row.t_ms / 1000).toFixed(1) + 's' : '';

      // ① 标题行右侧数据卡（V22 效果，稳定常驻展示）
      var card = dom.parentElement;
      if (card) {
        var head = card.querySelector('.chart-head');
        if (head) {
          var pd = head.querySelector('.pin-data');
          if (fn) {
            if (!pd) {
              pd = document.createElement('span');
              pd.className = 'pin-data';
              head.appendChild(pd);
            }
            pd.textContent = '📌 t=' + t + ' ' + fn(row);
            pd.style.display = 'inline-block';
          } else if (pd) {
            pd.style.display = 'none';
          }
        }
      }

      // ② 蓝线旁浮层（V23 当前效果，跟随蓝线定位）
      var tip = dom.querySelector('.pin-tip');
      if (!fn) { if (tip) tip.style.display = 'none'; return; }
      if (!tip) {
        tip = document.createElement('div');
        tip.className = 'pin-tip';
        dom.appendChild(tip);
      }
      tip.textContent = 't=' + t + '\n' + fn(row);
      tip.style.display = 'block';
      _pinPlaceTip(c, tip, idx, localX);   // v58：抽成独立函数，缩放后复用重定位
    });
  }

  // 浮层定位：跟随蓝线像素 x（优先点击像素 localX，否则 convertToPixel 换算），右缘溢出翻转到线左侧
  function _pinPlaceTip(chart, tip, idx, localX) {
    var dom = chart.getDom();
    var w = dom.clientWidth || dom.getBoundingClientRect().width;
    var x = (typeof localX === 'number' && isFinite(localX)) ? Math.round(localX) : null;
    if (x === null) {
      try {
        var px = chart.convertToPixel({ xAxisIndex: 0 }, idx);
        if (typeof px === 'number' && isFinite(px)) x = px;
      } catch (e) {}
    }
    if (x === null) {
      var n = _catTimes.length || 1;   // v58：用缓存类目数，避免 getOption
      try { x = Math.round(idxToPixel(idx, w, n, GRID_PAD.left, GRID_PAD.right)); }
      catch (e) { x = GRID_PAD.left; }
    }
    var tw = tip.offsetWidth || 150;
    var left = x + 10;
    if (left + tw > w - 4) left = Math.max(4, x - tw - 10);
    tip.style.left = left + 'px';
  }

  function _pinPlaceLine(chart, line, idx) {
    var x = null;
    try {
      var px = chart.convertToPixel({ xAxisIndex: 0 }, idx);
      if (typeof px === 'number' && isFinite(px)) x = px;
    } catch (e) {}
    if (x === null) {
      var n = _catTimes.length || 1;   // v58：用缓存类目数，避免 getOption
      var dom = chart.getDom();
      var w = dom.clientWidth || dom.getBoundingClientRect().width;
      x = Math.round(idxToPixel(idx, w, n, GRID_PAD.left, GRID_PAD.right));
    }
    line.style.left = (x | 0) + 'px';
  }

  function _pinUnlockAll() {
    _pinIdx = null;
    document.querySelectorAll('.pin-line').forEach(function (l) { l.style.left = '-9999px'; });
    document.querySelectorAll('.pin-tip').forEach(function (tip) { tip.style.display = 'none'; });
    document.querySelectorAll('.pin-data').forEach(function (pd) { pd.style.display = 'none'; });
    _pinNotify(null);   // v52（需求 B）：解锁 → 通知快照条隐藏
  }

  // ---------------- 自定义时间拖动条（2026-08-14 v28，PerfCollect 云端风格） ----------------
  // 弃用 ECharts 自带 slider，自写小型 HTML 拖动条：
  //   - 体积小（高 14px）；按下即拖，无需精确抓手柄
  //   - 两端细蓝色竖条 = 缩放；中间选区拖动 = 平移；点击选区外 = 窗口跳转到点击处
  //   - 拖动时图表实时跟随；所有图表共享同一窗口（一个拖动条操作全部联动）
  // v48（UI优化 3.1）：支持"全局单条"模式——传 mountEl 时只建 1 个条挂到该容器
  //   （所有图共用 _dz 状态，本就全联动），省去 6 份重复波形与 6 个挂点；
  //   不传（老调用兼容）保持"每图一条"。
  // v48（UI优化 3.2）：事件改 Pointer Events（mouse+touch 统一），触屏可用；
  //   不支持 PointerEvent 的环境回退原 mouse 事件。
  var _dz = { start: 0, end: 100, charts: [], rows: [], sliders: [], n: 0 };
  var _zoomLabelTimeline = null;

  function createTimeSliders(charts, rows, mountEl) {
    var list = [];
    if (Array.isArray(charts)) list = charts.filter(Boolean);
    else list = Object.keys(charts).map(function (k) { return charts[k]; }).filter(Boolean);
    if (!list.length || !rows.length) return;
    // 清理旧拖动条（含全局容器里的）
    _dz.sliders.forEach(function (s) { if (s && s.parentNode) s.parentNode.removeChild(s); });
    _dz.sliders = [];
    _dz.charts = list;
    _dz.rows = rows;
    _dz.n = rows.length;
    _dz.start = 0;
    _dz.end = 100;
    if (mountEl) {
      // 全局单条：波形用首图数据（fps 通常最满），挂到指定容器
      var el = _buildSlider(list[0]);
      mountEl.appendChild(el);
      _dz.sliders.push(el);
    } else {
      list.forEach(function (chart) {
        if (!chart) return;
        var e = _buildSlider(chart);
        var dom = chart.getDom();
        if (dom.parentNode) dom.parentNode.appendChild(e);   // 老行为：每图卡内一条
        _dz.sliders.push(e);
      });
    }
    _renderAllSliders();
  }

  function _buildSlider(chart) {
    var el = document.createElement('div');
    el.className = 'pd-slider';
    el._chart = chart;   // _drawWave 用它取首条系列数据画迷你波形
    var canvas = document.createElement('canvas');
    canvas.className = 'pd-wave';
    var sel = document.createElement('div');
    sel.className = 'pd-selection';
    var hl = document.createElement('div');
    hl.className = 'pd-handle pd-handle-l';
    var hr = document.createElement('div');
    hr.className = 'pd-handle pd-handle-r';
    el.appendChild(canvas);
    el.appendChild(sel);
    el.appendChild(hl);
    el.appendChild(hr);
    _bindSlider(el);
    return el;
  }

  function _bindSlider(el) {
    // v48（UI优化 3.2）：Pointer Events 统一 mouse/touch；capture 指针后
    // 拖出元素外也能收到 move；touch-action:none 已在 .pd-slider 上（防页面滚动抢手势）
    var hasPointer = typeof window.PointerEvent !== 'undefined';
    var down = hasPointer ? 'pointerdown' : 'mousedown';
    var move = hasPointer ? 'pointermove' : 'mousemove';
    var up = hasPointer ? 'pointerup' : 'mouseup';
    el.addEventListener(down, function (e) {
      if (hasPointer && e.isPrimary === false) return;   // 多指：只用首个触点
      e.preventDefault();
      try { el.setPointerCapture(e.pointerId); } catch (err) {}
      var rect = el.getBoundingClientRect();
      if (rect.width <= 0) return;
      var ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
      var pct = ratio * 100;
      var start = _dz.start, end = _dz.end;
      var mode;
      var hit = 2.5;   // 手柄命中放宽（%），好抓
      if (Math.abs(pct - start) <= hit) mode = 'resizeL';
      else if (Math.abs(pct - end) <= hit) mode = 'resizeR';
      else if (pct >= start && pct <= end) mode = 'move';
      else {
        // 点击选区外：窗口跳转（保持宽度，中心对齐点击处）
        var w = end - start;
        var ns = pct - w / 2;
        if (ns < 0) ns = 0;
        if (ns + w > 100) ns = 100 - w;
        _dz.start = ns;
        _dz.end = ns + w;
        mode = 'move';
      }
      var dragStartPct = pct;
      var baseStart = _dz.start, baseEnd = _dz.end;

      function onMove(ev) {
        var r2 = el.getBoundingClientRect();
        var p2 = Math.max(0, Math.min(1, (ev.clientX - r2.left) / r2.width)) * 100;
        var delta = p2 - dragStartPct;
        if (mode === 'resizeL') {
          _dz.start = Math.max(0, Math.min(baseStart + delta, baseEnd - 5));
        } else if (mode === 'resizeR') {
          _dz.end = Math.min(100, Math.max(baseEnd + delta, baseStart + 5));
        } else {
          var w2 = baseEnd - baseStart;
          var ns2 = baseStart + delta;
          if (ns2 < 0) ns2 = 0;
          if (ns2 + w2 > 100) ns2 = 100 - w2;
          _dz.start = ns2;
          _dz.end = ns2 + w2;
        }
        _syncDragUI();   // v29：rAF 节流合并渲染，避免 mousemove 高频触发 6 图重绘
      }
      function onUp() {
        document.removeEventListener(move, onMove);
        document.removeEventListener(up, onUp);
        _applyZoom();   // v30：松手强制应用最终窗口（防最后奇数帧图表未更新）
      }
      document.addEventListener(move, onMove);
      document.addEventListener(up, onUp);
      _syncDragUI();
    });
    // 画迷你波形
    _drawWave(el);
  }

  function _drawWave(el) {
    try {
      var chart = el._chart;
      var opt = chart.getOption();
      var data = (opt.series && opt.series[0] && opt.series[0].data) || [];
      var canvas = el.querySelector('.pd-wave');
      if (!canvas) return;
      var w = canvas.clientWidth || el.clientWidth || 0;
      var h = canvas.clientHeight || el.clientHeight || 0;
      if (!w || !h || !data.length) return;
      var dpr = window.devicePixelRatio || 1;
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      var ctx = canvas.getContext('2d');
      ctx.scale(dpr, dpr);
      ctx.clearRect(0, 0, w, h);
      // 数据归一化画线（跳过空值）
      var vals = [];
      for (var i = 0; i < data.length; i++) {
        var v = data[i];
        if (typeof v === 'number' && isFinite(v)) vals.push(v);
      }
      if (!vals.length) return;
      // 循环归约取极值（长报告 vals 可达数十万点，apply 展开会抛 RangeError）
      var lo = vals[0], hi = vals[0];
      for (var k = 1; k < vals.length; k++) {
        if (vals[k] < lo) lo = vals[k];
        if (vals[k] > hi) hi = vals[k];
      }
      var span = hi - lo || 1;
      ctx.beginPath();
      var first = true;
      for (var j = 0; j < data.length; j++) {
        var vv = data[j];
        if (typeof vv !== 'number' || !isFinite(vv)) { first = true; continue; }
        var x = w * (j / (data.length - 1));
        var y = h - 2 - (vv - lo) / span * (h - 4);
        if (first) { ctx.moveTo(x, y); first = false; }
        else ctx.lineTo(x, y);
      }
      ctx.strokeStyle = 'rgba(79,195,247,0.55)';
      ctx.lineWidth = 1;
      ctx.stroke();
    } catch (e) {}
  }

  // v30 性能优化升级：拖动条 UI 与图表渲染分离频率——
  //   滑块/选区/手柄用 CSS 更新（极廉价），跟随 rAF 每帧(60fps)；
  //   图表缩放重绘较贵，每隔一帧应用一次(~30fps)，观感无差异但重绘负载减半。
  //   松手时强制应用最终窗口，保证最终状态精确。
  var _rafPending = false;
  var _dragFrame = 0;
  function _syncDragUI() {
    if (_rafPending) return;
    _rafPending = true;
    requestAnimationFrame(function () {
      _rafPending = false;
      _renderAllSliders();      // 60fps：CSS 移动，廉价
      _dragFrame++;
      if (_dragFrame % 2 === 1) _applyZoom();   // 30fps：图表重绘，负载减半
    });
  }

  function _renderAllSliders() {
    _dz.sliders.forEach(function (el) {
      if (!el) return;
      var sel = el.querySelector('.pd-selection');
      var hl = el.querySelector('.pd-handle-l');
      var hr = el.querySelector('.pd-handle-r');
      if (!sel || !hl || !hr) return;
      var s = _dz.start, e = _dz.end;
      sel.style.left = s + '%';
      sel.style.width = (e - s) + '%';
      hl.style.left = s + '%';
      hr.style.left = e + '%';
    });
  }

  function _applyZoom() {
    // v58：缩放窗口时长（秒）——供 axisLabel formatter 判断刻度精度，替代 getOption().dataZoom
    _zoomWindowSec = (_dz.end - _dz.start) / 100 * _totalDurSec;
    _dz.charts.forEach(function (c) {
      try {
        // animation:false → 缩放无过渡动画，拖动即时生效不滞后（v29）
        c.dispatchAction({ type: 'dataZoom', dataZoomIndex: 0,
                           start: _dz.start, end: _dz.end, animation: false });
      } catch (e) {}
    });
    _refreshPinAfterZoom();   // v58（任务1）：缩放/平移后重算锁定蓝线位置，所见=所报
    if (_zoomLabelTimeline) {
      renderLabelTimeline(_zoomLabelTimeline.elId, _zoomLabelTimeline.rows,
                          _zoomLabelTimeline.annotations, _zoomLabelTimeline.options);
    }
  }

  // v100：缩放/平移后按当前视图重定位锁定蓝线/浮层；锁定点被窗口排除时只隐藏
  // 图内元素，顶部锁定快照保持不变，避免追加/移除警告行导致整页上下抖动。
  function _refreshPinAfterZoom() {
    if (_pinIdx === null) return;
    var n = _dispIdx ? _dispIdx.length : _pinRows.length;
    if (!n) return;
    var windowIdx = zoomCategoryWindow(n, _dz.start, _dz.end);
    var visible = _pinIdx >= windowIdx.start && _pinIdx <= windowIdx.end;
    _pinCharts.forEach(function (c) {
      if (!c) return;
      var dom = c.getDom();
      var line = dom.querySelector('.pin-line');
      if (line) {
        if (visible) { _pinPlaceLine(c, line, _pinIdx); line.style.display = ''; }
        else line.style.left = '-9999px';   // 窗口外 → 隐藏蓝线
      }
      var tip = dom.querySelector('.pin-tip');
      if (tip) {
        if (visible) { tip.style.display = 'block'; _pinPlaceTip(c, tip, _pinIdx, null); }
        else tip.style.display = 'none';
      }
    });
  }

  function clearTimeSliders() {
    _dz.sliders.forEach(function (s) { if (s && s.parentNode) s.parentNode.removeChild(s); });
    _dz.sliders = [];
  }

  function clean(arr) {
    return arr.filter(function (v) { return typeof v === 'number' && isFinite(v); });
  }

  function avg(arr) { var a = clean(arr); return a.length ? a.reduce(function (s, v) { return s + v; }, 0) / a.length : null; }

  function metricIsFresh(row, key) {
    var meta = row && row.metric_meta && row.metric_meta[key];
    return !(meta && meta.is_reused === true);
  }

  function freshSeries(rows, key, getter) {
    return series(rows, function (r) {
      return metricIsFresh(r, key) ? getter(r) : null;
    });
  }
  // 极值用循环归约（不用 Math.min/max.apply：长报告数十万点时参数展开抛
  // RangeError: Maximum call stack size exceeded，导致统计栏整体崩溃）
  function min(arr) {
    var a = clean(arr);
    if (!a.length) return null;
    var m = a[0];
    for (var i = 1; i < a.length; i++) { if (a[i] < m) m = a[i]; }
    return m;
  }
  function max(arr) {
    var a = clean(arr);
    if (!a.length) return null;
    var m = a[0];
    for (var i = 1; i < a.length; i++) { if (a[i] > m) m = a[i]; }
    return m;
  }
  function p95(arr) {
    var a = clean(arr).slice().sort(function (x, y) { return x - y; });
    if (!a.length) return null;
    var idx = Math.ceil(a.length * 0.95) - 1;
    return a[Math.max(0, Math.min(idx, a.length - 1))];
  }

  // v41：把 jsonl 原始行清洗为"纯采样点"并抽出 meta 行的核数。
  // meta / target_switch 等 event 行（无 t_ms、无 fps/cpu 字段）不参与绘图与统计，
  // 否则会在 x 轴塞进 NaN 类目、污染帧/CPU 等系列；核数从 {"event":"meta","cores":N} 提取。
  // 返回 { rows: [...], cores: <number|null> }；cores 已同步 setCores。
  function prepareRows(raw) {
    var rows = [], cores = null, device = null;
    (raw || []).forEach(function (r) {
      if (!r || typeof r !== 'object') return;
      if (r.event) {
        if (r.event === 'meta') {
          if (r.cores) {
            var c = parseInt(r.cores, 10);
            if (isFinite(c) && c > 0) cores = c;
          }
          if (r.device && typeof r.device === 'object') device = r.device;
        }
        return;   // event 行（meta / target_switch）不当作采样点
      }
      rows.push(r);
    });
    setCores(cores);   // 无论是否读到都重置：避免切到无 meta 行的报告时沿用上一份的核数
    return { rows: rows, cores: cores, device: device };
  }

  // v47：设备信息结构化 → 行数组 [{label, value}, ...]，历史看板 meta 区**分行分字段**显示。
  //   - 设备行：市场名优先（无市场名用型号代码）；型号代码与市场名不同则附 " (型号代码)"
  //   - 芯片行：cpu_hardware · cpu_max_freq_mhz(MHz) · N 核 —— 缺哪个跳哪个；
  //     核数仅随芯片信息出现（避免无芯片信息时孤零零一行"8 核"）
  //   - 分辨率行：screen_resolution
  // 某行 value 为空则整行不返回；device 无有效字段返回 []。
  // 值来自真机 getprop，渲染方必须转义（见 report.html：全部走 textContent 建节点）。
  function deviceInfoLines(device, cores) {
    if (!device || typeof device !== 'object') return [];
    var lines = [];
    // ① 设备行
    var code = device.model_code || device.model;
    var name = device.market_name || code;
    if (name) {
      var text = String(name);
      if (code && String(code) !== text) text += ' (' + code + ')';
      lines.push({ label: '设备', value: text });
    }
    // ② 芯片行（硬件型号 / 主频先组，核数仅在有芯片信息时追加）
    var chip = [];
    if (device.cpu_hardware) chip.push(device.cpu_hardware);
    if (device.cpu_max_freq_mhz) chip.push(device.cpu_max_freq_mhz + 'MHz');
    if (chip.length && cores) chip.push(cores + ' 核');
    if (chip.length) lines.push({ label: '芯片', value: chip.join(' · ') });
    // ③ 分辨率行
    if (device.screen_resolution) lines.push({ label: '分辨率', value: device.screen_resolution });
    return lines;
  }

  // 一行版（index.html 状态栏 / 老调用兼容）：基于 deviceInfoLines 重组，不重复逻辑。
  // 缺省字段静默跳过；全空返回空串。
  function formatDeviceInfo(device, cores) {
    var lines = deviceInfoLines(device, cores);
    if (!lines.length) return '';
    return lines.map(function (l) { return l.value; }).join(' · ');
  }

  function statText(arr, unit, digits) {
    var d = digits || 1;
    var a = clean(arr);
    if (!a.length) return '无数据';
    // 顺序：平均 → 最高 → 最低 → P95（最高/峰值最利于找优化方向）
    var parts = ['平均 ' + avg(a).toFixed(d) + unit,
                 '最高 ' + max(a).toFixed(d) + unit];
    if (a.length > 1) parts.push('最低 ' + min(a).toFixed(d) + unit,
                                 'P95 ' + p95(a).toFixed(d) + unit);
    return parts.join(' · ');
  }

  function timeAxis(rows, idxArr) {
    if (idxArr) {
      return idxArr.map(function (i) { var r = rows[i]; return r && r.t_ms != null ? Math.round(r.t_ms / 100) / 10 : null; });
    }
    return rows.map(function (r) { return r.t_ms != null ? Math.round(r.t_ms / 100) / 10 : null; });
  }
  // v58：series 支持 idxArr（降采样显示索引）——仅对显示数据抽稀，统计仍走全量
  function series(rows, getter, idxArr) {
    if (idxArr) return idxArr.map(function (i) { return getter(rows[i]); });
    return rows.map(getter);
  }

  // v58（性能）：等距抽稀——长报告（>max 点）取 max 个显示索引，含首尾点。
  // 曲线渲染另有 sampling:'lttb'，这里只为减小 setOption 的类目/系列数据体积。
  function _downsampleIndices(n, max) {
    if (n <= max) return null;
    var idx = [];
    var step = (n - 1) / (max - 1);
    for (var i = 0; i < max; i++) {
      var j = Math.round(i * step);
      if (!idx.length || idx[idx.length - 1] !== j) idx.push(j);
    }
    if (idx[idx.length - 1] !== n - 1) idx.push(n - 1);
    return idx;
  }

  // v72：长报告的 6 图 tooltip 联动会在 mousemove 时频繁传播
  // highlight/downplay。ECharts 默认 emphasis 会重画曲线，接近 3000 点时
  // 肉眼可见为线条闪烁。禁用线系列 hover 强调，但保留 axisPointer/
  // tooltip 和跨图白线联动，数据与点击锁定逻辑不变。
  var baseLine = { type: 'line', showSymbol: false, connectNulls: true,
                   lineStyle: { width: 1.6 }, sampling: 'lttb',
                   emphasis: { disabled: true } };

  // v41：集中颜色映射——series 顶层 color（决定 legend 图标色 + tooltip marker 色）
  // 与 lineStyle.color（决定曲线色）必须取同一值，否则会出现"legend 图标一个色、
  // 曲线另一个色、白线 tooltip marker 又一个色"的错位。取值以既有 lineStyle.color 为准，
  // 不改变曲线本身颜色。
  var COLORS = {
    fps: '#4fc3f7', jank: '#ff8a65',
    p50: '#80deea', p95: '#ffab40', max: '#ef5350',
    cpu_total: '#81c784', cpu_proc: '#ffd54f', cpu_proc_of_total: '#b39ddb',
    pss: '#ba68c8', rss: '#90a4ae',
    rx: '#4dd0e1', tx: '#f06292',
    temp: '#ff7043', power: '#aed581',
  };

  // 核数（cpu_proc_pct ÷ 核数 = 进程占整机%）。实时看板从 /api/status 注入；
  // 历史报告从 jsonl 的 meta 行（{"event":"meta","cores":N}）读出；未知时 null，
  // 此时"进程占整机%"曲线不渲染（renderCpu 内判断）。
  var _cores = null;
  function setCores(n) {
    var v = parseInt(n, 10);
    _cores = (typeof v === 'number' && isFinite(v) && v > 0) ? v : null;
  }

  function baseOption(zoom) {
    var opt = {
      animation: false,
      // v29：禁用缩放/更新的过渡动画（dispatchAction dataZoom 也带 transition），
      // 拖动时间条时图表即时更新、无动画滞后感
      animationDurationUpdate: 0,
      // 绘图区尽量占满：left 给 Y 轴数字+单位，right 保持紧凑
      // bottom 在有时间滑动条时让出空间给 slider
      grid: { left: GRID_PAD.left, right: GRID_PAD.right, top: 34, bottom: 28 },
      tooltip: { trigger: 'axis', confine: true,
        // 跨图联动时立即跟随指针，避免上一个位置的 CSS 过渡与
        // 新一轮 showTip/hideTip 重叠产生视觉抖动。
        transitionDuration: 0,
        axisPointer: { type: 'line', animation: false },
        // v46：白线 tooltip 按数值降序排列（FPS 59 在 Jank 3 上面、进程% 129 在整机% 43 上面）
        order: 'valueDesc',
        valueFormatter: function (v) { return (typeof v === 'number') ? v.toFixed(2) : v; } },
      xAxis: { type: 'category', name: '秒', nameTextStyle: { fontSize: 10 }, axisLabel: { fontSize: 10 } },
      yAxis: { type: 'value', scale: true, axisLabel: { fontSize: 10 } },
      // 图例移到右上角（grid 上方），避免与左侧 Y 轴单位重叠。
      // 颜色（2026-08-14）：选中=纯白实色（深底最醒目），未选中=35% 半透明弱化，
      // 修复"选中时与背景糊、未选中反而白色显眼"的倒置对比。
      legend: {
        top: 4, right: 8, left: 'auto', itemWidth: 14,
        textStyle: { fontSize: 11, color: '#ffffff' },
        inactiveColor: 'rgba(255,255,255,0.35)',
      },
    };

    // v28：不再使用 ECharts 自带 slider（交互反人类），改用自定义 HTML 拖动条。
    // 此处只保留一个无 UI 的 inside dataZoom 作为"缩放状态容器"，
    // 自定义拖动条通过 dispatchAction 控制它的 start/end → 图表缩放。
    if (zoom) {
      opt.dataZoom = [
        { type: 'inside', xAxisIndex: 0,
          zoomOnMouseWheel: false, moveOnMouseWheel: false,
          moveOnMouseMove: false, zoomOnMouseMove: false,
          start: 0, end: 100 },
      ];
    }
    return opt;
  }

  // v73：echarts.connect 跨图同步 showTip 时，部分版本只会把源图命中的
  // 单个 seriesIndex 传给目标图。目标图虽然同一 dataIndex 上还有其他有效
  // 序列，原生 tooltip 仍只显示这一项。这里根据目标图自己的 series data
  // 重建当前采样点的完整 tooltip，不依赖联动事件携带了多少个 series。
  function _tooltipEscape(v) {
    return String(v == null ? '' : v)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function axisTooltipHtml(seriesList, params) {
    var ps = Array.isArray(params) ? params : (params ? [params] : []);
    if (!ps.length) return '';
    var first = ps[0] || {};
    var idx = Number(first.dataIndex);
    if (!isFinite(idx) || idx < 0) return '';
    idx = Math.floor(idx);
    var axisLabel = first.axisValueLabel != null ? first.axisValueLabel : first.axisValue;
    var items = [];
    (seriesList || []).forEach(function (s) {
      var raw = s && s.data ? s.data[idx] : null;
      var value = raw && typeof raw === 'object' && raw.value != null ? raw.value : raw;
      if (typeof value !== 'number' || !isFinite(value)) return;
      items.push({ name: s.name || '', value: value, color: s.color || (s.lineStyle && s.lineStyle.color) || '#aaa' });
    });
    items.sort(function (a, b) { return b.value - a.value; });
    var html = '<div>' + _tooltipEscape(axisLabel) + '</div>';
    items.forEach(function (item) {
      html += '<div style="display:flex;align-items:center;gap:7px;min-width:150px">' +
        '<span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:' +
        _tooltipEscape(item.color) + '"></span><span style="flex:1">' +
        _tooltipEscape(item.name) + '</span><b>' + item.value.toFixed(2) + '</b></div>';
    });
    return html;
  }

  function setMetricOption(chart, opt) {
    if (opt && opt.tooltip) {
      var tooltipSeries = opt.series || [];
      opt.tooltip.formatter = function (params) { return axisTooltipHtml(tooltipSeries, params); };
    }
    chart.setOption(opt);
  }

  function applyTime(charts, rows, elIds, idxArr) {
    var times = timeAxis(rows, idxArr);
    _catTimes = times;   // v58：缓存类目（供 pin 定位/点击换算，避免 getOption 深拷贝）
    _totalDurSec = rows.length ? ((rows[rows.length - 1].t_ms || 0) / 1000) : 0;
    elIds.forEach(function (id) {
      var c = charts[id];
      if (!c) return;
      var xo = {
        xAxis: {
          type: 'category', data: times, name: '秒',
          nameTextStyle: { fontSize: 10 },
          // 刻度精度自适应（2026-08-14）：
          // 未缩放 / 大范围 → 整数秒；拖动底部时间条放大后 → 采集最大精度（0.1s）
          // v58：span 改读模块级 _zoomWindowSec（_applyZoom 里随缩放更新），
          // 避免 formatter 每次渲染都 getOption().dataZoom 深拷贝。
          axisLabel: {
            fontSize: 10,
            formatter: function (val) {
              var span = _zoomWindowSec;
              if (span === null || span >= 40) return Math.round(val) + '';
              return (Math.round(val * 10) / 10) + '';
            },
          },
        },
      };
      c.setOption(xo);
    });
  }

  // ---------------- 各指标渲染 ----------------
  function renderFps(chart, rows, zoom, idxArr) {
    var fps = series(rows, function (r) { return r.fps ? r.fps.fps : null; }, idxArr);
    var jank = series(rows, function (r) {
      var v = fpsMetric(r, 'jank_rate');
      return v != null ? v * 100 : null;
    }, idxArr);
    var windowed = hasFpsWindowSummary(rows);
    // FPS y 轴上限 = 实际数据最高帧率向上取 20 的倍数（不设 120 地板）：
    // 设备能跑多少就显示多少——60Hz 划到 60，120Hz 划到 120，144Hz 划到 160，更高同理
    var top = max(fps) || 60;
    var maxFps = Math.max(30, Math.ceil(top / 20) * 20);
    var step = Math.round(maxFps / 4 / 5) * 5 || 10;
    setMetricOption(chart, {
      ...baseOption(zoom),
      series: [
        Object.assign({}, baseLine, { name: 'FPS', data: fps, yAxisIndex: 0, color: COLORS.fps, lineStyle: { width: 1.6, color: COLORS.fps } }),
        Object.assign({}, baseLine, { name: windowed ? 'Jank%(短窗合并)' : 'Jank%', data: jank, yAxisIndex: 1, color: COLORS.jank, lineStyle: { width: 1.2, color: COLORS.jank } }),
      ],
      yAxis: [
        { type: 'value', min: 0, max: maxFps, interval: step, axisLabel: { fontSize: 10 } },
        { type: 'value', name: 'Jank%', nameLocation: 'middle', nameGap: 36,
          min: 0, max: 100, axisLabel: { fontSize: 10 }, splitLine: { show: false } },
      ],
    });
  }

  function renderFrameTime(chart, rows, zoom, idxArr) {
    var p50 = series(rows, function (r) { return r.fps ? r.fps.frame_p50_ms : null; }, idxArr);
    var p95 = series(rows, function (r) { return fpsMetric(r, 'frame_p95_ms'); }, idxArr);
    var mx = series(rows, function (r) { return fpsMetric(r, 'frame_max_ms'); }, idxArr);
    var windowed = hasFpsWindowSummary(rows);
    setMetricOption(chart, {
      ...baseOption(zoom),
      yAxis: { type: 'value', name: 'ms', nameLocation: 'middle', nameGap: 36,
               min: 0, axisLabel: { fontSize: 10 } },
      series: [
        Object.assign({}, baseLine, { name: 'P50', data: p50, color: COLORS.p50, lineStyle: { width: 1.4, color: COLORS.p50 } }),
        Object.assign({}, baseLine, { name: windowed ? 'P95(短窗峰值)' : 'P95', data: p95, color: COLORS.p95, lineStyle: { width: 1.6, color: COLORS.p95 } }),
        Object.assign({}, baseLine, { name: windowed ? 'Max(短窗峰值)' : 'Max', data: mx, color: COLORS.max, lineStyle: { width: 1.2, color: COLORS.max } }),
      ],
    });
  }

  function renderCpu(chart, rows, zoom, idxArr) {
    var total = series(rows, function (r) { return r.cpu ? r.cpu.cpu_total_pct : null; }, idxArr);
    var proc = series(rows, function (r) { return r.cpu ? r.cpu.cpu_proc_pct : null; }, idxArr);
    // v41：进程占整机% = cpu_proc_pct ÷ 核数。核数从 /api/status（实时）或 jsonl
    // meta 行（历史）取得；未知时不渲染该曲线（setCores 未注入 / meta 缺失）。
    var procOfTotal = _cores
      ? series(rows, function (r) {
          var p = r.cpu ? r.cpu.cpu_proc_pct : null;
          return (typeof p === 'number' && isFinite(p)) ? Math.round(p / _cores * 100) / 100 : null;
        }, idxArr)
      : null;
    var seriesList = [
      Object.assign({}, baseLine, { name: '整机%', data: total, color: COLORS.cpu_total, lineStyle: { width: 1.6, color: COLORS.cpu_total } }),
      Object.assign({}, baseLine, { name: '进程%', data: proc, color: COLORS.cpu_proc, lineStyle: { width: 1.6, color: COLORS.cpu_proc } }),
    ];
    if (procOfTotal) {
      seriesList.push(Object.assign({}, baseLine, {
        name: '进程占整机%', data: procOfTotal, color: COLORS.cpu_proc_of_total,
        lineStyle: { width: 1.4, color: COLORS.cpu_proc_of_total },
      }));
    }
    setMetricOption(chart, {
      ...baseOption(zoom),
      yAxis: { type: 'value', name: '%', nameLocation: 'middle', nameGap: 36,
               min: 0, axisLabel: { fontSize: 10 } },
      series: seriesList,
    });
  }

  function renderMem(chart, rows, zoom, idxArr) {
    var pss = series(rows, function (r) {
      return r.mem && r.mem.pss_kb != null ? Math.round(r.mem.pss_kb / 1024 * 10) / 10 : null;
    }, idxArr);
    var rss = series(rows, function (r) {
      return r.mem && r.mem.vmrss_kb != null ? Math.round(r.mem.vmrss_kb / 1024 * 10) / 10 : null;
    }, idxArr);
    setMetricOption(chart, {
      ...baseOption(zoom),
      yAxis: { type: 'value', name: 'MB', nameLocation: 'middle', nameGap: 36,
               min: 0, axisLabel: { fontSize: 10 } },
      series: [
        Object.assign({}, baseLine, { name: 'PSS MB', data: pss, color: COLORS.pss, lineStyle: { width: 1.6, color: COLORS.pss } }),
        Object.assign({}, baseLine, { name: 'RSS MB', data: rss, color: COLORS.rss, lineStyle: { width: 1.2, color: COLORS.rss } }),
      ],
    });
  }

  function renderNet(chart, rows, zoom, idxArr) {
    var rx = series(rows, function (r) { return r.net ? r.net.rx_kbps : null; }, idxArr);
    var tx = series(rows, function (r) { return r.net ? r.net.tx_kbps : null; }, idxArr);
    setMetricOption(chart, {
      ...baseOption(zoom),
      yAxis: { type: 'value', name: 'KB/s', nameLocation: 'middle', nameGap: 36,
               min: 0, axisLabel: { fontSize: 10 } },
      series: [
        Object.assign({}, baseLine, { name: '下行↓', data: rx, color: COLORS.rx, lineStyle: { width: 1.6, color: COLORS.rx } }),
        Object.assign({}, baseLine, { name: '上行↑', data: tx, color: COLORS.tx, lineStyle: { width: 1.6, color: COLORS.tx } }),
      ],
    });
  }

  function renderTemp(chart, rows, zoom, idxArr) {
    var temp = series(rows, function (r) { return r.therm ? r.therm.temp_c : null; }, idxArr);
    var power = series(rows, function (r) { return r.therm ? r.therm.power_w : null; }, idxArr);
    // v48（UI优化 2.3）：温度下界按数据自适应——原固定 25 在冬天/散热好的机型上
    // 会把曲线截断贴底；取数据最小值-2，无数据回退 25
    var tMin = min(temp);
    var tempAxisMin = (tMin != null) ? Math.floor(tMin - 2) : 25;
    setMetricOption(chart, {
      ...baseOption(zoom),
      yAxis: [
        { type: 'value', name: '°C', nameLocation: 'middle', nameGap: 36,
          min: tempAxisMin, axisLabel: { fontSize: 10 } },
        { type: 'value', name: 'W', nameLocation: 'middle', nameGap: 36,
          min: 0, axisLabel: { fontSize: 10 }, splitLine: { show: false } },
      ],
      series: [
        Object.assign({}, baseLine, { name: '温度°C', data: temp, yAxisIndex: 0, color: COLORS.temp, lineStyle: { width: 1.6, color: COLORS.temp } }),
        Object.assign({}, baseLine, { name: '功率W', data: power, yAxisIndex: 1, color: COLORS.power, lineStyle: { width: 1.2, color: COLORS.power } }),
      ],
    });
  }

  // ---------------- 卡片可见性 ----------------
  function setCardVisible(chartId, visible) {
    var el = document.getElementById(chartId);
    if (!el || !el.parentElement) return;
    el.parentElement.style.display = visible ? '' : 'none';
  }

  function hasData(rows, getter) {
    return rows.some(function (r) { var v = getter(r); return typeof v === 'number' && isFinite(v); });
  }

  function renderAll(charts, rows, opts) {
    var zoom = !!(opts && opts.zoom);
    if (!rows.length) return;
    // v58（性能）：长报告降采样——仅对显示数据（类目/系列输入）抽稀，统计仍走全量
    _dispIdx = _downsampleIndices(rows.length, DISP_MAX_POINTS);
    var idxArr = _dispIdx;
    // 无数据指标自动隐藏对应卡片。
    // hasData 用 typeof number 判断（合法 0 值——静止 FPS/空载 CPU——不会隐藏卡片；
    // 2026-08-21 复核：getter 显式返回 null 代替 && 短路，语义等价且更清晰）
    var fpsVisible = hasData(rows, function (r) { return r.fps ? r.fps.fps : null; });
    var frameVisible = hasData(rows, function (r) { return r.fps ? r.fps.frame_p95_ms : null; });
    var cpuVisible = hasData(rows, function (r) { return r.cpu ? r.cpu.cpu_total_pct : null; });
    var memVisible = hasData(rows, function (r) { return r.mem ? r.mem.pss_kb : null; });
    var netVisible = hasData(rows, function (r) { return r.net ? r.net.rx_kbps : null; });
    var tempVisible = hasData(rows, function (r) { return r.therm ? r.therm.temp_c : null; });

    setCardVisible('chart-fps', fpsVisible);
    setCardVisible('chart-frametime', frameVisible);
    setCardVisible('chart-cpu', cpuVisible);
    setCardVisible('chart-mem', memVisible);
    setCardVisible('chart-net', netVisible);
    setCardVisible('chart-temp', tempVisible);

    if (fpsVisible && charts.fps) renderFps(charts.fps, rows, zoom, idxArr);
    if (frameVisible && charts.frametime) renderFrameTime(charts.frametime, rows, zoom, idxArr);
    if (cpuVisible && charts.cpu) renderCpu(charts.cpu, rows, zoom, idxArr);
    if (memVisible && charts.mem) renderMem(charts.mem, rows, zoom, idxArr);
    if (netVisible && charts.net) renderNet(charts.net, rows, zoom, idxArr);
    if (tempVisible && charts.temp) renderTemp(charts.temp, rows, zoom, idxArr);

    // 只操作当前可见图表。ECharts 在 display:none 的 0×0 容器上重建类目轴时
    // 可能拿不到 coordinateSystem 并抛错，进而中断后续统计/汇总/完整度渲染。
    var visibleChartIds = [];
    if (fpsVisible) visibleChartIds.push('fps');
    if (frameVisible) visibleChartIds.push('frametime');
    if (cpuVisible) visibleChartIds.push('cpu');
    if (memVisible) visibleChartIds.push('mem');
    if (netVisible) visibleChartIds.push('net');
    if (tempVisible) visibleChartIds.push('temp');
    applyTime(charts, rows, visibleChartIds, idxArr);

    // 关键：渲染后强制 resize，按当前容器实际宽度铺满（容器从隐藏转显示 / 窗口变化时
    // 若不 resize，echarts 会沿用旧宽度导致曲线只占左半边、右侧空白）
    visibleChartIds.forEach(function (k) {
      var c = charts[k];
      if (c) c.resize();
    });
  }

  // ---------------- 统计栏 ----------------
  function updateStats(charts, rows) {
    function put(id, text) { var el = document.getElementById(id); if (el) el.textContent = text; }
    // v61：FPS 统计栏附带缺数构成——区分"链路读取失败(probe_fail)"与"无渲染层(no_layer)"，
    // 避免只看到一条空白曲线却不知道缺了多少点、为什么缺（2026-09-11 事故复盘）
    put('stat-fps', 'FPS ' + statText(freshSeries(rows, 'fps', function (r) { return r.fps ? r.fps.fps : null; }), '') +
        fpsMissingNote(rows) + fpsQualityNote(rows));
    put('stat-frametime', (hasFpsWindowSummary(rows) ? '帧时间P95(短窗峰值) ' : '帧时间P95 ') +
        statText(freshSeries(rows, 'fps', function (r) { return fpsMetric(r, 'frame_p95_ms'); }), 'ms', 1));
    put('stat-cpu', '进程CPU ' + statText(freshSeries(rows, 'cpu', function (r) { return r.cpu ? r.cpu.cpu_proc_pct : null; }), '%'));
    put('stat-mem', 'PSS ' + statText(freshSeries(rows, 'mem', function (r) { return r.mem && r.mem.pss_kb != null ? r.mem.pss_kb / 1024 : null; }), ' MB'));
    put('stat-net', '下行 ' + statText(freshSeries(rows, 'net', function (r) { return r.net ? r.net.rx_kbps : null; }), 'KB/s', 1));
    put('stat-temp', '温度 ' + statText(freshSeries(rows, 'therm', function (r) { return r.therm ? r.therm.temp_c : null; }), '°C', 1));
  }

  // ---------------- 统计汇总 ----------------
  function computeStats(rows) {
    var fps = freshSeries(rows, 'fps', function (r) { return r.fps ? r.fps.fps : null; });
    // v48（UI优化 4.6）：静止段（fps==0，合法画面非性能问题）不入"最低帧率"统计
    var fpsActive = freshSeries(rows, 'fps', function (r) {
      var v = r.fps ? r.fps.fps : null;
      return (typeof v === 'number' && isFinite(v) && v > 0) ? v : null;
    });
    var jank = freshSeries(rows, 'fps', function (r) {
      var v = fpsMetric(r, 'jank_rate');
      return v != null ? v * 100 : null;
    });
    var ftP95 = freshSeries(rows, 'fps', function (r) { return fpsMetric(r, 'frame_p95_ms'); });
    var cpuProc = freshSeries(rows, 'cpu', function (r) { return r.cpu ? r.cpu.cpu_proc_pct : null; });
    var pss = freshSeries(rows, 'mem', function (r) {
      return r.mem && r.mem.pss_kb != null ? r.mem.pss_kb / 1024 : null;
    });
    var temp = freshSeries(rows, 'therm', function (r) { return r.therm ? r.therm.temp_c : null; });
    var durS = rows.length ? Math.round((rows[rows.length - 1].t_ms || 0) / 1000) : 0;
    // v48（UI优化 1.1）：众数刷新率 → KPI 分级的"满帧 / 帧时间阈值"基准（无则 60）
    var hzCount = {};
    rows.forEach(function (r) {
      if (!metricIsFresh(r, 'fps')) return;
      var f = r.fps && r.fps.refresh_hz;
      if (typeof f === 'number' && isFinite(f)) hzCount[f] = (hzCount[f] || 0) + 1;
    });
    var refresh_hz = null, bestCnt = 0;
    var jankCount = 0, jankTotal = 0;
    rows.forEach(function (r) {
      var s = r && r.fps_window_summary;
      if (s && typeof s.jank_count === 'number' && typeof s.jank_total === 'number' && s.jank_total > 0) {
        jankCount += s.jank_count;
        jankTotal += s.jank_total;
      }
    });
    Object.keys(hzCount).forEach(function (k) {
      if (hzCount[k] > bestCnt) { bestCnt = hzCount[k]; refresh_hz = Number(k); }
    });
    return {
      count: rows.length, durS: durS,
      fps_avg: avg(fps), fps_min: min(fps), fps_min_active: min(fpsActive), fps_p95: p95(fps),
      jank_avg: jankTotal > 0 ? jankCount / jankTotal * 100 : avg(jank),
      ft_p95_avg: avg(ftP95),
      cpu_avg: avg(cpuProc),
      pss_peak: max(pss), pss_avg: avg(pss),
      temp_avg: avg(temp),
      refresh_hz: refresh_hz,
      fps_windowed: hasFpsWindowSummary(rows),
      jank_frame_weighted: jankTotal > 0,
      freshness_aware: rows.some(function (r) { return r && r.metric_meta; }),
    };
  }

  function renderSummary(elId, stats) {
    var el = document.getElementById(elId);
    if (!el || !stats) return;
    function fmt(v, d) { return (typeof v === 'number' && isFinite(v)) ? Number(v).toFixed(d) : '-'; }
    // v48（UI优化 1.1）：KPI 语义化分级 + 阈值着色（阈值取自 指标说明.md §1/§2/§12）
    var hz = (typeof stats.refresh_hz === 'number' && isFinite(stats.refresh_hz)) ? stats.refresh_hz : 60;
    var period = 1000 / hz;                     // 刷新周期（ms）
    // FPS：满帧(±5%)绿 / ≥30 黄 / <30 红
    function fpsGrade(v) {
      if (v == null) return '';
      if (v >= hz * 0.95) return 'kpi-good';
      if (v >= 30) return 'kpi-warn';
      return 'kpi-bad';
    }
    // Jank：<1% 绿 / 1~5% 黄 / >5% 红
    function jankGrade(v) {
      if (v == null) return '';
      if (v < 1) return 'kpi-good';
      if (v <= 5) return 'kpi-warn';
      return 'kpi-bad';
    }
    // 帧时间 P95：≤2×周期 绿 / ≤4×周期 黄 / >4×周期 红
    function ftGrade(v) {
      if (v == null) return '';
      if (v <= 2 * period) return 'kpi-good';
      if (v <= 4 * period) return 'kpi-warn';
      return 'kpi-bad';
    }
    // 温度：<40 绿 / 40~45 黄 / >45 红
    function tempGrade(v) {
      if (v == null) return '';
      if (v < 40) return 'kpi-good';
      if (v <= 45) return 'kpi-warn';
      return 'kpi-bad';
    }
    // 统一卡片构造：hero=核心 KPI（大卡）；grade 着色 .v
    function build(k, v, small, grade, hero) {
      return '<div class="item' + (hero ? ' kpi' : '') + '">' +
        '<div class="k">' + k + '</div>' +
        '<div class="v ' + (grade || '') + '">' + v +
        (small ? '<small> ' + small + '</small>' : '') + '</div></div>';
    }
    var fpsMinActive = stats.fps_min_active != null ? stats.fps_min_active : stats.fps_min;
    el.innerHTML =
      // 核心 KPI 置顶（大卡 + 阈值着色）——第一眼回答"这次测得好不好"
      build('平均帧率', fmt(stats.fps_avg, 1), '/ 满帧 ' + hz, fpsGrade(stats.fps_avg), true) +
      build(stats.jank_frame_weighted ? '卡顿率（帧加权）' : '卡顿率（均值）', fmt(stats.jank_avg, 2), '%', jankGrade(stats.jank_avg), true) +
      build(stats.fps_windowed ? '帧时间 P95（短窗峰值均值）' : '帧时间 P95（均值）', fmt(stats.ft_p95_avg, 1), 'ms', ftGrade(stats.ft_p95_avg), true) +
      // 次要指标
      build('最低帧率(除静止)', fmt(fpsMinActive, 1), 'FPS') +
      build('P95 帧率', fmt(stats.fps_p95, 1), 'FPS') +
      build(stats.freshness_aware ? '平均进程CPU（新采样）' : '平均进程CPU', fmt(stats.cpu_avg, 1), '%') +
      build('峰值内存', fmt(stats.pss_peak, 1), 'MB (PSS)') +
      build(stats.freshness_aware ? '平均内存（新采样）' : '平均内存', fmt(stats.pss_avg, 1), 'MB (PSS)') +
      build(stats.freshness_aware ? '平均温度（新采样）' : '平均温度', fmt(stats.temp_avg, 1), '°C', tempGrade(stats.temp_avg));
    // v49（需求 A）：删除"采集时长"元信息卡——时长/点数已移到报告标题行
    // （report.html selectRun 内拼入 meta-primary），汇总卡区不再显示，避免换行难看。
  }

  // ---------------- 数据完整度：缺数率 / 缺数原因 / 缺数区间（v61，2026-09-11 事故复盘） ----------------
  // 背景：run 20260911_162353 因主机 adb 通道瞬时失败，63 个采样点里 51 点取不到 FPS，
  // 而报告只呈现"曲线空白"——看不出缺了多少、为什么缺、缺在哪一段，只能人工反查设备日志。
  // 这里逐指标统计无值点（错误码分布 = 缺数原因）与连续缺数区间，报告顶部明示；
  // computeCompleteness 为纯函数，导出供 tests/test_nearest_cat.js 断言。
  var COMPLETENESS_METRICS = [
    { key: 'fps', label: 'FPS',
      get: function (r) { return r.fps ? r.fps.fps : null; },
      err: function (r) { return r.fps ? r.fps.error : null; } },
    { key: 'frametime', label: '帧时间',
      get: function (r) { return r.fps ? r.fps.frame_p50_ms : null; } },
    { key: 'cpu', label: 'CPU',
      get: function (r) { return r.cpu ? r.cpu.cpu_proc_pct : null; },
      err: function (r) { return r.cpu ? r.cpu.error : null; } },
    { key: 'mem', label: '内存',
      get: function (r) { return r.mem ? r.mem.pss_kb : null; },
      err: function (r) {
        var m = r.mem;
        if (!m) return null;                     // 该点连对象都没有 → 采样未就绪
        if (m.error) return m.error;             // v70 起采集端带码
        // v71：历史数据（v70 前）内存缺数不带码——pid 为 None 时必然没解析到进程，
        // 据此推断原因，让老报告也能判读（否则只能笼统显示"未取到值"）
        return m.pid == null ? 'no_pid' : null;
      } },
    { key: 'net', label: '网络',
      get: function (r) {
        var n = r.net || {};
        return (n.rx_kbps != null || n.tx_kbps != null) ? 1 : null;
      },
      err: function (r) {
        var n = r.net;
        if (!n) return null;
        if (n.error) return n.error;
        return n.pid == null ? 'no_pid' : null;   // v71：同上（历史数据推断）
      } },
    { key: 'temp', label: '温度',
      get: function (r) { return r.therm ? r.therm.temp_c : null; },
      err: function (r) { return r.therm ? r.therm.error : null; } },
  ];

  // 缺数原因码 → 人话（与采集端错误码一一对应，见 指标说明.md「一、1」「十」）
  var COMPLETENESS_REASONS = {
    probe_fail: '链路读取失败',
    gfx_unavailable: 'gfxinfo 不支持该应用，已回退 SurfaceFlinger',
    no_layer: '无渲染层(不在前台)',
    layer_read_fail: '渲染层失效',
    read_fail: '读取失败',
    no_pid: '进程未知(未解析到)',
    temperature_out_of_range: '温度超量程',
    // 兜底：没有错误码的缺数——多是采集线程本点尚无有效读数（首点/采样节奏未到，
    // 如内存 2s 一采的节流点）；v70 之前的历史数据部分指标不记录原因，也会落在这里
    no_value: '未取到值(无原因码)',
  };

  function _isValue(v) { return v != null && typeof v === 'number' && isFinite(v); }

  function _reasonText(code) { return COMPLETENESS_REASONS[code] || code; }

  function _reasonTexts(reasons) {
    var parts = [];
    Object.keys(reasons || {}).sort(function (a, b) { return reasons[b] - reasons[a]; })
      .forEach(function (c) { parts.push(_reasonText(c) + ' ' + reasons[c]); });
    return parts.join(' · ');
  }

  // 计算各指标缺数点数/缺数率/原因分布/连续缺数区间（时间单位秒，1 位小数）
  function computeCompleteness(rows) {
    rows = rows || [];
    var total = rows.length;
    var out = { total: total, metrics: {}, worst: null, worst_pct: 0 };
    COMPLETENESS_METRICS.forEach(function (m) {
      var missing = 0, reasons = {}, gaps = [], run = null;
      rows.forEach(function (r) {
        if (_isValue(m.get(r))) {
          if (run) { gaps.push(run); run = null; }
          return;
        }
        missing++;
        var code = (m.err && m.err(r)) || 'no_value';
        reasons[code] = (reasons[code] || 0) + 1;
        var t = Math.round((r.t_ms || 0) / 100) / 10;
        if (run) { run.to = t; run.n++; } else { run = { from: t, to: t, n: 1 }; }
      });
      if (run) gaps.push(run);
      var pct = total ? (missing / total * 100) : 0;
      out.metrics[m.key] = { key: m.key, label: m.label, total: total, missing: missing,
                             pct: pct, reasons: reasons, gaps: gaps };
      if (pct > out.worst_pct) { out.worst_pct = pct; out.worst = m.key; }
    });
    return out;
  }

  function completenessGrade(pct) {
    if (pct <= 5) return 'ok';
    if (pct <= 20) return 'warn';
    return 'bad';
  }

  // FPS 统计栏注记：无缺数返回空串（正常报告不显示噪声）
  function fpsMissingNote(rows) {
    var missing = 0, reasons = {};
    (rows || []).forEach(function (r) {
      var f = r.fps || {};
      if (_isValue(f.fps)) return;
      missing++;
      var c = f.error || 'no_value';
      reasons[c] = (reasons[c] || 0) + 1;
    });
    if (!missing) return '';
    return '  ⚠缺 ' + missing + ' 点（' + _reasonTexts(reasons) + '）';
  }

  // FPS 质量标记注记：只在真实新样本出现低置信/物理上限钳制时显示，
  // 旧 JSONL 无 metric_meta 时按每点真实样本兼容；正常报告返回空串不增加噪声。
  function fpsQualityNote(rows) {
    var low = 0, clamped = 0;
    (rows || []).forEach(function (r) {
      if (!metricIsFresh(r, 'fps')) return;
      var f = r.fps || {};
      if (f.fps_warn === 'low_frames') low++;
      if (f.fps_clamped === true) clamped++;
    });
    var parts = [];
    if (low) parts.push('低帧数低置信 ' + low + ' 点');
    if (clamped) parts.push('FPS钳制 ' + clamped + ' 点');
    return parts.length ? '  ⚠' + parts.join(' · ') : '';
  }

  // 渲染完整度卡片；全部指标完整时不显示（避免正常报告顶部多一块噪声）
  function renderCompleteness(elId, comp) {
    var el = document.getElementById(elId);
    if (!el || !comp || !comp.total) return;
    var worst = comp.worst ? comp.metrics[comp.worst] : null;
    if (!worst || !worst.missing) { el.innerHTML = ''; el.style.display = 'none'; return; }
    var grade = completenessGrade(worst.pct);
    var head = grade === 'bad' ? '⚠️ 数据大面积缺失'
             : grade === 'warn' ? '⚠️ 部分数据缺失' : '数据基本完整';
    // v69：可折叠卡片——默认收起只占一行（徽标 + 一句话摘要 + 箭头），点开展开详情。
    // 收起时 CSS 用 grid-template-rows 0fr→1fr 过渡做展开/收起动效（Chromium/Edge 支持）。
    var summary = worst.label + ' 缺 ' + worst.missing + '/' + worst.total + ' 点（' +
      worst.pct.toFixed(1) + '%）' +
      (Object.keys(worst.reasons).length ? ' · ' + _reasonTexts(worst.reasons) : '');
    var body = '';
    body += '<div class="cmpl-content"><div class="cmpl-sub">最差指标 ' + worst.label +
      '：缺 ' + worst.missing + '/' + worst.total + ' 点（' + worst.pct.toFixed(1) + '%）' +
      (Object.keys(worst.reasons).length ? ' —— ' + _reasonTexts(worst.reasons) : '') +
      '</div><div class="cmpl-table">';
    COMPLETENESS_METRICS.forEach(function (m) {
      var c = comp.metrics[m.key];
      if (!c) return;
      var g = completenessGrade(c.pct);
      body += '<div class="cmpl-row"><span class="cmpl-k">' + c.label + '</span>' +
        '<span class="cmpl-v ' + (c.missing ? g : 'ok') + '">' +
        (c.missing ? (c.missing + '/' + c.total + '（' + c.pct.toFixed(1) + '%）') : '完整') +
        '</span><span class="cmpl-r">' +
        (c.missing ? _reasonTexts(c.reasons) : '') + '</span></div>';
    });
    body += '</div>';
    var gaps = worst.gaps || [];
    if (gaps.length) {
      var chips = gaps.slice(0, 6).map(function (g) {
        var txt = g.n > 1 ? (g.from.toFixed(1) + '~' + g.to.toFixed(1) + 's') : (g.from.toFixed(1) + 's');
        return '<span class="cmpl-gap">' + txt + '</span>';
      }).join('');
      body += '<div class="cmpl-gaps"><span class="cmpl-k">' + worst.label + ' 缺数区间</span>' +
        chips + (gaps.length > 6 ? '<span class="cmpl-r">…共 ' + gaps.length + ' 段</span>' : '') +
        '</div>';
    }
    body += '<div class="cmpl-note">缺数 = 该采样点取不到值（多为设备/adb 链路抖动，' +
      '或目标不在前台），曲线在此处断开是如实记录；「无原因码」多为采样未就绪或历史数据' +
      '未记录原因（v70 前），常见原因见 指标说明.md「十」。</div></div>';

    var html = '<button type="button" class="cmpl-toggle" aria-expanded="false" title="点击展开/收起">' +
      '<span class="cmpl-badge ' + grade + '">' + head + '</span>' +
      '<span class="cmpl-toggle-text">' + summary + '</span>' +
      '<span class="cmpl-arrow">▶</span></button>' +
      '<div class="cmpl-body"><div class="cmpl-body-inner">' + body + '</div></div>';
    el.innerHTML = html;
    el.style.display = '';
    // 始终默认收起（不记忆偏好）：这块只在有缺数时出现，常驻只占一行不占地方，
    // 要看详情点一下即可——避免"展开过一次后每份报告都铺开"。
    var btn = el.querySelector('.cmpl-toggle');
    btn.addEventListener('click', function () {
      var now = el.classList.toggle('cmpl-open');
      btn.setAttribute('aria-expanded', now ? 'true' : 'false');
    });
  }

  // 在图上用灰色带标出该指标的缺数区间（直观看到"空洞在哪"）；
  // 端点用 nearestCat 吸附到合法类目值，避免落不到类目轴上静默不渲染。
  function markCompleteness(charts, rows, comp, key) {
    key = key || 'fps';
    if (!charts || !rows || !rows.length || !comp || !comp.metrics[key]) return;
    var chart = charts[key];
    if (!chart) return;
    var gaps = comp.metrics[key].gaps || [];
    var times = rows.map(function (r) { return r.t_ms; });
    // 无缺数时也要执行（data 为空 → 清掉上一份报告残留的灰带）
    var data = [];
    gaps.slice(0, 40).forEach(function (g) {
      var a = nearestCat(times, g.from * 1000), b = nearestCat(times, g.to * 1000);
      if (a == null || b == null) return;
      data.push([{ xAxis: a }, { xAxis: b }]);
    });
    try {
      chart.setOption({ series: [{ markArea: {
        silent: true, itemStyle: { color: 'rgba(144,164,174,0.14)' }, data: data,
      } }] });
    } catch (e) {}
  }

  // ---------------- Label 时间映射（仅供紧凑 Label 条；不再覆盖图表） ----------------
  function _annotationColor(color, alpha) {
    var m = /^#([0-9a-f]{6})$/i.exec(String(color || ''));
    if (!m) return 'rgba(79,195,247,' + alpha + ')';
    var n = parseInt(m[1], 16);
    return 'rgba(' + ((n >> 16) & 255) + ',' + ((n >> 8) & 255) + ',' +
      (n & 255) + ',' + alpha + ')';
  }

  function annotationMarkAreas(rows, annotations, showLabels) {
    if (!rows || !rows.length) return [];
    var times = rows.map(function (r) { return r.t_ms; });
    var first = times[0], last = times[times.length - 1];
    var data = [];
    (annotations || []).slice(0, 200).forEach(function (a) {
      var start = Number(a && a.start_ms);
      var end = a && a.end_ms == null ? last : Number(a && a.end_ms);
      if (!isFinite(start) || !isFinite(end) || end <= start || end < first || start > last) return;
      var x1 = nearestCat(times, Math.max(first, start));
      var x2 = nearestCat(times, Math.min(last, end));
      if (x1 == null || x2 == null) return;
      var text = String(a.text || '').slice(0, 120);
      data.push([{
        xAxis: x1,
        name: text,
        itemStyle: { color: _annotationColor(a.color, 0.18) },
        label: { show: !!showLabels, formatter: text, color: a.color || '#ffffff',
                 fontSize: 10, position: 'insideTop' },
      }, { xAxis: x2 }]);
    });
    return data;
  }

  function renderAnnotations(charts, rows, annotations) {
    // v96：Label 色带不再贯穿图表。保留此兼容入口用于清除同页面旧状态，
    // 避免热更新/切报告时遗留 v95 的 markArea。
    if (!charts) return;
    Object.keys(charts).forEach(function (key) {
      var chart = charts[key];
      if (!chart) return;
      var dom = chart.getDom && chart.getDom();
      if (dom && dom.parentElement && dom.parentElement.style.display === 'none') return;
      try {
        chart.setOption({ series: [{
          id: 'perfcollect-annotation-bands', type: 'line', data: [], silent: true,
          showSymbol: false, lineStyle: { opacity: 0 }, tooltip: { show: false },
          markArea: { silent: true, data: [] },
        }] });
      } catch (e) {}
    });
  }

  function renderAnnotationList(elId, annotations, onDelete) {
    var el = document.getElementById(elId);
    if (!el) return;
    while (el.firstChild) el.removeChild(el.firstChild);
    var items = annotations || [];
    if (!items.length) { el.style.display = 'none'; return; }
    items.forEach(function (a) {
      var chip = document.createElement('span');
      chip.className = 'annotation-chip';
      chip.style.borderColor = a.color || '#4fc3f7';
      var dot = document.createElement('i');
      dot.style.background = a.color || '#4fc3f7';
      var start = (Number(a.start_ms) / 1000).toFixed(1);
      var end = a.end_ms == null ? '末尾' : (Number(a.end_ms) / 1000).toFixed(1) + 's';
      chip.appendChild(dot);
      chip.appendChild(document.createTextNode(start + 's–' + end + ' · ' + String(a.text || '')));
      if (typeof onDelete === 'function') {
        var remove = document.createElement('button');
        remove.type = 'button';
        remove.title = '删除这条区间备注';
        remove.textContent = '×';
        remove.addEventListener('click', function () { onDelete(a); });
        chip.appendChild(remove);
      }
      el.appendChild(chip);
    });
    el.style.display = '';
  }

  function _startLabelRename(segment) {
    var item = segment && segment.__labelItem;
    var owner = segment && segment.parentNode && segment.parentNode.parentNode;
    var options = owner && owner.__labelOptions;
    if (!item || !item.id || !options || typeof options.onRename !== 'function') return;
    var existing = segment.querySelector('input');
    if (existing) { existing.focus(); return; }
    var labelName = segment.querySelector('.label-segment-name');
    var edit = segment.querySelector('.label-segment-edit');
    var input = document.createElement('input');
    input.type = 'text';
    input.maxLength = 120;
    input.value = String(item.text || '');
    if (labelName) labelName.style.display = 'none';
    if (edit) edit.style.display = 'none';
    segment.appendChild(input);
    var done = false;
    function finish(save) {
      if (done) return;
      done = true;
      var value = input.value.trim();
      if (input.parentNode) input.parentNode.removeChild(input);
      if (labelName) { labelName.style.display = ''; labelName.textContent = save && value ? value : String(item.text || 'Label'); }
      if (edit) edit.style.display = '';
      if (save && value && value !== item.text) options.onRename(item, value);
    }
    input.addEventListener('click', function (e) { e.stopPropagation(); });
    input.addEventListener('keydown', function (e) {
      e.stopPropagation();
      if (e.key === 'Enter') { e.preventDefault(); finish(true); }
      else if (e.key === 'Escape') { e.preventDefault(); finish(false); }
    });
    input.addEventListener('blur', function () { finish(true); });
    input.focus();
    input.select();
  }

  function _newLabelSegment(track, key) {
    var segment = document.createElement('span');
    segment.dataset.labelKey = key;
    var labelName = document.createElement('span');
    labelName.className = 'label-segment-name';
    segment.appendChild(labelName);
    var edit = document.createElement('span');
    edit.className = 'label-segment-edit';
    edit.textContent = '✎';
    segment.appendChild(edit);
    segment.addEventListener('click', function (e) {
      e.stopPropagation();
      _startLabelRename(segment);
    });
    segment.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault(); e.stopPropagation(); _startLabelRename(segment);
      }
    });
    track.appendChild(segment);
    return segment;
  }

  function _alignLabelTrackToChart(el, track, chartId) {
    // 先清掉上次宽度下的动态边距，再量取真实布局；getBoundingClientRect 会同步布局。
    track.style.marginLeft = '0px';
    track.style.marginRight = '0px';
    if (!chartId || typeof echarts === 'undefined') return;
    var chartEl = document.getElementById(chartId);
    var chart = chartEl && echarts.getInstanceByDom(chartEl);
    if (!chart || !chartEl.offsetWidth) return;
    var base = track.getBoundingClientRect();
    var chartRect = chartEl.getBoundingClientRect();
    // Label 轨道的左右边界与 ECharts grid 完全重合，而非与图表卡片外框重合。
    var targetLeft = chartRect.left + GRID_PAD.left;
    var targetRight = chartRect.right - GRID_PAD.right;
    var leftGap = Math.max(0, targetLeft - base.left);
    var rightGap = Math.max(0, base.right - targetRight);
    track.style.marginLeft = leftGap + 'px';
    track.style.marginRight = rightGap + 'px';
  }

  // 按 Label ID 增量更新位置/宽度：实时采样继续推动当前段增长，正在输入的节点不销毁。
  // 分段使用轨道右侧独立按钮；点击任意已落盘色块只负责改名，彻底隔离两种操作。
  function renderLabelTimeline(elId, rows, annotations, options) {
    var el = document.getElementById(elId);
    if (!el) return;
    options = options || {};
    if (options.followZoom) {
      _zoomLabelTimeline = { elId: elId, rows: rows,
                             annotations: annotations, options: options };
    }
    el.__labelOptions = options;
    var track = el.querySelector('.label-track');
    if (!track) {
      track = document.createElement('div');
      track.className = 'label-track';
      el.appendChild(track);
    }
    el.style.display = '';

    var split = el.querySelector('.label-split-button');
    if (typeof options.onPin === 'function') {
      if (!split) {
        split = document.createElement('button');
        split.type = 'button';
        split.className = 'label-split-button';
        split.textContent = '＋ 分段';
        split.title = '在当前最新采样时刻切换到下一个 Label';
        split.addEventListener('click', function (e) {
          e.stopPropagation();
          var current = el.__labelOptions;
          if (current && typeof current.onPin === 'function') current.onPin();
        });
        el.appendChild(split);
      }
    } else if (split && split.parentNode) {
      split.parentNode.removeChild(split);
    }

    var items = annotations || [];
    if (!items.length && rows && rows.length && typeof options.onPin === 'function') {
      items = [{ start_ms: rows[0].t_ms, end_ms: null,
                 text: 'Label1', color: '#ef5664' }];
    }
    var empty = track.querySelector('.label-track-empty');
    if (!rows || !rows.length || !items.length) {
      track.querySelectorAll('.label-segment').forEach(function (node) { node.remove(); });
      if (!empty) {
        empty = document.createElement('span');
        empty.className = 'label-track-empty';
        track.appendChild(empty);
      }
      empty.textContent = typeof options.onPin === 'function' ? '等待首个采样点…' : '暂无 Label';
      el.style.display = '';
      return;
    }
    if (empty && empty.parentNode) empty.parentNode.removeChild(empty);

    _alignLabelTrackToChart(el, track, options.alignChartId);

    var existing = {};
    track.querySelectorAll('.label-segment').forEach(function (node) {
      existing[node.dataset.labelKey] = node;
    });
    var keep = {};
    var fullFirst = Number(rows[0].t_ms), fullLast = Number(rows[rows.length - 1].t_ms);
    var fullSpan = Math.max(1, fullLast - fullFirst);
    var displayTimes = _dispIdx
      ? _dispIdx.map(function (i) { return Number(rows[i] && rows[i].t_ms); })
      : rows.map(function (r) { return Number(r && r.t_ms); });
    var indexWindow = options.followZoom
      ? zoomCategoryWindow(displayTimes.length, _dz.start, _dz.end)
      : null;
    var indexSpan = indexWindow ? Math.max(0.000001, indexWindow.end - indexWindow.start) : 0;
    var first = options.followZoom && displayTimes.length
      ? displayTimes[Math.max(0, Math.floor(indexWindow.start))] : fullFirst;
    var last = options.followZoom && displayTimes.length
      ? displayTimes[Math.min(displayTimes.length - 1, Math.ceil(indexWindow.end))] : fullLast;
    var spanMs = Math.max(1, last - first);
    items.forEach(function (a, index) {
      var start = Number(a && a.start_ms);
      var end = a && a.end_ms == null ? fullLast : Number(a && a.end_ms);
      if (!isFinite(start) || !isFinite(end)) return;
      var leftPct, rightPct;
      if (indexWindow) {
        var startIndex = timeToCategoryIndex(displayTimes, start);
        var endIndex = timeToCategoryIndex(displayTimes, end);
        if (startIndex == null || endIndex == null || endIndex <= indexWindow.start ||
            startIndex >= indexWindow.end) return;
        leftPct = (Math.max(indexWindow.start, startIndex) - indexWindow.start) * 100 / indexSpan;
        rightPct = (Math.min(indexWindow.end, endIndex) - indexWindow.start) * 100 / indexSpan;
      } else {
        if (end <= first || start >= last) return;
        leftPct = (Math.max(first, start) - first) * 100 / spanMs;
        rightPct = (Math.min(last, end) - first) * 100 / spanMs;
      }
      var key = String(a.id || ('preview-' + index));
      keep[key] = true;
      var segment = existing[key] || _newLabelSegment(track, key);
      segment.__labelItem = a;
      segment.className = 'label-segment' + (a.end_ms == null ? ' active' : '');
      var left = Math.max(0, Math.min(100, leftPct));
      var right = Math.max(0, Math.min(100, rightPct));
      segment.style.left = left + '%';
      segment.style.width = Math.max(1.5, right - left) + '%';
      segment.style.background = a.color || '#4fc3f7';
      var canRename = !!(a.id && typeof options.onRename === 'function');
      segment.classList.toggle('editable', canRename);
      segment.tabIndex = canRename ? 0 : -1;
      segment.title = canRename ? '点击色块修改名称' : String(a.text || 'Label');
      var labelName = segment.querySelector('.label-segment-name');
      var edit = segment.querySelector('.label-segment-edit');
      if (!segment.querySelector('input') && labelName) labelName.textContent = String(a.text || 'Label');
      if (edit) edit.style.display = canRename ? '' : 'none';
    });
    Object.keys(existing).forEach(function (key) {
      if (!keep[key] && existing[key].parentNode) existing[key].parentNode.removeChild(existing[key]);
    });
    el.style.display = '';
  }

  // ---------------- 事件标注层（2026-08-14 模式1：logcat console.log 叠加） ----------------
  // 把事件按 t_ms 映射到 x 轴类目值，在每张图画竖线标注；
  // 第一张图（FPS）附带文字标签，其余图只画线（避免标签 6 次重复）。
  // v36 修复：类目轴值是采样点时间（rows 的 t_ms 递增，多为整数秒），事件时刻
  // （如 10.4s）未必命中类目 → 此前 Math.round(ev.t_ms/100)/10 映射的类目在
  // ECharts 类目轴中找不到对应值会静默不渲染（大部分事件线丢失）。
  // 改为二分找最近采样点，保证标注线一定落在曲线窗口内。
  // v40：抽成纯函数并导出（window.PerfCharts.nearestCat），便于脱离浏览器做边界断言
  // （见 tests/test_nearest_cat.js）。times 必须是递增的 t_ms 数组。
  // 返回类目轴取值（秒，1 位小数）；times 为空或 ms 无效返回 null。
  function nearestCat(times, ms) {
    if (ms == null || !times || !times.length) return null;
    var cat = function (v) { return Math.round(v / 100) / 10; };
    if (ms <= times[0]) return cat(times[0]);
    if (ms >= times[times.length - 1]) return cat(times[times.length - 1]);
    var lo = 0, hi = times.length - 1;
    while (lo < hi - 1) {
      var mid = (lo + hi) >> 1;
      if (times[mid] <= ms) lo = mid; else hi = mid;
    }
    // 等距时取左侧采样点（与统计栏"落在哪个采样点"口径一致）
    return (ms - times[lo] <= times[hi] - ms) ? cat(times[lo]) : cat(times[hi]);
  }

  function renderEvents(charts, rows, events) {
    if (!events || !events.length || !rows || !rows.length) return;
    var MAX_EV = 200;
    var times = rows.map(function (r) { return r.t_ms; });
    var markers = [];
    events.forEach(function (ev) {
      if (markers.length >= MAX_EV) return;
      if (!ev || ev.t_ms == null) return;
      // 完整 Java/Native 堆栈保留在 *.crash.log 与 events.jsonl 中；图表只画
      // 崩溃主事件和进程状态沿，避免几十条栈帧变成一片红色竖线。
      if (ev.kind === 'crash_log') return;
      var cat = nearestCat(times, ev.t_ms);
      if (cat === null) return;
      var isCrash = ev.kind === 'confirmed_crash' || ev.kind === 'anr' ||
                    ev.kind === 'system_kill';
      var isExit = ev.kind === 'process_exit';
      var isRestart = ev.kind === 'process_restart';
      var isErr = isCrash || ev.level === 'E' || ev.level === 'F';
      var color = isErr ? 'rgba(239,83,80,0.75)' :
                  (isExit ? 'rgba(255,183,77,0.75)' :
                  (isRestart ? 'rgba(79,195,247,0.75)' : 'rgba(79,195,247,0.55)'));
      markers.push({
        xAxis: cat,
        lineStyle: {
          color: color,
          width: 1, type: 'dashed',
        },
        label: {
          show: true,
          formatter: String(ev.text || '').slice(0, 16),
          fontSize: 9,
          color: isErr ? '#ef5350' : (isExit ? '#ffb74d' : '#4fc3f7'),
          position: 'insideEndTop',
        },
      });
    });
    if (!markers.length) return;
    var ids = Object.keys(charts);
    ids.forEach(function (id, idx) {
      var c = charts[id];
      if (!c) return;
      var mk = markers.map(function (m) {
        var o = { xAxis: m.xAxis, lineStyle: m.lineStyle };
        if (idx === 0) o.label = m.label;   // 仅首图带标签
        return o;
      });
      try {
        c.setOption({ series: [{ markLine: { symbol: 'none', silent: true, data: mk } }] });
      } catch (e) {}
    });
  }

  window.PerfCharts = {
    makeChart: makeChart,
    initSortable: initSortable,
    enableLink: enableLink,
    enableClickPin: enableClickPin,
    clearPin: _pinUnlockAll,
    createTimeSliders: createTimeSliders,
    clearTimeSliders: clearTimeSliders,
    renderEvents: renderEvents,
    renderAnnotations: renderAnnotations,
    renderAnnotationList: renderAnnotationList,
    renderLabelTimeline: renderLabelTimeline,
    annotationMarkAreas: annotationMarkAreas,
    nearestCat: nearestCat,   // 纯函数，导出供 tests/test_nearest_cat.js 断言
    pixelToIdx: pixelToIdx,   // v66：grid-aware 像素 → 显示索引（纯函数，供回归测试）
    idxToPixel: idxToPixel,   // v66：显示索引 → grid 内像素（与 pixelToIdx 同一套数学）
    timeToCategoryIndex: timeToCategoryIndex, // v99：Label 时间 → 连续类目索引
    zoomCategoryWindow: zoomCategoryWindow,   // v99：dataZoom 百分比 → 连续类目窗口
    fpsMetric: fpsMetric,     // v76：新 schema 短窗聚合优先、旧数据字段回退（纯函数）
    metricIsFresh: metricIsFresh, // v79：重复 latest 快照不重复进入统计
    setCores: setCores,       // 注入核数（实时看板 /api/status；历史报告 meta 行）
    prepareRows: prepareRows, // 清洗 event 行 + 抽取核数/设备信息（历史报告/导出 HTML 用）
    deviceInfoLines: deviceInfoLines,   // 设备信息 → 结构化行数组（历史看板分行渲染）
    formatDeviceInfo: formatDeviceInfo, // 设备信息 → 一行小字（状态栏；基于 deviceInfoLines）
    setPinData: setPinData,
    setPinHook: setPinHook,   // v52（需求 B）：注册锁定/解锁快照钩子（report.html 用）
    renderAll: renderAll,
    updateStats: updateStats,
    computeStats: computeStats,
    renderSummary: renderSummary,
    // v61：数据完整度（缺数率/原因/区间）+ 图上缺数灰带
    computeCompleteness: computeCompleteness,
    renderCompleteness: renderCompleteness,
    markCompleteness: markCompleteness,
    completenessGrade: completenessGrade,
    _reasonText: _reasonText,   // 纯函数，供错误码文案回归测试
    fpsQualityNote: fpsQualityNote, // FPS 低置信/钳制注记（纯函数）
    axisTooltipHtml: axisTooltipHtml, // 跨图联动时按 dataIndex 重建完整序列 tooltip
    _statText: statText,
  };
})();
