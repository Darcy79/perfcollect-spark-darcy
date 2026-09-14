# CHANGELOG — 自研 PerfDog 变更日志

> **接手项目先读本文件最新 1~2 条 + `AGENTS.md`（协作规约）**，不必通读代码。
> 每条格式：`版本 | 日期 | 负责 | 改动文件 | 为什么 | 影响面`。
> 维护人：主会话（每次提交同步追加；历史条目由版本记录回溯整理）。

---

## 当前状态（2026-09-11，v71）

| 项 | 值 |
|---|---|
| 前端资源版本 | **v65**（`web/index.html` + `web/report.html` 各 3 处 `?v=`） |
| 测试 | Python **168** 条 + JS **94** 断言（全绿） |
| 采集环境 | Windows + adb 真机（荣耀 ADT-AN00 Magic3 Pro / OPPO；详见 `devices.md`） |
| 工具链 | `uv`（Python）+ `bun`（JS 测试）+ `adb`；路径见 `AGENTS.md` §4 |

**未完成 / 待验证**

- 代码（非阻塞）：report.html 内联 JS 抽离、main.py 可测试化、web.py 18 端点端到端测试、apk_label 二进制解析模糊测试；前端尚未展示 `fps_clamped` / `fps_warn`（当前只在 jsonl 与 CSV/XLSX 里）
- 待真人参与：真机矩阵（微信多开 / 分屏、高刷切换、30min+ 长测）；官方 PerfDog 对拍（**先出《口径差异白皮书》**，口径素材见 `指标说明.md`「九」）；tag 打包验证 CI
- 待真机确认：开小游戏时 appbrand 进程的 `comm`/`cmdline` 实测值；OPPO 及其他品牌 `comm` 截断方向（首 15 / 末 15）；微信多开时 appbrand1/2 能否区分

---

## v71（2026-09-11 · 主会话）—— 历史数据缺数原因推断（老报告也能判读）

- **改动**：`web/assets/app.js`（内存/网络的 `err` 取值函数增加**历史数据推断**：v70 之前
  这两个指标缺数不带 `error` 字段，此时若该点 `pid == null`（必然没解析到进程）→ 归因为
  `no_pid`；`r.mem`/`r.net` 对象本身不存在（首点）→ 仍归 `no_value`；有 pid 但无值 → 不误推断；
  兜底文案 `no_value` 由「未取到值(采样未就绪)」改为 **「未取到值(无原因码)」**，并在展开区
  说明里解释该类的两种来源）；`web/index.html` + `web/report.html`（资源版本 v64 → **v65**）；
  `tests/test_nearest_cat.js`（+3 断言 → 94）
- **为什么**：v70 让**新采集**带上原因码，但用户手上已有的历史报告（含出问题的
  `20260911_162353`）仍是含糊的「该指标无值」。其中内存/网络的 `pid=None` 是**可推断**的——
  没解析到进程才会没值，据此还原原因，老报告即刻可判读
- **影响面**：纯展示层，不重新写数据；推断只在"缺数且无码"时生效，有值点与带码点不受影响
- **验证**：① JS 断言 91 → **94 全绿**（含"有 pid 无值不得误推断"的反例）；② 用历史事故数据
  实测渲染：内存 `进程未知(未解析到) 30 · 未取到值(无原因码) 11`（旧版为笼统的「该指标无值 41」）、
  网络 `进程未知(未解析到) 34 · 未取到值(无原因码) 7`、CPU `进程未知 31 · 读取失败 9`、
  FPS `无渲染层(不在前台) 51`；温度无 pid 字段不可推断，如实显示「未取到值(无原因码) 40」；
  ③ 重新导出留档验证报告（`【验证】报告数据完整度效果-20260911_162353.html`）

## v70（2026-09-11 · 主会话）—— 补全缺数原因码：不再出现含糊的「该指标无值」

- **触发**：用户问"该指标无值 2 / 该指标无值 1 是什么意思"。核查发现这是**信息缺口**而非
  正常分类：`mem.py` / `network.py` / `thermal.py` 在「进程未解析到」「读取失败」时**只返回
  空值、不写 `error` 字段**（只有 `cpu.py` / `fps.py` 写了），前端只能兜底显示「该指标无值」——
  run `20260911_162353` 里内存 41 个缺数点、网络 41、温度 40 **全部**落在该兜底分类，
  看不出是链路问题还是进程问题
