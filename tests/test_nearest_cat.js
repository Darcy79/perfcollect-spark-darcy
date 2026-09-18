/**
 * web/assets/app.js 纯函数最小验证（2026-08-25，二次评估 #3）
 *
 * 只验证 nearestCat（事件标注线的二分查找）——它决定 logcat 事件竖线落在哪个
 * 类目上，越界/相等/未命中都容易写错，且浏览器里出错是"静默不渲染"，很难发现。
 *
 * 不引任何测试框架：把 app.js 当普通脚本执行（它是 IIFE，顶层只挂 window.PerfCharts，
 * document/echarts 仅在函数体内使用 → 无 DOM 也能加载），再对导出的纯函数断言。
 *
 * 运行（项目根目录，二选一）：
 *     bun tests/test_nearest_cat.js
 *     node tests/test_nearest_cat.js
 */
'use strict';

const fs = require('fs');
const path = require('path');

// 无 DOM 环境：造一个 window 壳给 app.js 挂载导出
globalThis.window = globalThis;
const APP_JS = path.join(__dirname, '..', 'web', 'assets', 'app.js');
new Function(fs.readFileSync(APP_JS, 'utf8'))();

const nearestCat = window.PerfCharts && window.PerfCharts.nearestCat;
if (typeof nearestCat !== 'function') {
  console.error('[x] app.js 未导出 window.PerfCharts.nearestCat');
  process.exit(1);
}

let passed = 0;
const failures = [];
function eq(actual, expected, name) {
  if (actual === expected) { passed++; return; }
  failures.push(`${name}: 期望 ${JSON.stringify(expected)}，实际 ${JSON.stringify(actual)}`);
}

// 采样点：0s / 1s / 2s / 3.5s（t_ms），类目轴取值 = ms/1000 保留 1 位小数
const T = [0, 1000, 2000, 3500];

// 空/无效输入
eq(nearestCat([], 500), null, '空数组返回 null');
eq(nearestCat(null, 500), null, 'times 为 null 返回 null');
eq(nearestCat(undefined, 500), null, 'times 为 undefined 返回 null');
eq(nearestCat(T, null), null, 'ms 为 null 返回 null');
eq(nearestCat(T, undefined), null, 'ms 为 undefined 返回 null');

// 单点数组
eq(nearestCat([1000], 0), 1, '单点：左越界夹到唯一点');
eq(nearestCat([1000], 9999), 1, '单点：右越界夹到唯一点');
eq(nearestCat([1000], 1000), 1, '单点：精确命中');

// 左右边界（事件早于首个采样点 / 晚于最后一个采样点 → 夹到端点，不返回 null）
eq(nearestCat(T, -1), 0, '左越界夹到首点');
eq(nearestCat(T, 0), 0, '命中首点');
eq(nearestCat(T, 99999), 3.5, '右越界夹到末点');
eq(nearestCat(T, 3500), 3.5, '命中末点');

// 精确命中中间采样点
eq(nearestCat(T, 1000), 1, '精确命中中间点');
eq(nearestCat(T, 2000), 2, '精确命中中间点2');

// 未命中：取最近的采样点
eq(nearestCat(T, 1200), 1, '偏左 → 取左邻');
eq(nearestCat(T, 1800), 2, '偏右 → 取右邻');
eq(nearestCat(T, 1500), 1, '正中间等距 → 取左邻（口径固定）');
eq(nearestCat(T, 2900), 3.5, '不等距区间内偏右 → 取右邻');
eq(nearestCat(T, 2700), 2, '不等距区间内偏左 → 取左邻');

// 类目取值精度：四舍五入到 0.1s（与 renderAll 的 x 轴生成口径一致）
eq(nearestCat([10440], 10440), 10.4, '类目值保留 1 位小数');
eq(nearestCat([10460], 10460), 10.5, '类目值四舍五入');

// 长序列：二分应稳定命中（顺带防死循环）
const LONG = Array.from({ length: 1000 }, (_, i) => i * 500);   // 0 ~ 499.5s，步长 0.5s
eq(nearestCat(LONG, 250 * 500 + 10), 125, '长序列：偏左取左邻');
eq(nearestCat(LONG, 250 * 500 - 10), 125, '长序列：偏右取右邻');
eq(nearestCat(LONG, 999 * 500), 499.5, '长序列：命中末点');

