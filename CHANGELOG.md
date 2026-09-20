# CHANGELOG — 自研 PerfCollect 变更日志

> **接手项目先读本文件最新 1~2 条**，不必通读代码。
> 每条格式：`版本 | 日期 | 负责 | 改动文件 | 为什么 | 影响面`。
> 维护人：主会话（每次提交同步追加；历史条目由版本记录回溯整理）。

---

## 当前状态（2026-09-20，v101）

| 项 | 值 |
|---|---|
| 前端资源版本 | **v81**（`web/index.html` 3 处、`web/report.html` 4 处 `?v=`） |
| 测试 | Python **274** 条 + JS **181** 断言（全绿） |
| 分发方式 | **zip**（`make_zip.bat` / `tools/make_zip.py` → `share/perfcollect-cn-<版本>-<日期>.zip`）——**已无 exe / 安装包** |
| 采集环境 | Windows + adb 真机（荣耀 ADT-AN00 Magic3 Pro / OPPO；详见 `devices.md`） |
| 工具链 | `uv`（Python）+ `bun`（JS 测试）+ `adb` |

---

## v101（2026-09-20 · 主会话）—— 仓库公开面收敛：内部文档移出仓库

- **本地专用目录 `docs-local/`**（已 gitignore）：`架构设计.md`、`开发交接记忆-20260917.md`、
  `UI优化建议.md`、`代码评估与优化项目.md`、`打包后操作流程与改动需求.md`、
  `评估报告-kimi-k3.md`、`评估报告-qwen3.8-max.md` 及相关本机产物移入；
  **文件位置改变、内容保留，仅不再随仓库公开**（`git rm --cached`，磁盘文件未删）。
- **`分享说明.md` 保留在仓库**（复核后调整）：它是 zip 包内「先读我-使用说明.md」的来源、
  面向使用者，与 `tools/make_zip.py` 配对 → 任何 clone 都能打出完整分发包。
- **`AGENTS.md` 不入库**（保留在仓库根目录以便 AI 协作者自动发现），并新增「文档归属规约」：
  只有"项目运行/使用者必需"的文档才可入库，且入库文档不得含"下一步 / 未完成 / 待验证 /
  待办 / 路线图"等计划性内容；其余一律放 `docs-local/`。
- **公开文档清理**：README 删除「下一步」段、文档索引表只列 5 份现行文档；
  CHANGELOG 删除「未完成 / 待验证」段（上述计划性内容已归入本机 `docs-local/` 交接记忆）。
- **一致性门禁适配**：不再读取 `AGENTS.md` / `架构设计.md` / `开发交接记忆`（它们已不入库），
  保留 CHANGELOG 版本与测试数、两页 `?v=` 一致性、README 导航 ↔ `指标说明.md` 正文版本；
  **新增公开文档禁词检查**（根目录已入库 `*.md` 中不得出现计划性措辞；
  `CHANGELOG.md` 因属历史日志、`devices.md` 因"待验证场景"是给使用者的能力矩阵，二者豁免）。
- **范围**：不改采集算法、指标口径、JSONL schema、Web API 与前端资源（`?v=` 保持 v81）；
  仓库跟踪文件 **79 → 71**。

---

## v100（2026-09-20 · Codex）—— Pin 移出窗口时保持页面稳定

- **移除窗口外警告行**：拖动/缩放使锁定点离开当前数据窗口时，只隐藏图内蓝线与
  浮层；顶部锁定快照保持原有数据和高度，不再追加“锁定点已在当前缩放窗口外”。
- **消除纵向抖动**：锁定点离开或重新进入窗口均不再重建快照 DOM，页面不会因警告行
  展开/收起而上下跳动；锁定状态与原始时刻数据仍完整保留。
- **范围**：纯前端体验调整，不修改 Pin 数据、采集算法、Label、JSONL schema 或统计口径；
  前端资源 **v80 → v81**，Python **274**、JS **180 → 181**。

---

## v99（2026-09-20 · Codex）—— Pin 与 Label 统一类目坐标系

- **Pin 采样点吸附**：点击只决定最近采样索引，蓝色锁定线与浮层统一回到该类目坐标；
  不再保留原始点击像素，因此命中同一数据时与 ECharts 白色悬停线严格重合。
- **Label 绘图区对齐**：历史看板和自包含报告的 Label 轨道左右边界与 FPS 图实际
  `grid` 对齐，不再覆盖 Y 轴留白；窗口缩放后仍使用同一绘图区宽度。
- **统一缩放口径**：Label 毫秒边界先按真实采样时刻插值为连续类目索引，再套用与
  `dataZoom` 相同的可见索引窗口；非等间隔采样和缩放场景不再产生 Label 归属漂移。
- **真实数据复验**：在 `20260920_084958` 报告验证 `t=63.7s`，全范围及缩放至前约
  34% 后均稳定位于“交易行”（旁车边界为 30.314s–64.702s），控制台无错误。
- **范围**：不修改采集算法、Label 旁车数据、JSONL schema 或统计口径；前端资源
  **v79 → v80**，Python **274**、JS **172 → 180**。

---

## v98（2026-09-20 · Codex）—— Label 缩放联动与历史页滚轮修复

- **Label 跟随可见窗口**：历史看板拉伸、缩短或整体平移全局拖动条时，Label 时间轨
  同步裁剪到当前可见时间范围并重新映射宽度；用户看到的 Label 与图表数据区间一致。