- **改动**：`collector/metrics/mem.py`（pid 为 None → `error: "no_pid"`；dumpsys 抛异常或
  拿到输出但两个兜底都解析不出 PSS → `error: "read_fail"`；**节流点不带码**）；
  `collector/metrics/network.py`（pid 缺失 → `no_pid`；`/proc/<pid>/net/dev` 读取失败 →
  `read_fail`；首点无基准仍不带码）；`collector/metrics/thermal.py`（温度完全读不到 →
  `read_fail`；超量程仍报自己的 `temperature_out_of_range`）；
  `web/assets/app.js`（`COMPLETENESS_METRICS` 给 内存/网络/温度 补 `err` 取值函数——否则
  采集端补了码前端也不读；文案改为 `no_pid`→「进程未知(未解析到)」、`no_value`→
  「未取到值(采样未就绪)」、新增 `temperature_out_of_range`→「温度超量程」）；
  `web/index.html` + `web/report.html`（资源版本 v63 → **v64**）；
  `tests/test_metric_error_codes.py` 新建（12 用例）、`tests/test_nearest_cat.js`（+5 断言）
- **影响面**：**新采集**的缺数点原因可直接判读；**历史数据不变**（那批 `no_value` 仍是
  兜底分类，无法追溯）；`main.py` 的断连/缺数告警（`channel_alert`）现在会把内存/网络/温度
  的缺数一并计入 `err_count`——**告警更灵敏也更准确**（此前这三个指标的缺数完全不计入），
  阈值仍是「3 个指标带 error 记 missing、≥4 记 disconnect」且状态沿去重，不会刷屏
- **验证**：① Python 测试 156 → **168 全绿**（新增：失败必带码、正常路径不带码、节流点不带码、
  首点不带码、超量程保留自己的码）；② JS 断言 86 → **91 全绿**（含文案映射：不再出现
  「该指标无值」）；③ **离线端到端**：合成一份"新格式"数据（各指标带 `probe_fail`/
  `no_pid`/`read_fail`）→ 导出报告 → Edge headless 渲染，卡片文本实测：
  `内存 4/10（40.0%）进程未知(未解析到) 3 · 未取到值(采样未就绪) 1`、
  `温度 2/10（20.0%）未取到值(采样未就绪) 1 · 读取失败 1`、`FPS … 链路读取失败 3 ·
  无渲染层(不在前台) 2`，`channel_alert` 事件行未污染采样点；④ **真机抽检未完成**：
  执行时设备已从 `adb devices` 消失（`USB Composite Device` 在但无 ADB 接口），采集器如实
  报「未检测到已连接的设备」，故本轮只做离线验证，待设备恢复后按长测流程补测

## v69（2026-09-11 · 主会话）—— 「数据完整度」改为可折叠卡片（默认收起一行 + 展开/收起动效）

- **改动**：`web/assets/app.js`（`renderCompleteness` 输出改为 `.cmpl-toggle` 标题行按钮 +
  `.cmpl-body` 内容区两段结构：标题行 = 严重度徽标 + 一句话缩略摘要 + 旋转箭头；点击切换
  `.cmpl-open`；**始终默认收起**，不记忆偏好——避免"展开过一次后每份报告都铺开"）；
  `web/assets/style.css`（新增 `.cmpl-toggle`（整行可点按钮）/`.cmpl-toggle-text`（缩略超长
  省略）/`.cmpl-arrow`（`transition: transform .35s`，展开时 `rotate(90deg)`）/
  `.cmpl-body`（`display:grid; grid-template-rows:0fr`，展开态 `1fr`，`transition .35s ease`）/
  `.cmpl-body-inner`（`overflow:hidden; min-height:0`）/`.cmpl-content`；容器圆角裁剪）；
  `web/index.html` + `web/report.html`（资源版本 v62 → **v63**）
- **为什么**：用户反馈"这个版块常驻的话有点占地方，能不能做成缩略、可展开可收起，精简显示，
  展开和收起都加动效"
- **影响面**：纯展示层，数据结构与导出字段不变；有缺数时卡片**常驻只占一行（41px）**，
  点开才铺开详情（宽屏 284px / 520px 窄屏 457px）；动效用 `grid-template-rows 0fr↔1fr`
  过渡（内容高度自适应，无需 JS 量高）；浏览器不支持该过渡时退化为瞬时展开（功能不受影响）