// ---------------- prepareRows：event 行过滤 + 核数抽取（v41 任务 3） ----------------
const prepareRows = window.PerfCharts && window.PerfCharts.prepareRows;
const setCores = window.PerfCharts && window.PerfCharts.setCores;
if (typeof prepareRows !== 'function' || typeof setCores !== 'function') {
  console.error('[x] app.js 未导出 window.PerfCharts.prepareRows/setCores');
  process.exit(1);
}
{
  const pr = prepareRows([
    { ts: 1.0, event: 'meta', cores: 8 },
    { t_ms: 500, cpu: { cpu_proc_pct: 50 } },
    { ts: 2.0, event: 'target_switch', to: 'com.x' },
  ]);
  eq(pr.rows.length, 1, 'prepareRows：event 行被过滤');
  eq(pr.rows[0].t_ms, 500, 'prepareRows：保留真实采样点');
  eq(pr.cores, 8, 'prepareRows：抽出核数 8');
}
{
  const pr2 = prepareRows([{ t_ms: 0, cpu: { cpu_proc_pct: 10 } }]);
  eq(pr2.rows.length, 1, 'prepareRows：无 meta 时保留采样点');
  eq(pr2.cores, null, 'prepareRows：无 meta 时核数为 null');
}
{
  setCores(12);
  const pr3 = prepareRows([{ t_ms: 0, cpu: {} }]);   // 无 meta → 重置为 null，不沿用上次 12
  eq(pr3.cores, null, 'prepareRows：无 meta 时重置核数（不沿用旧报告）');
}

// ---------------- prepareRows 抽取设备信息 + formatDeviceInfo（v46 优化 2） ----------------
const formatDeviceInfo = window.PerfCharts && window.PerfCharts.formatDeviceInfo;
if (typeof formatDeviceInfo !== 'function') {
  console.error('[x] app.js 未导出 window.PerfCharts.formatDeviceInfo');
  process.exit(1);
}
{
  const pr = prepareRows([
    { ts: 1.0, event: 'meta', cores: 8, device: { model: 'ADT-AN00', market_name: 'Magic3 Pro', cpu_hardware: 'SM8350', cpu_max_freq_mhz: '1804.8', screen_resolution: '1080×2388' } },
    { t_ms: 500, cpu: { cpu_proc_pct: 50 } },
  ]);
  eq(pr.device && pr.device.model, 'ADT-AN00', 'prepareRows：抽出设备信息 model');
  eq(pr.device.market_name, 'Magic3 Pro', 'prepareRows：抽出设备信息市场名');
}
{
  const dev = { model: 'ADT-AN00', market_name: 'Magic3 Pro', cpu_hardware: 'SM8350', cpu_max_freq_mhz: '1804.8', screen_resolution: '1080×2388' };
  const text = formatDeviceInfo(dev, 8);
  eq(text.indexOf('Magic3 Pro') >= 0, true, 'formatDeviceInfo：含市场名');
  eq(text.indexOf('ADT-AN00') >= 0, true, 'formatDeviceInfo：含型号代码');
  eq(text.indexOf('SM8350') >= 0, true, 'formatDeviceInfo：含 CPU 型号');
  eq(text.indexOf('1080×2388') >= 0, true, 'formatDeviceInfo：含分辨率');
  eq(text.indexOf('8 核') >= 0, true, 'formatDeviceInfo：含核数');
}
{
  eq(formatDeviceInfo(null, 8), '', 'formatDeviceInfo：null → 空串');
  eq(formatDeviceInfo({}, 8), '', 'formatDeviceInfo：空对象 → 空串');
  eq(formatDeviceInfo({ model: 'ADT-AN00' }, null), 'ADT-AN00', 'formatDeviceInfo：仅型号，无核数');
}