- **图表区域可正常翻页**：在捕获阶段隔离 ECharts 的普通滚轮监听但保留浏览器默认行为，
  鼠标位于任一指标图上时也能继续上下滚动页面；`Ctrl + 滚轮` 不受影响。
- **实时按钮精简**：`＋ 分段` 收窄到 60px、11px 字号与更轻的内边距，仍保留清晰点击区。
- **回归**：真实浏览器验证缩放、平移、图表滚轮和控制台；不修改采集算法、Label 旁车格式、
  JSONL schema 或统计口径。前端资源 **v78 → v79**，Python **274**、JS **170 → 172**。

---

## v97（2026-09-20 · Codex）—— Label 增量更新、交互隔离与吸顶时间轨

- **编辑不再冻结时间轨**：Label 色块按稳定 ID 复用 DOM；实时采样只更新色块位置和
  宽度，输入框保持原节点，因此当前段可继续增长，已结束段与进行中段都可随时改名。
- **分段/改名完全隔离**：取消“点击整条即分段”；新增醒目的 `＋ 分段` 按钮，
  单击一次仍完成切段。Label 整个色块均可点击改名，不必精准命中小铅笔。
- **历史页吸顶**：紧凑 Label 时间轨与全局拖动条组成双层 sticky 区域；下翻越过
  原位置后平滑增加背景、边框、位移和阴影，向上返回时反向恢复。
- **范围**：不修改 Label 旁车格式、采集算法、JSONL schema 或统计口径；前端资源
  **v77 → v78**，Python **274**、JS **170** 回归保持全绿。

---

## v96（2026-09-20 · Codex）—— Label 改名稳定性与报告信息降噪

- **修复实时改名被打断**：实时采样仍正常刷新图表，但检测到 Label 输入框时不再
  重建 Label 时间条；回车或失焦提交、Esc 取消后恢复常规刷新。
- **取消图内彩色背景**：Label 颜色不再以 `markArea` 贯穿每张指标图，避免与图表
  grid 边距造成视觉错位，也不再遮挡曲线和刻度。
- **紧凑全局时间轨**：历史看板把 Label 条移到全局拖动条下方、统计卡上方，
  高度缩至 20px；只保留一份时间分段信息，各数据块保持纯净。
- **范围**：只调整前端交互和布局，不修改 Label 数据、采集算法、JSONL schema
  或统计口径。JS 回归 **168 → 170**，前端资源 **v76 → v77**。

---

## v95（2026-09-18 · Codex）—— 一键 Label 分段替代双 Pin 区间编辑

- **采集期零负担打点**：采集首点自动进入 `Label1`；Label 条每点击一次便以
  最新采样时刻闭合当前段并开始 `Label2/Label3…`，无需两次 Pin、填备注或选颜色。
- **自动配色与可选改名**：服务端按序号循环分配稳定颜色；采集时只需点击一次，
  有空时可点段内 `✎` 改名。历史看板同样支持改名并立即重绘。
- **进行中区间**：最后一段以 `end_ms: null` 保存，实时延伸至最新点，采集结束后
  自然延伸至报告末尾；原 v94 已保存的闭合区间仍兼容。
- **三端展示**：实时页、历史页和自包含 HTML 都增加与时间轴对齐的彩色 Label 条，
  曲线色带继续同步显示；不修改采样、指标算法、JSONL schema 或统计口径。
- **回归**：Python **271 → 274**，JS **167 → 168**，前端资源 **v75 → v76**。

---

## v94（2026-09-18 · Codex）—— 实时 Pin 区间备注与历史同步

- **实时区间打点**：实时看板图表启用跨图 Pin；锁定时间点后可设区间起点，
  再锁定另一时间点作为终点，填写场景/操作备注并选择颜色。
- **独立可靠存储**：新增 `timeline_annotations.py`，每份报告以
  `*.annotations.json` 原子保存区间起止、备注、颜色与 ID；不污染性能 JSONL，
  不参与统计。输入长度、时间范围、颜色和路径穿越均有校验，支持删除误标。
- **三端一致展示**：实时曲线、历史看板和采集结束生成的自包含 HTML 均显示
  同色半透明区间带与备注图例；历史看板读取同一旁车文件，无需重新采集。
- **回归**：新增存储、HTTP API、自包含报告和前端时间轴映射覆盖；Python
  **265 → 271**，JS **161 → 167**，前端资源 **v74 → v75**。
- **兼容性**：未修改指标算法、采样节奏、JSONL schema 或统计口径；没有标注的
  历史报告保持原样。

---

## v93（2026-09-18 · Codex）—— 热切换编排、报告脚本分层与 APK 模糊测试

- **main.py 继续收口**：新增 `TargetSwitcher`，统一负责目标配置原子持久化、
  Resolver/目标相关采集器重建、切换事件顺序和 Web 状态刷新；温度采集器仍跨目标复用。
  `main.py` 不再内联约 50 行热切换副作用编排。
- **历史报告分层**：将 `report.html` 的约 620 行页面业务逻辑原样迁移到
  `web/assets/report.js`；HTML 只保留结构与资源入口，共享图表能力继续位于 `app.js`。
  静态资源 HTTP 回归新增 `report.js` 覆盖。