- **验证**：① JS 断言 86 条全绿；② **Edge headless 三态实测**（导出报告，注入测量脚本后
  `dump-dom`）：1600px 窗口 `收起 41px → 展开 284px（内容 243px）→ 再收起 41px`，520px 窗口
  `41 → 457（内容 416）→ 41`，**三态所有元素零溢出**；展开态箭头 transform =
  `matrix(0,1,-1,0,0,0)`（= rotate 90°）；收起态过渡属性实测 `grid-template-rows/0.35s`、
  箭头 `0.35s`；③ 重新导出验证报告确认含折叠结构

## v68（2026-09-11 · 主会话）—— 修「数据完整度」卡片排版与文字截断

- **改动**：`web/assets/style.css`（完整度卡片样式**全部改用 `cmpl-` 前缀**；指标行改三列
  grid 对齐 `标签 4.5em / 缺数率 minmax(9.5em,11em) / 原因 1fr`；摘要·原因·说明一律
  `overflow-wrap:anywhere` 允许换行；缺数区间从一行文本改为 **chip 样式**；新增
  `≤640px` 媒体查询：原因整行下移）；`web/assets/app.js`（`renderCompleteness` 输出结构同步
  改类名，标题与摘要拆成两行）；`web/index.html` + `web/report.html`（资源版本 v61 → **v62**）
- **为什么**：用户反馈"数据完整度版块的排版和显示需要优化，目前文字显示不全"。**根因是类名
  冲突**——新卡片用了 `.cmp-head` / `.cmp-title` / `.cmp-row`，而这三个类名早已被「双报告对比
  面板」（`style.css` 下方 `.compare-panel .cmp-*`）占用；同特异性下后定义者胜出，于是卡片被
  套上对比面板的 **4 列 grid（1.1fr 1fr 1fr 0.7fr）+ 每行下边框 + align-items:center**，
  而我只放了 3 个子元素 → 布局错位；再叠加 `.cmp-r` 的 `white-space:nowrap` +
  `text-overflow:ellipsis` → **原因文字被省略号截断**（"文字显示不全"由此而来）
- **影响面**：纯展示层，数据结构与导出字段不变；`cmpl-` 与对比面板 `cmp-` 彻底分离，两端
  样式不再互相污染；前端资源版本 v61 → **v62**（浏览器需强刷一次）
- **验证**：① JS 断言 86 条全绿；② **Edge headless 真实布局测量**（导出报告，1600 / 900 /
  520px 三档窗口）：卡片内**所有元素 `scrollWidth == clientWidth`（零溢出）**，原因文本
  （如"无渲染层(不在前台) 51 · 该指标无值 1"）完整可见；窄屏 520px 下摘要自动折成两行、
  原因整行展示；③ 重新导出验证报告确认含 `cmpl-*`、无旧 `cmp-r` 残留；④ `grep` 确认
  `style.css` 中 `.cmp-*` 已只属于对比面板

## v67（2026-09-11 · 主会话）—— 报告页「数据完整度」（缺数率 / 缺数原因 / 缺数区间）

- **改动**：`web/assets/app.js`（新增 `computeCompleteness()` 逐指标统计缺数点数、缺数率、
  错误码分布与**连续缺数区间**；`renderCompleteness()` 渲染顶部完整度卡片（分级：≤5% 绿 /
  ≤20% 黄 / >20% 红）；`markCompleteness()` 在 FPS 图用灰带标出缺数区间（端点经 `nearestCat`
  吸附到合法类目，无缺数时清空避免跨报告残留）；`updateStats()` 的 FPS 统计栏追加缺数构成
  注记，`probe_fail`→「链路读取失败」与 `no_layer`→「无渲染层(不在前台)」分开呈现）；
  `web/assets/style.css`（`.completeness` / `.cmp-*` 样式）；`web/report.html`（新增
  `#report-completeness` 容器 + 调用 + hint-card 补"曲线断开"说明）；`collector/export_report.py`
  （**自包含报告**模板同步加容器与调用——它内联同一份 `app.js`，因此导出的可分享报告同样带
  完整度卡片）；`web/index.html`（资源版本）；`tests/test_nearest_cat.js`（+27 断言，59 → **86**）