// ---------------- deviceInfoLines（v47：历史看板设备信息分行分字段） ----------------
const deviceInfoLines = window.PerfCharts && window.PerfCharts.deviceInfoLines;
if (typeof deviceInfoLines !== 'function') {
  console.error('[x] app.js 未导出 window.PerfCharts.deviceInfoLines');
  process.exit(1);
}
{
  // 全字段：设备(市场名+型号) / 芯片(硬件·主频·核数) / 分辨率 三行，顺序固定
  const dev = { model: 'ADT-AN00', market_name: 'Magic3 Pro', cpu_hardware: 'SM8350', cpu_max_freq_mhz: '1804.8', screen_resolution: '1080×2388' };
  const lines = deviceInfoLines(dev, 8);
  eq(lines.length, 3, 'deviceInfoLines：全字段 → 3 行');
  eq(lines[0].label, '设备', 'deviceInfoLines：第 1 行标签=设备');
  eq(lines[0].value, 'Magic3 Pro (ADT-AN00)', 'deviceInfoLines：市场名+型号组合');
  eq(lines[1].label, '芯片', 'deviceInfoLines：第 2 行标签=芯片');
  eq(lines[1].value, 'SM8350 · 1804.8MHz · 8 核', 'deviceInfoLines：芯片行硬件·主频·核数');
  eq(lines[2].label, '分辨率', 'deviceInfoLines：第 3 行标签=分辨率');
  eq(lines[2].value, '1080×2388', 'deviceInfoLines：分辨率值');
}
{
  // 市场名与型号代码相同 → 不重复附 "(代码)"
  const lines = deviceInfoLines({ model: 'ADT-AN00', market_name: 'ADT-AN00' }, null);
  eq(lines.length, 1, 'deviceInfoLines：同名不附型号 → 仅设备行');
  eq(lines[0].value, 'ADT-AN00', 'deviceInfoLines：市场名==型号时不重复');
}
{
  // 无市场名 → 设备行只显示型号代码
  const lines = deviceInfoLines({ model: 'PKC110' }, null);
  eq(lines.length, 1, 'deviceInfoLines：无市场名仅 1 行');
  eq(lines[0].value, 'PKC110', 'deviceInfoLines：无市场名 → 用型号');
}
{
  // 缺哪个跳哪个：无分辨率 → 无第 3 行；无主频 → 芯片行不含 MHz
  const lines = deviceInfoLines({ model: 'ADT-AN00', cpu_hardware: 'SM8350' }, 8);
  eq(lines.length, 2, 'deviceInfoLines：缺分辨率 → 2 行');
  eq(lines[1].value, 'SM8350 · 8 核', 'deviceInfoLines：芯片行缺主频跳主频');
}
{
  // 核数仅随芯片信息出现：无芯片信息时不孤零零出 "8 核" 行
  const lines = deviceInfoLines({ model: 'ADT-AN00', screen_resolution: '1080×2388' }, 8);
  eq(lines.length, 2, 'deviceInfoLines：无芯片信息 → 设备+分辨率 2 行');
  eq(lines[1].label, '分辨率', 'deviceInfoLines：无芯片行，第 2 行=分辨率');
}
{
  // device 无效 → 空数组（老数据无 device 时 report.html 不渲染设备块）
  eq(deviceInfoLines(null, 8).length, 0, 'deviceInfoLines：null → []');
  eq(deviceInfoLines({}, 8).length, 0, 'deviceInfoLines：空对象 → []');
  eq(deviceInfoLines(undefined, null).length, 0, 'deviceInfoLines：undefined → []');
}
{
  // formatDeviceInfo 基于 deviceInfoLines 重组，一行版语义一致（index.html 状态栏不变）
  const dev = { model: 'ADT-AN00', market_name: 'Magic3 Pro', cpu_hardware: 'SM8350', cpu_max_freq_mhz: '1804.8', screen_resolution: '1080×2388' };
  eq(formatDeviceInfo(dev, 8),
     'Magic3 Pro (ADT-AN00) · SM8350 · 1804.8MHz · 8 核 · 1080×2388',
     'formatDeviceInfo：基于 lines 重组为一行');
}