- **APK 解析抗畸形输入**：新增确定性随机字节、变异 chunk 头、恶意字符串数量、
  未终止 ULEB、超深容器和 AXML 超限回归，锁定解析器快速失败且不抛异常。
- **回归**：新增 10 项 Python 测试，Python **255 → 265**；JS 保持 **161**；
  前端资源 **v73 → v74**。不修改采集指标算法、JSONL schema 或报告统计口径。

---

## v92（2026-09-18 · Codex）—— 跨图联动 tooltip 完整性修复

- **修复单序列提示框**：`echarts.connect` 跨图同步 `showTip` 偶尔只携带
  当前命中的一个 `seriesIndex`，导致 FPS/Jank 等多序列图仅显示其中一项；
  现在根据目标图的 `dataIndex` 从本图 series 数据重建全部有效序列。
- **行为保持**：保留跨图白色指示线、按数值降序、两位小数、v91 的无动画与
  禁用 emphasis 策略；不修改采集端、JSONL schema、指标算法或统计口径。
- **回归**：新增单序列联动参数仍补齐 FPS/Jank、空参数安全返回等断言；
  JS **155 → 161**，前端资源 **v72 → v73**。

---

## v91（2026-09-18 · Codex）—— 48 分钟长测验收与悬停闪烁修复

- **长测链路通过**：复核 `20260918_103516` 的 2867 个报告点（约
  48.2 分钟），无 JSON 损坏、无 >1.5s 断档、无指标错误码，PID 与 SF 图层
  全程稳定；`fps_clamped` / `low_frames` 均为 0。
- **长测风险提示**：后半程 FPS 与前半程相比明显回落，同时电池温度
  上升至 44°C、CPU 与 PSS 上升；这是被测应用/场景的性能风险，不是
  采集链路异常，需用固定场景循环复测区分热降频与内存累积。
- **悬停闪烁修复**：长报告多图 `echarts.connect` 会在鼠标移动时高频传播
  hover 强调与 tooltip 过渡，接近 3000 点时会引起曲线反复重画。现禁用
  线系列默认 emphasis，并关闭 tooltip/axisPointer 延迟动画；跨图白线、
  tooltip 和点击锁定功能保留。
- **回归**：JS **152 → 155** 断言；前端资源 **v71 → v72**。
- **改名（主会话执行）**：自研品牌 `PerfDog` → **`PerfCollect`**，仓库改名
  `perfcollect-spark-darcy`：UI 标题/页脚、`start_perfcollect.bat`、`sdk/perfcollect-sdk.js`、
  数据文件前缀 `perfcollect_<run_id>.{jsonl,events.jsonl,html}`、分发 zip 顶层目录、
  Excel sheet 名、报告标题、控制台与浏览器日志标记统一替换。
  ⚠️ **第三方指名引用保留**：文中指向商业工具 PerfDog 的表述（"与 PerfDog 同口径"、
  "官方 PerfDog 对拍"、"PerfDog Memory"、"PerfDog 官方免 root 同理"等）与历史产物名
  （`perfdog.spec`、`perfdog.exe`）按原样保留——替换成自家名字会让技术表述失真。
  ⚠️ **历史数据零改动**：`collector/output` 下 114 个旧文件名与 45 个 run 目录保持原样；
  服务端按 `*.jsonl` 扫描、报告页按 API 返回的真实文件名读取，旧报告无需重命名即可继续查看
  （已用真实长测 `20260918_103516` 走 HTTP 端点验证）。

---

## v90（2026-09-18 · Codex）—— gfxinfo/温度采集可靠性与 FPS 质量提示

- **gfxinfo 切换降载**：通道切换函数直接返回首个基线样本，不再在同一
  `ts` 立即重复 `dumpsys gfxinfo`；正常指标口径不变，减少一次 ADB 往返。
  边界副作用（主会话 A/B 实测）：切换点的 `total_frames` 取第一次读取值、且该帧
  计入下一次增量（不丢帧）；「连续 0 帧 → 回退 sf」现严格按 3 次采样推进
  （旧实现每次切换记 2 次、实际约 1.5 次即回退）→ WebGL 类应用回退延迟约 0.5s，
  与注释声明的“连续 3 次”一致。
- **gfxinfo 边界防护**：ROM 计数重置/延迟导致 Janky 增量大于总帧增量时，
  `jank_count` 夹到 `[0, 帧增量]`，防止产出超过 100% 的非物理 Jank 率。
- **温度通道自恢复**：sys 节点首次探测失败后改为 30s 低频重试；
  不支持的 ROM 仍避免每轮无效 `cat`，启动阶段 ADB 瞬断后则能自动恢复电流/功率。
- **FPS 质量可见**：报告 FPS 统计栏汇总 `fps_warn="low_frames"` 与
  `fps_clamped=true` 点数，排除复用快照；正常数据不增加界面噪声。
- **口径文档**：《指标说明》升至 V0.8，修正“采集器无上限”的过时表述，
  说明保护性钳制和低置信标记。
- **回归**：新增 3 项 Python 与 3 条 JS 断言；Python **252 → 255**，
  JS **149 → 152**；前端资源 **v70 → v71**。

## v89（2026-09-18 · Codex）—— 报告口径说明与实现对齐