- **为什么**：v66 让采集端能如实区分「读失败」与「真没有层」并落盘 `channel_alert`，但**报告页
  仍只显示一条空白曲线**——用户看不出缺了多少点、为什么缺、缺在哪一段（run `20260911_162353`
  就是这样：62 个采样点里 52 点无 FPS，只能靠人工反查设备日志才定位到 adb 链路抖动）
- **影响面**：仅展示层，**数据结构与导出字段不变**；完整度卡片**只在有缺数时显示**（正常报告
  不新增噪声）；**前端资源版本 v60 → v61**（浏览器需强刷一次）
- **验证**：① JS 断言 59 → **86** 全绿；② 用**真实事故数据**（run `20260911_162353`）跑
  `computeCompleteness`：FPS 缺 52/62（83.9%，`no_layer` 51）、CPU 缺 42/62（`no_pid` 31 /
  `read_fail` 9）、FPS 缺数区间 `0.0s · 2.0~5.0s · 9.0~55.1s` —— 与主会话独立侦查结论**完全一致**；
  ③ fake-DOM 渲染断言 8/8（标题/分级/原因人话/区间文字/正常数据自动隐藏/probe_fail 新语义）；
  ④ 三份真实 run（含 162353 事故数据与 140605 正常数据）经 `export_report.export_html` 重新导出
  均成功且含完整度容器与调用；⑤ Python 156 测试回归全绿

## v66（2026-09-11 · 智谱GLM5.3flash 编码 + 主会话核实）

- **改动**：`collector/metrics/fps.py`（`resolve_layer_ex()` 返回 `(layer, err)`，把「读失败」
  与「真的没有层」分开：`--list` 异常/输出空 → 新错误码 **`probe_fail`**；`--list` 成功但
  无匹配层 → 保持 `no_layer`；旧签名 `resolve_layer()` 保留兼容）；
  `collector/pidresolver.py`（身份校验三态：`True` 确认 / `False` cmdline 可读且明确不匹配
  → 立即失效 / **`None` 读不到 = 未知** → 沿用旧 pid，连续 `IDENTITY_FAIL_STREAK = 3` 次
  才判失效；comm 不匹配只算未知不算否定——截断方向因 ROM 而异）；
  `collector/adb.py`（`shell(args, retries=1)`：瞬时通道错误 `error: closed` / `device
  offline` / `device not found` / `connection reset` 重试 1 次、间隔 0.15s；命令本身失败
  与**超时不重试**）；`collector/main.py`（新增 `ChannelAlertTracker` 状态机 → 断连/缺数
  写 jsonl 事件行 `channel_alert`（kind = `disconnect` / `missing_metric` / `recovered`，
  状态沿触发天然去重）；新增 `row_has_any_value()` 首点门槛修复首点全空；控制台文案补
  `probe_fail`）；`tests/test_parsers.py`（+17 用例，139 → **156**）
- **为什么**：run `20260911_162353`（16:23:53–16:24:55）63 点中 **51 点误报 `no_layer`**、
  32 点 `pid=None`，事后核对**层（`#18944`）与进程（13694）全程都在**，且同代码同设备
  事后三轮实测全绿（组件级 75s、9 流并发压测 45s、真实采集器 70s 均零缺数）、Windows
  无 USB 事件、设备 logcat 无异常 ⇒ 实为**主机 adb 通道瞬时失败**；但代码把「读不到」
  归因为「层不存在 / 进程消失」，且告警只打印不落盘，导致数据大面积空洞**且事后不可判读**
- **影响面**：新增错误码 `probe_fail`（历史数据的 `no_layer` 语义不变）；jsonl 新增
  `channel_alert` 事件行（前端 `prepareRows`、导出 `data_rows`、`data_health` 均按 `event`
  字段跳过——已核实）；采集首点不再全空；pid 判失效最多延后 ~10s（真死亡）
- **验证**：① **156 测试全绿**（`uv run --no-project python -m unittest discover -s tests -p "test_*.py"`）；
  ② 真机 60s 抽检（主会话独立跑）：59/59 点有 FPS，`no_layer=0 / probe_fail=0 / mem.pid=None=0 /
  cpu.pid=None=0`，首点 `t_ms=1000.8` 即含 FPS；③ 主会话独立降级验证（stub `--list` 抛异常）：
  连续 6 点均为 `probe_fail`、通道保持 `sf` 且零 `gfxinfo` 调用、含 `channel_alert` 的 jsonl
  导出正常且事件行不进入内联数据