// ---------------- computeCompleteness（v61：数据完整度/缺数率/缺数区间） ----------------
const computeCompleteness = window.PerfCharts && window.PerfCharts.computeCompleteness;
if (typeof computeCompleteness !== 'function') {
  console.error('[x] app.js 未导出 window.PerfCharts.computeCompleteness');
  process.exit(1);
}
{
  // 空输入
  const c0 = computeCompleteness([]);
  eq(c0.total, 0, 'computeCompleteness：空输入 total=0');
  eq(c0.worst, null, 'computeCompleteness：空输入无最差指标');
  eq(c0.metrics.fps.missing, 0, 'computeCompleteness：空输入缺数 0');
}
{
  // 事故形态（run 20260911_162353 抽象）：6 点里 FPS 只有 2 点有值
  const rows = [
    { t_ms: 0,    fps: { fps: 59.8, frame_p50_ms: 16.7 }, mem: { pss_kb: 100 }, therm: { temp_c: 30 } },
    { t_ms: 1000, fps: { fps: null, error: 'probe_fail' } },
    { t_ms: 2000, fps: { fps: null, error: 'probe_fail' } },
    { t_ms: 3000, fps: { fps: null, error: 'no_layer' } },
    { t_ms: 4000, fps: { fps: 59.9, frame_p50_ms: 16.8 } },
    { t_ms: 5000, fps: { fps: 59.8, frame_p50_ms: 16.7 } },
  ];
  const c = computeCompleteness(rows);
  eq(c.total, 6, 'computeCompleteness：总点数');
  eq(c.metrics.fps.missing, 3, 'computeCompleteness：FPS 缺数 3');
  eq(c.metrics.fps.pct, 50, 'computeCompleteness：FPS 缺数率 50%');
  eq(c.metrics.fps.reasons.probe_fail, 2, 'computeCompleteness：区分 probe_fail');
  eq(c.metrics.fps.reasons.no_layer, 1, 'computeCompleteness：区分 no_layer');
  eq(c.metrics.fps.gaps.length, 1, 'computeCompleteness：连续缺数合并为 1 段');
  eq(c.metrics.fps.gaps[0].from, 1, 'computeCompleteness：缺数段起点 1.0s');
  eq(c.metrics.fps.gaps[0].to, 3, 'computeCompleteness：缺数段终点 3.0s');
  eq(c.metrics.fps.gaps[0].n, 3, 'computeCompleteness：缺数段含 3 点');
  eq(c.metrics.mem.missing, 5, 'computeCompleteness：mem 缺数 5（仅首点有值）');
  eq(c.metrics.mem.reasons.no_value, 5, 'computeCompleteness：无错误码归为 no_value');
  eq(c.metrics.temp.missing, 5, 'computeCompleteness：温度同理');
  eq(c.worst, 'cpu', 'computeCompleteness：最差指标取缺数率最高（cpu 字段全缺=100%）');
}
{
  // 全有值 → 无缺数（报告顶部不显示卡片）
  const rows = [
    { t_ms: 0, fps: { fps: 60, frame_p50_ms: 16.7 }, cpu: { cpu_proc_pct: 10 }, mem: { pss_kb: 1 },
      net: { rx_kbps: 1, tx_kbps: 1 }, therm: { temp_c: 30 } },
    { t_ms: 1000, fps: { fps: 60, frame_p50_ms: 16.7 }, cpu: { cpu_proc_pct: 10 }, mem: { pss_kb: 1 },
      net: { rx_kbps: 1, tx_kbps: 1 }, therm: { temp_c: 30 } },
  ];
  const c = computeCompleteness(rows);
  eq(c.metrics.fps.missing, 0, 'computeCompleteness：完整数据缺数 0');
  eq(c.metrics.fps.gaps.length, 0, 'computeCompleteness：完整数据无缺数段');
  eq(c.worst_pct, 0, 'computeCompleteness：完整数据 worst_pct=0');
}
{
  // 首尾缺数（开区间）与网络"任一方向有值即算有值"
  const rows = [
    { t_ms: 0, net: { rx_kbps: null, tx_kbps: null } },
    { t_ms: 1000, net: { rx_kbps: 5, tx_kbps: null } },
    { t_ms: 2000, net: {} },
  ];
  const c = computeCompleteness(rows);
  eq(c.metrics.net.missing, 2, 'computeCompleteness：网络缺数 2（任一方向有值即有值）');
  eq(c.metrics.net.gaps.length, 2, 'computeCompleteness：首尾各 1 段缺数');
  eq(c.metrics.net.gaps[0].from, 0, 'computeCompleteness：首段起点 0s');
  eq(c.metrics.net.gaps[1].from, 2, 'computeCompleteness：尾段起点 2s');
}
{
  // 分级阈值（>5% 黄、>20% 红）
  const g = window.PerfCharts.completenessGrade;
  eq(g(0), 'ok', 'completenessGrade：0% 绿');
  eq(g(5), 'ok', 'completenessGrade：5% 仍绿');
  eq(g(20), 'warn', 'completenessGrade：20% 黄');
  eq(g(20.1), 'bad', 'completenessGrade：>20% 红');
}