- **报告帮助修正**：不再把 v76+ 新数据的 Jank 写成“逐点算术平均”，
  明确为 `Σjank_count / Σjank_total`帧加权；帧时间 P95 明确为各报告点
  “0.5s 短窗 P95 峰值”再求均值，并说明旧 JSONL 自动回退旧口径。
- **导出文档修正**：`fps_source()` 注释与实际 SF Jank 阈值
  `2×实际呈现节奏×1.1`对齐，同时保留小样本回退 `2×refresh` 说明。
- **文档索引治理**：README 中指标/架构文档版本分别更新为 V0.7/V1.5；
  架构文档移除会在每次提交后立即过期的固定 HEAD hash。
- **防复发门禁**：一致性检查新增 README 文档导航与《指标说明》/
  《架构设计》正文版本对比，后续再漏更会在 CI 直接失败。
- **兼容性**：未修改采集/统计逻辑（`collector/export_report.py` 仅改动
  `fps_source()` 的文档串措辞，无行为变化）、指标算法、JSONL schema、Web API 或统计实现；
  前端资源 **v69 → v70**。
- **文档收尾**：交接记忆移除会随每次提交立刻过期的固定 HEAD/「尚未提交」表述，
  改为指向 `git log`；同步 v89 打包冒烟结果；自检命令统一为
  `uv run --no-project python`。

## v88（2026-09-18 · Codex）—— 缺帧数报告渲染修复

- **报告页不再整体中断**：FPS/帧时间全缺时，`applyTime` 和 `resize`
  只操作当前可见图表，避免 ECharts 在 `display:none` 的 0×0 容器上重建
  坐标系抛错；CPU/内存/网络/温度图、统计、汇总与完整度卡片继续渲染。
- **错误码文案**：`gfx_unavailable` 显示为“gfxinfo 不支持该应用，已回退
  SurfaceFlinger”，不再直接暴露英文原码。
- **回归覆盖**：新增 11 条 JS 断言，覆盖隐藏图不收到 `setOption/resize`、
  其他四图继续渲染及错误码中文映射；JS **138 → 149**。
- **工程卫生**：根 `.gitignore` 显式忽略 `.uv-cache/`。
- **兼容性**：未修改采集器、指标算法、JSONL schema 或 Web API；前端资源
  **v68 → v69**。

## v87（2026-09-17 · Codex）—— logcat 收尾、事件写入边界与真实浏览器回归

- **事件写入收口**：新增 `collector/event_sink.py`，logcat 事件改为惰性打开、
  批量 JSONL 写入与单一关闭边界；零事件仍不创建空文件。
- **停止顺序**：`LogcatMonitor.stop()` 先置停止信号、终止子进程并有限等待
  reader 线程；主流程按“停生产者 → 排空尾部事件 → 关 sink”收尾，重复停止幂等。
- **历史列表修复**：真实浏览器回归发现 `*.events.jsonl` 被误列为独立报告；
  `/api/runs` 现排除事件旁车文件，`/api/report` 也拒绝将其当性能报告读取。
- **最小浏览器回归**：新增 `tools/browser_smoke_server.py` 生成双场景合成数据；
  已用真实浏览器验证实时图、Pin 蓝线/数值浮层、历史场次切换、全局时间滑块、
  logcat 事件虚线与控制台无警告/错误。
- **测试/CI**：新增 7 项覆盖 sink、停止排空、线程回收和事件旁车过滤；
  新模块/浏览器数据服务纳入 Python 3.12 语法检查。Python **245 → 252**，JS **138** 全绿。
- **分发**：按当前优先级不生成 zip。

## v86（2026-09-17 · Codex）—— 目标错配状态收口与控制台输出解耦

- **TargetMismatchTracker**：新增 `collector/target_monitor.py`，将 AppBrand 渲染层/
  进程索引错配的判定、消息去重和恢复沿从 `main.py` 提取为纯状态机。
- **修复过期告警**：当页面已显示微信 AppBrand 错配后热切换到非微信 App，
  原 watcher 会直接跳过并永久保留旧告警；现在会发出一次 recovered 状态沿清理 Web 状态。
- **行为不扩张**：仍然只在微信目标下读 SurfaceFlinger；同一错配不重复落盘，
  不自动更换进程，不修改 `target_mismatch` 事件字段。
- **控制台格式化**：新增 `collector/console_output.py`，将 FPS 错误文案、CPU/内存/
  网络/温度单行输出提取为纯函数；`main.py` 只负责打印返回文本。
- **兼容性**：无指标算法、采样周期、JSONL schema、Web API 或前端变化；
  已锁定三种 FPS 错误文案、节流/缺值占位符及未知错误码仍显示有效 FPS 的历史契约。
- **测试/CI**：新增 7 项状态与格式回归，两个新模块纳入 Python 3.12 语法检查；
  Python **238 → 245**，JS **138** 全绿。
- **分发**：按当前优先级不生成 zip。

## v85（2026-09-17 · Codex）—— 运行健康状态机从 main.py 解耦

- **RuntimeHealthTracker**：新增 `collector/runtime_health.py`，统一维护每轮指标
  错误码、通道缺数/断连事件沿、多数指标连续失败计数和实时健全性连续命中状态。
- **副作用边界**：新组件只返回 `probe_required` / `recovered` 等状态沿；
  ADB 探活、控制台告警、Web status 更新和 JSONL 事件落盘仍在 `main.py`，没有引入隐式 I/O。