- **未决**：`channel_alert` 的真机端到端形态未实测（60s 正常运行零故障）；报告页的
  `probe_fail` / `no_layer` 差异化标注与「缺数率」展示已由 **v67** 补上（web 域）

## v65（2026-09-11 · 主会话）

- **改动**：`collector/main.py`（目标错配自检从采样主循环 **移出**，改为独立守护线程
  `_mismatch_watch()`，新增常量 `MISMATCH_CHECK_INTERVAL = 10.0`）；
  `指标说明.md` 升 **V0.5**（新增「一、1」FPS 口径对比小节：上屏帧率 vs 引擎渲染循环帧率；
  「一、3」补充帧时间 P50≈P95≈Max 的正常性说明）；
  `web/index.html` + `web/report.html`（FPS 卡标题加 `.h2-q` 口径提示徽标；报告页 hint-card
  补 FPS 口径与帧时间说明）；`web/assets/style.css`（`.h2-q` 样式）；资源版本 **v59 → v60**
- **为什么**：① 真机 run `20260911_140605` 出现 **7 次 21.0s 采样间隔跳变**（t_ms
  198669→219683→240698…）——v63 的错配自检在采样主循环里每 5s 同步执行一次
  `dumpsys SurfaceFlinger --list`，设备/链路卡顿时 adb 阻塞约 20s，把采样循环一起拖住，
  造成可见数据空洞；② 用户明确要求：SDK 埋点实装前，先把"本工具 FPS（上屏）"与"引擎/
  开发者工具 FPS（渲染循环）"的口径差异在文档和界面标注清楚
- **影响面**：错配自检变为 **10s 一次异步检测**，采样间隔恢复正常（检测延迟 ≤10s，可接受）；
  界面/文档新增口径提示（**无数据格式变化**，jsonl 结构不变）
- **验证**：`compileall` 通过 + **139 测试全绿**；前端资源版本核查无 `?v=59` 残留

## v64（2026-09-11 · 主会话）

- **改动**：`collector/metrics/mem.py`（parse_meminfo 增解析 `TOTAL SWAP PSS` → 落盘
  `swap_pss_kb`；MemCollector 结果新增该字段）；`collector/data_health.py`（`rss_lt_pss`
  规则改用"非 swap PSS = pss − swap_pss"与 RSS 比较；swap 缺失时保持原口径）；
  `tests/test_mem_swap.py` 新建（8 用例）
- **为什么**：`dumpsys meminfo` 的 **TOTAL PSS 含 swap 部分**，进程被换出时会出现
  PSS > RSS（真机 appbrand0：PSS 231MB / RSS 211MB / SWAP PSS 141MB），被规则误判为
  "内存解析异常"（老报告 172/172 点全命中）
- **影响面**：jsonl 的 `mem` 新增 `swap_pss_kb` 字段；**新采集不再产生该误报**；
  历史数据（无 swap 字段）维持原判定口径，其 `rss_lt_pss` 告警可能为 swap 误报
- **验证**：真机（fixed_pid=20621）采样得 `pss 1316615 / rss 1406756 / swap 101449`，
  `check_row_health` 返回空（不误报）；测试 131 → 139 全绿

## v63（2026-09-11 · 主会话）

**背景**：`20260911_133010` 报告再次采错进程（第 2 次同类事故）——采到 appbrand0
（PSS 231MB、CPU 增量≈0），而游戏实际在 appbrand1（PSS 1035MB）；铁证是 FPS 层名
`SurfaceView[...AppBrandUI1](BLAST)` 指向 appbrand1 而进程是 appbrand0（微信同时存在
appbrand0/1/2 三实例）。v44 的"按累计 CPU 选最活跃"会偏向存在时间久的进程，且锁定后
不再重选。**用户要求：双击启动后先轻量检测展示，用户选定进程、点开始采集之前不记录
任何数据。**