// ---------------- renderCompleteness 文案映射（v70：缺数原因要说人话） ----------------
{
  // 最小 DOM stub：renderCompleteness 只用到 innerHTML/style/querySelector/classList
  const els = {};
  globalThis.document = {
    getElementById: function (id) {
      if (!els[id]) {
        els[id] = {
          innerHTML: '', style: {},
          querySelector: function () { return { setAttribute: function () {}, addEventListener: function () {} }; },
          classList: { toggle: function () { return true; } },
        };
      }
      return els[id];
    },
  };
  const rows = [
    { t_ms: 0, mem: { pid: null, pss_kb: null, error: 'no_pid' } },   // 进程未解析到
    { t_ms: 1000 },                                                    // 连对象都没有 → 采样未就绪
  ];
  const c = window.PerfCharts.computeCompleteness(rows);
  window.PerfCharts.renderCompleteness('cmpj', c);
  const html = els['cmpj'].innerHTML;
  eq(html.indexOf('进程未知(未解析到)') >= 0, true, '文案：no_pid → 「进程未知(未解析到)」');
  eq(html.indexOf('未取到值(无原因码)') >= 0, true, '文案：无错误码 → 「未取到值(无原因码)」');
  eq(html.indexOf('该指标无值') >= 0, false, '文案：不再出现含糊的「该指标无值」');
  eq(html.indexOf('cmpl-toggle') >= 0, true, '结构：输出可折叠的标题行按钮');
  eq(html.indexOf('cmpl-body') >= 0, true, '结构：输出可折叠的内容区');

  // v71：历史数据（v70 前）内存/网络缺数不带码，但 pid=None 可推断为"进程未知"
  const legacy = [
    { t_ms: 0, mem: { pid: null, pss_kb: null, vmrss_kb: null },   // 老格式：无 error 字段
      net: { pid: null, rx_kbps: null, tx_kbps: null } },
    { t_ms: 1000, mem: { pid: 13694, pss_kb: 900000 }, net: { pid: 13694, rx_kbps: 1 } },
  ];
  const c2 = window.PerfCharts.computeCompleteness(legacy);
  eq(c2.metrics.mem.reasons.no_pid, 1, '历史数据：内存 pid=None → 推断为进程未知');
  eq(c2.metrics.net.reasons.no_pid, 1, '历史数据：网络 pid=None → 推断为进程未知');
  // 有 pid 但无值（异常形态）→ 不推断为进程未知，仍归"无原因码"
  const odd = [{ t_ms: 0, mem: { pid: 13694, pss_kb: null } }];
  eq(window.PerfCharts.computeCompleteness(odd).metrics.mem.reasons.no_value, 1,
     '有 pid 无值 → 不误推断为进程未知');
}