- **纯逻辑归位**：`ChannelAlertTracker`、`backoff_sleep`、`row_has_any_value`
  从 CLI 入口迁入运行健康模块；原错误阈值、退避公式、事件字段和首点门槛保持不变。
- **恢复语义**：仅在连续失败第 3 轮首次请求 ADB 探活，持续失败不重复探活；
  低于多数错误阈值后清零，且只发一次 Web 恢复状态沿，与原实现一致。
- **测试/CI**：新增 4 项覆盖第 3 轮探活、去重、恢复、短失败清零和
  健全性状态所有权；新模块纳入 Python 3.12 语法检查。Python **234 → 238**，JS **138** 全绿。
- **分发**：按当前优先级不生成 zip，留待项目功能/架构全面收敛后统一验收。

## v84（2026-09-17 · Codex）—— 采样调度与样本聚合从 main.py 解耦

- **SamplerScheduler**：`collector/sampling.py` 新增独立调度器，统一创建五类
  周期 worker，保留“采样耗时从周期中扣除、最低等待 50ms、停止竞态安全收口”语义。
- **SampleAggregator**：将 mailbox 快照转为 JSONL 行的逻辑从 `main.py` 提取，
  统一生成 `ts/t_ms/target`、`metric_meta`、`fps_windows`、短窗摘要和丢弃计数。
- **职责收敛**：`main.py` 现在只负责创建调度器、取得目标代次快照并调用聚合器；
  指标执行节奏和行数据组装可分别独立测试。
- **兼容性**：未修改采样周期、指标算法、目标切换、停止顺序、CLI、JSONL 字段或前端；
  schema 保持 v3，前端资源保持 v68。
- **测试**：新增 5 项覆盖 worker 命名/登记、停止前置、耗时补偿、
  新鲜/复用样本及 FPS 短窗摘要等价性；Python **229 → 234**，JS **138** 全绿。
- **分发**：按当前优先级未重新生成 v84 zip；分发验收留待功能和架构收敛后统一执行。

## v83（2026-09-17 · Codex）—— 项目元数据一致性门禁

- **新增只读校验**：`tools/check_consistency.py` 自动对比 CHANGELOG、AGENTS、
  架构文档、交接记忆和优化待办中的项目版本与测试计数。
- **真实计数**：用 `unittest` discovery 直接统计当前 Python 用例，与文档中的
  229 项对比，不再只依赖人工复制数字。
- **前端缓存版本**：校验 `web/index.html` 与 `web/report.html` 各有 3 个、
  且全部与状态文档一致的 `?v=` 引用，防止改前端后漏升版。
- **CI 门禁**：GitHub Actions 在单元测试后执行一致性检查，脚本本身纳入
  Python 3.12 语法检查。不一致时直接列出每个文档的实际值并使 CI 失败。
- **Windows 测试稳定性**：`CaptureSession.wait()` 的 10ms 超时回归改用
  `perf_counter()` 计时，避免 Python 3.12 / Windows 上 `monotonic()` 15.625ms 分辨率导致的偶发假失败；业务实现未改。
- **兼容性**：未修改采集器、Web API、前端或分发包内容；Python **229**、
  JS **138** 保持全绿，前端资源保持 v68。

## v82（2026-09-17 · Codex）—— Web HTTP/SSE 真实端到端回归与资源收口

- **SSE 断连兼容**：将 Windows 客户端正常断开可能产生的
  `ConnectionAbortedError` 纳入正常结束语义，不再向控制台输出 WinError 10053 堆栈。
- **Web 服务关闭**：`WebServer.stop()` 现在会原子摘除实例、`shutdown` +
  `server_close` 释放监听 socket，并有限等待服务线程；重复停止幂等，同一端口可立即重新绑定。
- **文件句柄**：历史列表读取备注 sidecar 改用上下文管理，消除轮询期间的未关闭文件警告。
- **真实 HTTP 回归**：新增 `tests/test_web_http.py` 13 项，通过本机随机端口覆盖
  所有 API、SSE、首页/历史页/静态资源/favicon、停止与重绑定、跨域 POST、
  备注/events/raw 往返及 report/raw 路径穿越防护。
- **兼容性**：未修改采集器、指标算法、CLI、JSONL schema 或前端资源；前端版本保持 v68。
- **验证**：Python **216 → 229**，JS **138** 全绿；Python 3.12 `py_compile`、
  JS bundle 语法检查与 zip 分发烟测通过。

## v81（2026-09-17 · Codex）—— 核心切换时序、异常契约与采样时间修复

- **目标切换顺序**：`TargetContext.switch_target()` 新增持锁 `before_switch(old_snapshot)`
  卡口；`target_switch` 事件先提交唯一 writer，新目标才对主循环可见。修复高并发下第一条
  新目标采样可能排在切换事件之前、导致历史分段边界偏移的问题。
- **异常契约**：采样器未捕获异常统一写成
  `{"error":"<metric>_exception","detail":"..."}`，首点门槛、缺数统计和断连状态机
  都能识别，不再产生静默空洞。
- **完成时间语义**：采集器继续接收开始时间，保持 CPU/网络差值算法不变；mailbox 的
  `sampled_at` 改用调用完成时间，使 schema v3 的 `sampled_at_ms` / `age_ms` 与“结果真正
  可用时间”一致，慢 ADB 调用不再被误算成旧数据年龄。