- **后端**（commit `0a88b2b`）：
  · 新增 `collector/probe.py`——启动期只读探测（`parse_ps_candidates` /
    `pick_game_layer`（挑 SurfaceView 游戏层并解析 `AppBrandUI(n)`，排除
    InputSink/GestureNav/Input/Background）/ `parse_stat_ticks` / `parse_vmrss_kb` /
    `probe_once`）；推荐优先级：**层索引匹配 > 探测窗增量 CPU 最大 > 第一候选**
  · `main.py` 新增 `--auto`（跳过向导）；带 `--web` 默认走向导：探测（**不建任何采集
    输出**）→ 终端打印候选/推荐 → 等待确认（网页 `/api/start` 或本窗口回车/输入 pid）
    → **确认后才创建 jsonl 并采样**；采集期每 5s 做"层 vs 进程索引"错配自检，不一致写
    `target_mismatch` 事件行 + 页面告警，**不自动切换**（切换前必先采错数据）
  · `pidresolver.py` 新增 `fixed_pid` 模式：用户选定后固定采集该 pid，进程消失返回
    None（缺数可见），不再自动改选
  · `web.py` 新增 GET `/api/candidates`（TTL 8s）、POST `/api/start?pid=`（校验 pid
    在候选中）；`status` 增 `phase` / `target_source` / `mismatch`
- **前端**（本次提交）：`web/index.html` 启动向导卡片（候选列表 + 推荐标记 + 内存/CPU
  增量 + 「开始采集」/「重新探测」）+ 采集期错配横幅（含「停止并重新选择」/「忽略本次」）；
  `web/assets/style.css` 向导样式；`?v=` → 59
- **测试**：新增 `tests/test_probe.py` 18 用例 → Python 131 条全绿
- **真机验证**（荣耀 ADT-AN00）：探测识别层 `AppBrandUI1` → 推荐 **appbrand1**
  （RSS 1266MB、CPU 增量 84%），采错的 appbrand0（196MB、0%）被标不推荐；接口端到端
  验证：`/api/candidates` 200、非法 pid 被拒、`/api/start` 合法 pid 后
  `take_start_request()` 恰取走一次、跨站 Origin 403

## v62（2026-09-11 · 主会话）

- **改动**：`collector/web.py`（报告缓存新增"采样点总数"预算：`trim_report_cache` 纯函数 + `_report_cache_points/pints_max`）；`tests/test_fixes_20260911.py`（+5 用例）；**新建 `AGENTS.md`、`CHANGELOG.md`**
- **为什么**：① `_report_cache` 原策略只按"份数 ≤50"淘汰，每条是完整 rows（1 万点约 10~30MB），长测后连开多份长报告可能吃 GB 级内存；② 项目组多 AI 成员协作，需要单一同步入口，避免每次接手都通读项目
- **影响面**：采集端 web 服务（无接口/口径变化）；测试 108 → 113

## v61（2026-09-11 · 主会话）

- **改动**：`collector/web.py`（破坏性 POST 增同源校验 `same_origin_ok`）；`collector/main.py`（断连告警阈值 10→3 轮 + `backoff_sleep` 线性退避，封顶 5s）；`collector/export_report.py`（CSV/XLSX 追加"FPS已钳制/FPS低置信"列）；`tests/test_fixes_20260911.py` 新建（15 用例）
- **为什么**：长测前加固——防浏览器跨站 no-cors 静默触发 `/api/stop|shutdown|switch-target`；缩短设备半死时告警延迟（原最坏 3 分钟）；导出可筛选不可信采样点
- **影响面**：**导出多两列（列尾，原列序不变）**；测试 93 → 108

## v60（2026-09-11 · kimi k3 交付 + 主会话补正）

- **改动**：`devices.md`（**新建 V1.0**：适配矩阵 / 机型行为差异 / 「五、换机验收步骤」/ 待验证场景 / 阈值过拟合提醒）；`架构设计.md`（**重写 V1.0**）；`指标说明.md`（**V0.4**：Jank 节奏校准口径 + 自掩蔽局限，新增「九、统计口径说明」「十、数据健全性自检」）；`README.md`（「5 分钟上手」+「文档索引」）；6 份文档头部状态标注
- **为什么**：文档收敛与交接准备；把口径与自查规则写成可查文档（此前 data_health 规则零文档）
- **影响面**：仅文档，无代码

## v59（2026-09-11 · 智谱GLM5.3flash）

- **改动**：`collector/metrics/fps.py`（FPS 物理上限钳制 + `fps_clamped`/`fps_warn` 落盘 + FPS/Jank 口径说明 + `_parse_latency` 改"首个非空行"+刷新周期物理窗口 + Jank 注释校正）；`collector/pidresolver.py`（`_identity_ok`：cmdline 完整名优先 + comm 回退）；`collector/metrics/mem.py`（pid=None 不回退包名）；`collector/export_report.py`（`script_safe_json` 转义 `</script>`）；`tests/test_parsers.py`（+17）
- **为什么**：独立评估发现的高风险项——FPS 主段无上限可输出非物理值、comm 校验与自身文档假设矛盾（真机实测：荣耀 Android 14 的 comm 取"末 15 字符"）、mem 回退包名会造成假突跳、导出内联 JSON 有注入面
- **影响面**：**jsonl 新增字段 `fps_clamped` / `fps_warn`**；测试 76 → 93