// ---------------- Pin 蓝线像素 ↔ 索引换算（v66：扣除 ECharts grid 边距） ----------------
{
  const pixelToIdx = window.PerfCharts.pixelToIdx;
  const idxToPixel = window.PerfCharts.idxToPixel;
  eq(typeof pixelToIdx, 'function', 'pixelToIdx：已导出纯函数');
  eq(typeof idxToPixel, 'function', 'idxToPixel：已导出纯函数');

  eq(pixelToIdx(100, 1500, 1505, 56, 24), 47, 'pixelToIdx：长报告左段');
  eq(pixelToIdx(640, 1500, 1505, 56, 24), 619, 'pixelToIdx：长报告中段');
  eq(pixelToIdx(1430, 1500, 1505, 56, 24), 1455, 'pixelToIdx：长报告右段');
  eq(pixelToIdx(56, 1500, 1505, 56, 24), 0, 'pixelToIdx：绘图区左边界');
  eq(pixelToIdx(1476, 1500, 1505, 56, 24), 1504, 'pixelToIdx：绘图区右边界');
  eq(pixelToIdx(0, 1500, 1505, 56, 24), 0, 'pixelToIdx：越界左侧 clamp');
  eq(pixelToIdx(1500, 1500, 1505, 56, 24), 1504, 'pixelToIdx：越界右侧 clamp');

  eq(Math.abs(idxToPixel(47, 1500, 1505, 56, 24) - 100.4) < 0.5, true,
     'idxToPixel：索引 47 约为 100.4px');
  eq(idxToPixel(0, 1500, 1505, 56, 24), 56, 'idxToPixel：首索引在左边界');
  eq(idxToPixel(1504, 1500, 1505, 56, 24), 1476, 'idxToPixel：末索引在右边界');

  [0, 47, 619, 1455, 1504].forEach((i) => {
    eq(pixelToIdx(idxToPixel(i, 1500, 1505, 56, 24), 1500, 1505, 56, 24), i,
       '像素索引往返一致：' + i);
  });

  eq(pixelToIdx(100, 1500, 0, 56, 24), 0, 'pixelToIdx：n=0 退化输入');
  eq(pixelToIdx(100, 40, 10, 56, 24), 0, 'pixelToIdx：usable<=0 退化输入');
}

// ---------------- FPS 短窗聚合读取（v76：新 schema 优先、旧数据兼容） ----------------
{
  const metric = window.PerfCharts.fpsMetric;
  eq(typeof metric, 'function', 'fpsMetric：已导出');
  const row = {
    fps: { jank_rate: 0, frame_p95_ms: 16.7, frame_max_ms: 20 },
    fps_window_summary: { jank_rate: 0.1667, frame_p95_peak_ms: 80, frame_max_peak_ms: 120 },
  };
  eq(metric(row, 'jank_rate'), 0.1667, 'fpsMetric：优先短窗加权 Jank');
  eq(metric(row, 'frame_p95_ms'), 80, 'fpsMetric：优先短窗 P95 峰值');
  eq(metric(row, 'frame_max_ms'), 120, 'fpsMetric：优先短窗 Max 峰值');
  eq(metric({ fps: { jank_rate: 0.25 } }, 'jank_rate'), 0.25,
     'fpsMetric：旧数据回退顶层字段');
  eq(metric({}, 'jank_rate'), null, 'fpsMetric：缺失数据返回 null');

  const stats = window.PerfCharts.computeStats([
    { t_ms: 1000, fps: { fps: 50, jank_rate: 0, frame_p95_ms: 16.7 },
      fps_window_summary: { window_count: 2, jank_count: 5, jank_total: 30,
        jank_rate: 0.1667, frame_p95_peak_ms: 80 } },
    { t_ms: 2000, fps: { fps: 60, jank_rate: 0, frame_p95_ms: 16.7 },
      fps_window_summary: { window_count: 2, jank_count: 1, jank_total: 10,
        jank_rate: 0.1, frame_p95_peak_ms: 40 } },
  ]);
  eq(stats.jank_avg, 15, 'computeStats：Jank 按帧数全局加权');
  eq(stats.ft_p95_avg, 60, 'computeStats：短窗 P95 峰值再按报告点求均值');
  eq(stats.fps_windowed, true, 'computeStats：标记短窗 schema');
  eq(stats.jank_frame_weighted, true, 'computeStats：标记 Jank 帧加权口径');

  const oldStats = window.PerfCharts.computeStats([
    { t_ms: 1000, fps: { fps: 60, jank_rate: 0.1, frame_p95_ms: 16.7 } },
    { t_ms: 2000, fps: { fps: 60, jank_rate: 0.3, frame_p95_ms: 33.3 } },
  ]);
  eq(oldStats.jank_avg, 20, 'computeStats：旧数据保持逐点 Jank 均值');
  eq(oldStats.ft_p95_avg, 25, 'computeStats：旧数据保持逐点 P95 均值');
  eq(oldStats.fps_windowed, false, 'computeStats：旧数据不误标短窗 schema');
  eq(oldStats.jank_frame_weighted, false, 'computeStats：旧数据不误标帧加权');
}