- **向导停止收口**：SIGINT 与 Web stop/shutdown 在向导开始前注册；初始化阶段增加取消
  卡口，停止后不继续创建输出或启动采样线程。若仅含 meta 的文件已经创建，则停止
  monitor、关闭 writer 和 Web 后安全返回。
- **兼容性**：指标算法、CLI、JSONL 字段名和前端均未改变，schema 保持 v3、资源 v68。
- **测试**：新增 5 项覆盖完成时间、标准异常结构、切换前置卡口和并发可见性；Python
  **211 → 216**，JS **138** 全绿。

## v80（2026-09-17 · Codex）—— CaptureSession 生命周期第一阶段

- **停止状态单一来源**：新增 `collector/capture_session.py`，以 `threading.Event` 统一
  Ctrl+C、Web `/api/stop`、`/api/shutdown`、向导取消和 duration 到期，不再共享可变 dict。
- **可中断等待**：采样间隔、断连退避和 mismatch 周期从 `time.sleep()` 改为会话等待；
  收到停止请求即可唤醒，正常情况下不再额外等待最长一个 mismatch 周期。
- **线程归属**：五个 sampler 与 mismatch watcher 统一由会话启动、登记和有限时间 join；
  ADB 调用若正在阻塞只等待总计 1 秒，不强杀线程，继续保持原有超时与容错策略。
- **关闭顺序**：采集循环退出后先 request_stop、有限回收生产者，再 drain/close
  `JsonlWriter`；迟到的 mismatch 写入仍由 writer 的关闭保护安全拒绝。
- **兼容性**：CLI、JSONL schema、采样周期、指标算法和前端均未改变，资源版本保持 v68。
- **测试/CI**：新增 6 项覆盖初始状态、幂等停止、等待唤醒、shutdown、停止后拒启线程与
  join 存活报告；CI 语法检查纳入新模块。Python **205 → 211**，JS **138** 全绿。

## v79（2026-09-17 · Codex）—— 指标真实采样时间与去重复统计

- **schema v3**：每个采样点新增 `metric_meta.<metric>`，记录 `seq`、相对采集起点的
  `sampled_at_ms`、落盘时 `age_ms` 和 `is_reused`；meta 新增
  `metric_freshness_mode: "sampled_at"`。原指标字段不变，旧读取方可忽略新增字段。
- **统计纠偏**：实时统计栏和报告汇总只让真实新样本参与 FPS/CPU/内存/网络/温度统计，
  解决 mem/therm 2 秒采样值在 1 秒报告点中被重复加权的问题；曲线仍保留 latest 连续显示，
  数据完整度仍按每个报告点是否可用判断。
- **兼容性**：旧 JSONL 没有 `metric_meta` 时，`metricIsFresh()` 默认把每点视为新样本，
  保持原统计结果；新报告卡片用“（新采样）”明确标注平均值口径。
- **导出**：CSV/XLSX 为五类指标分别追加采样时刻、年龄、序号和是否复用字段，便于外部
  分析按真实样本去重，不覆盖既有列。
- **前端**：资源版本 **v67 → v68**。
- **测试**：增加时间/年龄计算、复用识别、时钟偏差保护、导出字段以及新旧报告统计兼容；
  Python **201 → 205**，JS **127 → 138** 全绿。

## v78（2026-09-17 · Codex）—— 主 JSONL 唯一串行写入器

- **单一写入入口**：新增 `collector/jsonl_writer.py`；meta、采样点、缺数事件、目标切换
  和 mismatch 事件全部提交给唯一后台写线程，不再由主循环和回调分别持有 `w/a` 文件句柄。
- **确定顺序**：writer 在注册 Web 回调和启动采样线程前写入 meta，保证 meta 始终为首行；
  多生产者记录按队列接收顺序落盘，每一行只由一个文件句柄编码和写入。
- **可靠停止**：采集结束（包括 duration 到期）先置 stop，阻止 sampler/mismatch 继续生产，
  再 drain 队列、flush、close；HTML 导出只在 writer 完全关闭后读取 JSONL。
- **flush/异常语义**：`flush=True` 返回即已刷盘；非法 JSON 在提交线程立即失败；打开或写入
  错误可传播，失败路径会唤醒等待者且不会遗留 writer 线程；关闭后的迟到写入被明确拒绝。
- **边界**：logcat 的独立 `.events.jsonl` 文件保持原有路径，不混入主性能 JSONL；CLI 与
  JSONL schema 均未改变。
- **测试/CI**：新增 7 项，覆盖首行、Unicode、四线程并发 200 条、close drain、同步 flush、
  关闭后拒写、非法对象和打开失败线程收口；CI 语法检查纳入新模块。Python **194 → 201**，
  JS **127** 保持全绿。

## v77（2026-09-17 · Codex）—— TargetContext 收拢热切换状态

- **目标状态单一来源**：新增 `collector/target_context.py`，用线程安全
  `TargetContext` 统一保存 package、process pattern、resolver、动态 pid、采集器集合和
  generation；`main.py` 不再用散落闭包变量与外部锁拼接目标状态。
- **跨代采样隔离**：worker 采样前领取采集器与 generation，完成后只允许当前代次发布；
  主循环在同一原子操作中取得 mailbox 窗口和目标标签，热切换不会把慢返回结果标成新目标。