## v58（2026-09-11 · 重活大鲸鱼DSV4P）

- **改动**：`web/assets/app.js`（锁定蓝线随缩放/平移重定位；长报告等距降采样 3000 显示点、统计仍全量；削减 `getOption()` 深拷贝；KPI 口径标注）；`web/report.html`（"?" 卡口径说明）；`web/assets/style.css`；`web/index.html`（版本号）
- **为什么**：锁定后拖时间条"所见≠所报"；长报告（数千点）打开与交互卡顿；统计口径未标注会影响与官方 PerfDog 对拍的可信度
- **影响面**：**前端资源 `?v=` → 58**；界面文案（"卡顿率（均值）""帧时间 P95（均值）"）

## v57（2026-09-11 · 智谱GLM5.3flash）

- **改动**：`.gitignore`（`!/perfdog.spec`）、`perfdog.spec`（**首次入库**）、`web/assets/app.js`（三处 `Math.min/max.apply` → 循环归约）、`web/report.html`（事件层 fetch 竞态守卫）、`web/index.html`（版本号）
- **为什么**：`perfdog.spec` 被 `*.spec` 忽略从未入库，而 CI 执行 `pyinstaller perfdog.spec` → **构建必失败**；长报告 `apply` 参数展开抛 `RangeError` 致统计栏崩溃；快速切报告时旧事件线会画到新报告上
- **影响面**：CI 打包链路恢复可用；`?v=` → 57

---

## 历史摘要（v36 ~ v56）

| 版本 / 时间 | 要点 | 负责 |
|---|---|---|
| v56（9-08） | 顶部时间条出入动效（与快照条观感一致） | pro |
| v52~v55（9-07） | rename 内联化；锁定时刻全指标快照条（引入 → 淡入淡出动效 → 按模块分行 → Excel 冻结窗格） | pro |
| v50/v51（9-03） | 左侧栏去卡片化（透明 + 右缘极淡分隔线）；全局深色细滚动条 | pro |
| v49（9-03） | 报告标题行收纳时长/采样点；左侧栏 sticky 常驻；双报告对比轻量版（两列 KPI + Δ 方向着色） | pro + 主会话补漏（注入转义/占位恢复） |
| v47/v48（9-02/03） | 看板大优化：KPI 阈值着色、"?" 操作卡、全局单时间条、📄 打开自包含报告、URL hash 深链、toast、窄屏修复 | Qwen + pro |
| v46（9-02） | 锁定浮层/白线数值降序；设备信息探测（型号/市场名/芯片/频率/分辨率） | pro |
| OPPO 适配（9-01） | 输入事件层 skip + 窗口层重匹配（FPS 0 修复）；FPS 主段法（修 0.01 病态值）；9-07 真人确认恢复（中位 ~57.6） | 主会话 + Qwen |
| v44/v45（8-27） | 采错进程修复（多 appbrand 按 utime+stime 选活跃）；Jank 节奏吸附阈值（修"面板 120Hz + 游戏锁 60fps"假 Jank） | 主会话（kimi 归因） |
| 数据健全性自检（8-27） | `data_health.py` 5 规则 + 实时告警 + 报告横幅；借此发现 2 份历史采错数据 | pro |
| v36~v43（8-22~8-26） | mem PSS/RSS 同源修复；加载态"加载完再显示"；Failed to fetch 修复；legend 点击拦截；runs/report 缓存 + 并发锁；15 golden test | 主会话 / Qwen / pro |

---

## 已知作废数据（历史，勿用于结论）

| 采集记录 | 问题 | 作废范围 |
|---|---|---|
| `20260824_082744` | PSS 全空（meminfo 段内空行解析 bug） | 内存指标 |
| `20260824_084252`、`20260827_083117` | 采错进程（采到闲置 appbrand） | CPU / 内存 / 网络 |
| `20260901_155646/155733/160412` 等 | OPPO FPS 病态 0.01（稀疏缓冲算法，已修） | 仅 FPS |