// ---------------- 指标新鲜度（v79：统计排除重复 latest） ----------------
{
  const isFresh = window.PerfCharts.metricIsFresh;
  eq(typeof isFresh, 'function', 'metricIsFresh：已导出');
  eq(isFresh({}, 'mem'), true, 'metricIsFresh：旧数据默认按新采样兼容');
  eq(isFresh({ metric_meta: { mem: { is_reused: false } } }, 'mem'), true,
     'metricIsFresh：明确的新采样保留');
  eq(isFresh({ metric_meta: { mem: { is_reused: true } } }, 'mem'), false,
     'metricIsFresh：复用快照被排除');

  const stats = window.PerfCharts.computeStats([
    { t_ms: 1000, cpu: { cpu_proc_pct: 10 }, mem: { pss_kb: 102400 }, therm: { temp_c: 30 },
      metric_meta: { cpu: { is_reused: false }, mem: { is_reused: false }, therm: { is_reused: false } } },
    { t_ms: 2000, cpu: { cpu_proc_pct: 90 }, mem: { pss_kb: 102400 }, therm: { temp_c: 30 },
      metric_meta: { cpu: { is_reused: false }, mem: { is_reused: true }, therm: { is_reused: true } } },
    { t_ms: 3000, cpu: { cpu_proc_pct: 20 }, mem: { pss_kb: 307200 }, therm: { temp_c: 40 },
      metric_meta: { cpu: { is_reused: false }, mem: { is_reused: false }, therm: { is_reused: false } } },
  ]);
  eq(stats.cpu_avg, 40, 'computeStats：CPU 使用三个新样本');
  eq(stats.pss_avg, 200, 'computeStats：内存复用点不重复加权');
  eq(stats.pss_peak, 300, 'computeStats：内存峰值仍取新采样最大值');
  eq(stats.temp_avg, 35, 'computeStats：温度复用点不重复加权');
  eq(stats.freshness_aware, true, 'computeStats：标记新鲜度 schema');

  const oldStats = window.PerfCharts.computeStats([
    { t_ms: 1000, mem: { pss_kb: 102400 } },
    { t_ms: 2000, mem: { pss_kb: 307200 } },
  ]);
  eq(oldStats.pss_avg, 200, 'computeStats：旧 JSONL 仍统计全部报告点');
  eq(oldStats.freshness_aware, false, 'computeStats：旧 JSONL 不误标新鲜度 schema');
}

// ---------------- 隐藏图表隔离（v88：FPS/帧时间全缺不中断报告） ----------------
{
  const oldDocument = globalThis.document;
  const cards = {};
  ['chart-fps', 'chart-frametime', 'chart-cpu', 'chart-mem', 'chart-net', 'chart-temp']
    .forEach((id) => { cards[id] = { parentElement: { style: {} } }; });
  globalThis.document = { getElementById: (id) => cards[id] || null };

  function fakeChart() {
    return {
      setOptionCalls: 0,
      resizeCalls: 0,
      setOption() { this.setOptionCalls++; },
      resize() { this.resizeCalls++; },
    };
  }
  const charts = {
    fps: fakeChart(), frametime: fakeChart(), cpu: fakeChart(),
    mem: fakeChart(), net: fakeChart(), temp: fakeChart(),
  };
  const rows = [0, 1].map((i) => ({
    t_ms: i * 1000,
    fps: { error: 'gfx_unavailable' },
    cpu: { cpu_total_pct: 30, cpu_proc_pct: 10 },
    mem: { pss_kb: 102400 },
    net: { rx_kbps: 1, tx_kbps: 1 },
    therm: { temp_c: 35, power_w: 2 },
  }));
  window.PerfCharts.renderAll(charts, rows, { zoom: true });

  eq(cards['chart-fps'].parentElement.style.display, 'none', '全缺 FPS：隐藏 FPS 卡片');
  eq(cards['chart-frametime'].parentElement.style.display, 'none', '全缺帧时间：隐藏帧时间卡片');
  eq(charts.fps.setOptionCalls, 0, '全缺 FPS：不对 0×0 隐藏图调用 setOption');
  eq(charts.frametime.setOptionCalls, 0, '全缺帧时间：不对 0×0 隐藏图调用 setOption');
  eq(charts.fps.resizeCalls, 0, '全缺 FPS：不 resize 隐藏图');
  eq(charts.frametime.resizeCalls, 0, '全缺帧时间：不 resize 隐藏图');
  eq(charts.cpu.setOptionCalls > 0, true, 'FPS 全缺时 CPU 仍正常渲染');
  eq(charts.mem.setOptionCalls > 0, true, 'FPS 全缺时内存仍正常渲染');
  eq(charts.net.setOptionCalls > 0, true, 'FPS 全缺时网络仍正常渲染');
  eq(charts.temp.setOptionCalls > 0, true, 'FPS 全缺时温度仍正常渲染');
  globalThis.document = oldDocument;

  eq(window.PerfCharts._reasonText('gfx_unavailable'),
     'gfxinfo 不支持该应用，已回退 SurfaceFlinger',
     'gfx_unavailable：完整度卡片显示中文回退说明');
}