- **错配守护修复**：mismatch 线程每轮读取当前 resolver/proc_name/pid；连续热切换后不再
  引用启动时的旧 resolver，切到非微信目标时暂停 AppBrand 专属检查。
- **兼容性**：CLI、JSONL schema、采样周期和报告口径均不变；温度采集器作为设备级实例
  在目标切换时继续复用。
- **测试/CI**：新增 6 项上下文回归，覆盖代次丢弃、原子标签、mailbox 清理、设备级采集器
  复用、空包名防护和 resolver 动态 pid；CI 语法检查纳入新模块。Python **188 → 194**，
  JS **127** 保持全绿。

## v76（2026-09-17 · Codex）—— FPS 短窗精确聚合与报告展示

- **架构边界前移**：`collector/sampling.py` 新增纯函数 `summarize_fps_windows()`，
  聚合口径不再散落在 `main.py` 或前端，为后续 `SampleAggregator` 拆分提供稳定边界。
- **Jank 精确合并**：SF/gfxinfo 采集结果新增 `jank_count` / `jank_total`；同一报告点
  内多个 0.5 秒窗口按分子/分母合并，整份报告再按全部有效帧数加权，不做百分比平均。
- **帧时间口径**：P95/Max 只对确有新增帧间隔的窗口取峰值，沿用旧帧时间的静止窗口
  不重复计入。页面明确显示“短窗峰值”“帧加权”，避免把新口径伪装成旧指标。
- **兼容性**：新值写入 `fps_window_summary`；顶层 `fps` 原字段不覆盖。旧 JSONL 无
  summary 时，`fpsMetric()` 自动回退旧字段。CSV/XLSX 增加独立短窗列，不覆盖旧列。
- **前端**：实时/历史/自包含报告的曲线、Pin 浮层、快照、统计栏和汇总卡统一读取
  新口径；资源版本 **v66 → v67**。
- **测试**：增加窗口加权、静止沿用排除、旧数据回退、导出字段和报告统计测试；
  Python **184 → 188**，JS **113 → 127** 全绿。

## v75（2026-09-17 · Codex）—— 保留 FPS/Jank 高频采样窗口

- **改动**：新增 `collector/sampling.py` 的线程安全 `MetricMailbox`。各指标继续提供
  现有 latest 快照，同时 FPS 的每个 0.5 秒结果进入有界 pending 队列；主循环每次
  落盘原子 drain 为 `fps_windows`，包含递增 `seq`、真实 `sampled_t_ms` 和完整数据。
- **兼容性**：顶层 `fps` 仍是最新快照，旧前端、CSV/XLSX 和历史读取逻辑不变；meta
  新增 `schema_version: 2` 与 `fps_window_mode: "preserved"`。新字段为纯追加。
- **防静默丢失**：队列上限 256（约 128 秒）；极端阻塞溢出时写入
  `fps_windows_dropped`，而不是悄悄覆盖。目标热切换会清空旧窗口，并以 generation
  丢弃切换前已启动但切换后才返回的旧目标采样。
- **测试**：新增 `tests/test_sampling.py` 6 项，覆盖“前窗卡顿、后窗正常”仍同时保留、
  drain/latest 语义、溢出计数、热切换清空、相对时间序列化和并发发布；Python **178 → 184**，
  JS **113** 全绿。
- **验证边界**：本轮先解决采集与 JSONL 层的不可逆丢失；报告如何展示/聚合多个短窗
  将单独版本化，不在本轮用简单平均或无说明的最大值改变现有口径。

## v74（2026-09-17 · Codex）—— 修正内存健全性聚合与配置契约

- **Swap PSS 误报修复**：`collector/data_health.py` 抽出统一的
  `_rss_lt_pss_violation()`；实时单点和整份报告扫描均先用
  `effective_pss = pss_kb - swap_pss_kb` 再与 RSS 比较。历史数据缺少
  `swap_pss_kb` 时按 0 处理，保持原判读兼容。最新长测 `20260916_151858`
  的 1505 个点重新扫描后不再产生 `rss_lt_pss` 误报。
- **配置契约修复**：`collector/main.py` 新增 `resolve_capture_timing()`，使 README
  已公开的 `config.json.interval_ms` / `duration_s` 真正生效；优先级为
  命令行 > 配置文件 > 内置默认值，并拒绝非数字、非正采样间隔和负时长。
- **测试**：`tests/test_data_health.py` 增加 Swap 单点、报告聚合、旧数据兼容 3 项；
  新增 `tests/test_main_config.py` 7 项；Python **168 → 178**，JS **113** 全绿。
- **影响面**：不改变采集指标算法、JSONL 格式或前端资源；无需提升 `?v=`。
- **验证边界**：已完成单元测试和真实历史数据离线扫描；配置值的真机定时停止待
  下次连接设备时抽检。

## v73（2026-09-17 · GPT 交付 + 主会话独立验证）—— 修正锁定蓝线「像素 ↔ 类目索引」换算