// ---------------- FPS 质量标记可见性（v90） ----------------
{
  const qualityNote = window.PerfCharts.fpsQualityNote;
  eq(qualityNote([{ fps: { fps: 60 } }]), '', 'FPS 质量正常：不增加噪声');
  eq(qualityNote([
    { fps: { fps: 90, fps_warn: 'low_frames', fps_clamped: true } },
    { fps: { fps: 80, fps_warn: 'low_frames' } },
  ]), '  ⚠低帧数低置信 2 点 · FPS钳制 1 点',
  'FPS 质量异常：汇总低置信与钳制点');
  eq(qualityNote([
    { fps: { fps: 90, fps_warn: 'low_frames', fps_clamped: true },
      metric_meta: { fps: { is_reused: true } } },
  ]), '', 'FPS 质量标记：不重复统计复用快照');
}

// ---------------- 长报告悬停稳定性（v91 / 前端 v72） ----------------
{
  const oldDocument = globalThis.document;
  const cards = {};
  ['chart-fps', 'chart-frametime', 'chart-cpu', 'chart-mem', 'chart-net', 'chart-temp']
    .forEach((id) => { cards[id] = { parentElement: { style: {} } }; });
  globalThis.document = { getElementById: (id) => cards[id] || null };

  function captureChart() {
    return {
      options: [],
      setOption(opt) { this.options.push(opt); },
      resize() {},
    };
  }
  const charts = {
    fps: captureChart(), frametime: captureChart(), cpu: captureChart(),
    mem: captureChart(), net: captureChart(), temp: captureChart(),
  };
  const rows = [{
    t_ms: 1000,
    fps: { fps: 60, jank_rate: 0, frame_p50_ms: 16.7, frame_p95_ms: 16.8, frame_max_ms: 17 },
    cpu: { cpu_total_pct: 30, cpu_proc_pct: 50 },
    mem: { pss_kb: 102400, vmrss_kb: 204800 },
    net: { rx_kbps: 1, tx_kbps: 1 },
    therm: { temp_c: 35, power_w: 2 },
  }];
  window.PerfCharts.renderAll(charts, rows, { zoom: true });
  const fpsOption = charts.fps.options[0];
  eq(fpsOption.series.every((s) => s.emphasis && s.emphasis.disabled === true), true,
     '长报告悬停：线系列禁用默认 emphasis 重画');
  eq(fpsOption.tooltip.transitionDuration, 0,
     '长报告悬停：tooltip 不做跨图位置过渡');
  eq(fpsOption.tooltip.axisPointer.animation, false,
     '长报告悬停：联动白线不做延迟动画');
  globalThis.document = oldDocument;
}

if (failures.length) {
  console.error(`[x] 断言失败 ${failures.length} 条（通过 ${passed}）：`);
  failures.forEach((f) => console.error('    - ' + f));
  process.exit(1);
}
console.log(`[+] 全部断言通过（${passed} 条）`);