- **改动**（`fix/pin-line-index` 分支，merge commit `bb755e4`）：
  `web/assets/app.js`——新增 `GRID_PAD = { left: 56, right: 24 }` 作为 grid 边距**单一来源**
  并让 `baseOption().grid` 引用它；新增纯函数 `pixelToIdx()` / `idxToPixel()`（grid-aware，
  含 clamp 与退化输入保护）并挂到 `window.PerfCharts`；`_pinIndexAtLocal()` 主路径由
  `{xAxisIndex:0}` 改为 `[{seriesIndex:0},{gridIndex:0}]`（**ECharts 5.5.0 下前者恒返回 `null`**），
  兜底改走 `pixelToIdx()`；`_pinPlaceTip()` / `_pinPlaceLine()` 兜底改走 `idxToPixel()`；
  finder 全失效时 `console.warn` 一次（带各 finder 返回详情，不再静默）；`convertToPixel`
  保持 `{xAxisIndex:0}`（实测反向只有它有效，不可"统一"）；
  `tests/test_nearest_cat.js`（+19 断言，94 → **113**）；`web/index.html` + `web/report.html`
  （资源版本 v65 → **v66**）
- **为什么**：用户长测报告实测——悬停白线与锁定后显示的数据时间不一致
  （66s→118s、642s→661s、1456s→1435s，**偏差随位置变号**），且锁定后拖动时间条蓝线会自行偏移。
  根因：像素→索引主路径从未生效（`convertFromPixel({xAxisIndex:0})` 恒返回 `null`），
  代码长期走"**未扣除 grid 左右边距**"的兜底公式（左段索引偏大 → 数据偏晚；右段偏小 → 数据偏早）
- **影响面**：仅报告页交互定位（锁定线 / 浮层 / 快照条显示的时刻）；采集、存储与统计口径不变
- **验证**：① JS 断言 94 → **113** 全绿；② **主会话独立复现**（Edge headless 打开真实长测报告
  `20260916_151858`，模拟点击 10% / 25% / 50% / 95% 处）：pin 显示时间与该像素应有类目时间的
  偏差由修复前的 **+52 / +19 / −21** 变为 **0.0s**（仅左段 −1.0s，属取整边界）；
  ③ `convertToPixel(xAxis)` 与 `idxToPixel()` 差 **≤0.4px**（证明线定位与数据索引同源 → 问题 2 一并消解）；
  ④ Python **168** 测试回归全绿
- **过程记录**：该修复由外部模型（ChatGPT）按《给ChatGPT-Pin蓝线修复任务书.md》产出；
  主会话负责隔离到独立分支 → 跑断言 → 真实浏览器复现验证 → 合并

## v72（2026-09-17 · 主会话）—— 取消 exe / 安装包分发，统一改为 zip 分发

- **改动**：
  - **删除**全部 Windows 安装/打包链路：`perfdog.spec`（PyInstaller 配置）、`packaging/`
    （`build_exe.ps1`、`build_installer.ps1`、`launcher.py`、`perfdog_installer.iss`，
    以及未纳入版本库的 `installer_output/`（12 MB 安装包）与 `staging/`）、
    `.github/workflows/build.yml`（打包 exe + 发 Release），并清理本机 `build/`、`dist/` 产物
  - **新增** `tools/make_zip.py` + 根目录 `make_zip.bat`：生成
    `share/perfcollect-cn-<版本>-<日期>.zip`（版本号自动从本文件状态头读取；脚本内自检
    「不含 collector/output/」）；新增 `分享说明.md`（打包后作为包内「先读我-使用说明.md」）
  - **新增** `.github/workflows/tests.yml` 取代 `build.yml`：只跑 `py_compile` +
    Python 单测 + JS 断言 + 打包脚本冒烟（CI 从"出安装包"变为"守代码质量"）
  - `start_perfcollect.bat` / `start_dashboard.bat`：加 `--with openpyxl`
    （仅 XLSX 导出需要，其余功能零依赖 → zip 分发开箱可用）
  - `.gitignore`：清理打包相关规则（`build/`、`dist/`、`*.spec`、`packaging/staging/`、
    `packaging/installer_output/`），新增 `share/`
  - 文档同步：`README.md`（获取章节重写为 zip + 明示取消 exe 及原因）、
    `使用教程-保姆级.md`（"方式 A/B"重写为 zip/源码，数据路径与 FAQ 里所有 exe 引用改为源码路径）、
    `AGENTS.md`（"打包 / CI"行 → "分发 / CI"）、`打包后操作流程与改动需求.md`（补 v72 作废说明）
- **为什么**：用户要求「剔除项目内所有跟 Windows 安装相关的逻辑代码，以后分享都走 zip，
  不用走 exe，避免很多问题」——打包 exe/安装包带来的 SmartScreen 拦截、杀软误报、
  签名缺失、安装残留与卸载、构建环境依赖等问题一次性消除
- **影响面**：**仅分发方式变化**；运行时行为完全不变（采集器 / 看板 / 报告功能与源码运行一致，
  因为打包版本来就是 `python main.py --web` 的等价物）；采集数据与版本库隔离关系不变
- **验证**：① 实际执行 `tools/make_zip.py` → 生成 41 文件 / 约 0.5 MB 的分发包，
  自检输出「确认不含采集数据：OK（无 collector/output/）」；② 解压到临时目录后
  **从包内直接运行 `python collector/main.py --help` 正常**（证明分发包自包含可用）；
  ③ Python **168** 测试 + JS **94** 断言全绿（无功能回归）；
  ④ 全仓库检索 `perfdog.spec` / `build.yml` / PyInstaller / Inno Setup 的代码级引用为 0
  （仅文档中保留"已取消"的说明）

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
