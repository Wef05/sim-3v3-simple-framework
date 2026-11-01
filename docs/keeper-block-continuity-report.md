# 守门员不可达封堵提前停步修复

日期：2026-11-01（Asia/Shanghai）。实现与离线验收完成；完成一次 60.114 秒裁判计时仿真。该场未触发射门封堵分支，不能据此宣称实战扑救效果已验证。

## 基线、复现与最小修改

- 开工重新 `git fetch origin`；基于最新 `MAIN` 的 `403d39883512012cc356f4b733e0ebaf7bc407bc`（含导航 PR #1）。远端另有旧 `main`，本次 PR 目标是实际开发主线 `MAIN`。
- 独立分支 `codex/keeper-block-continuity`；worktree `/Users/limingzhe/.codex/worktrees/keeper-block-continuity/sim-3v3-simple-framework`。主工作区未修改。
- 用户确认重新复现。本次是固定观测的离线复现，不声称找回了用户最初看到的丢分片段。
- 门将在 `(-6.5, 0)`、朝向 0；球沿 y=0.8 以 vx=-6 m/s 接近。原版在球 x=-5 时给出 `block:best_effort`，目标 y 仅 0.02356；球 x=-6、-6.5 时切成 `block:unreachable_hold` / FaceIntent，尚未覆盖球路就停止平移。
- 原因：`block_target` 把短时可达位置当导航终点；每帧扣除反应时间，预算变短时终点缩回当前位置。控制器还会针对这个过近终点减速。
- 仅改变 `src/utils/goalkeeper.py` 的不可达候选：仍按预计可达位置评估漏球距离，但向守门区域内的理想接触点移动；检查到完整目标的直线路径安全，仍返回 `reachable=False`。已有覆盖、障碍阻挡和区域限制保留，不改变预测模型、核心架构、角色或接口。
- 修复后同一输入全部输出 `block:best_effort` / MoveIntent，目标 y 约 0.60，实际控制器输出正向侧移速度。新增连续输入、受阻停留、已覆盖停留三项回归测试。
- 未新增功能；看板已有动作标识足以反映结果，不改变看板协议和 UI。

[同输入对比](keeper-block-continuity-evidence/reproduction.json)、[原版失败日志](keeper-block-continuity-evidence/baseline-regression.txt)、[实际部署差异](keeper-block-continuity-evidence/source.patch)。

## 检查结果

- 新增 3 项回归：原版连续来球测试的三个子场景失败；修复后 3 项通过（含实际控制器平移输出检查）。
- 最新远端原版完整检查：64 项中 63 项通过，1 项导入错误；修改后 67 项中 66 项通过，同一项导入错误。`checks/test_goalkeeper_replay.py` 依赖未提交的 `checks.goalkeeper_replay_cases`，未当作通过，也未为此扩大修改范围。[修改后日志](keeper-block-continuity-evidence/fixed-tests.txt)。
- 补充加载主工作区已有、远端缺失的守门员测试与固定回放数据，用本任务 src 运行：39 项全部通过。此结果依赖本机文件，不等同干净克隆的全套通过。[日志](keeper-block-continuity-evidence/local-existing-guard-tests.txt)。
- `node checks/test_dashboard_guard.cjs`、`node checks/test_dashboard_navigation.cjs` 和 `git diff --check` 均通过。

## 构建与实验身份

通过 Booster Studio 正式入口加载本任务 worktree、构建并部署；开赛前裁判确认上一场 finished，无运行中测试。仿真窗口开工前已持续打开超过 60 秒，部署后核验双方 Active 与看板新鲜。

我方 `com.example.lqinternalmatch` 2.0.3，对手 `com.booster.default3v3ai`；虚拟机器人 `4x2a0`、容器 `4x2a0-k1`。逐文件比较本任务 44 个 Python 文件、构建包与实际 runner 指向的部署目录：43 个完全一致；顶层 `__init__.py` 只有说明文字，被构建工具置空，已单独确认无执行逻辑。

[源码/构建包/部署摘要](keeper-block-continuity-evidence/source-identity.json)、[运行配置](keeper-block-continuity-evidence/runtime-config.json)、[runner 日志](keeper-block-continuity-evidence/run01/runner.log)。构建包保留在本任务 `build/com.example.lqinternalmatch-2.0.3.agent`，包 SHA-256 见摘要。实验源码为基线加 `source.patch`，随机种子不可控。

## 一次仿真与四类分析

### 比分与时长

初始 0:0，结束 0:0；裁判主时间从 600.0 降至 539.886，实际经过 **60.114 秒**。固定 600 秒赛制内主动结束（`operator_finish`），非自然完赛，不计完整比赛胜平负。裁判与看板终态均确认。197 次连续状态样本，采样系统跨度约 180.23 秒，包括首次 READY 和停止摆球等待；不能用该跨度代替比赛时间。

### 机器人行为复现

以下为真实采集帧，时间是 `context.now` 的仿真秒数（沿用已有模拟器，未从零开始）。

| 帧 ID / 仿真时间 | 观测、决策与表现 |
| --- | --- |
| 342172 / 840.060 | 本场首次 READY。双方球员入场，未持续停在场外出生点。 |
| 343169 / 862.792 | PLAYING 开始；1 号请求向 3 号附近传球，2 号 `guard:set` 向 (-6.5, 0) 预站位。动作请求不能当作已踢到球。 |
| 343353 / 869.666 | 球位于 (-3.062, 4.543)，3 号处于 attack；2 号随球横向预站位。随后裁判将边线球判给对手。 |
| 343600 / 875.640 | 对方边线球限制期间，2 号 `guard:set:restricted`，继续预站位；1/3 号为 freeman。 |
| 343890 / 882.372 | 2 号出现一帧 `guard:anticipate:ray`。本场未出现任何 `guard:block:*` 帧。 |
| 344118 / 897.400 | 球停在 (-6.834, -0.714)，2 号仍是 `guard:set`；1 号在球门线后附近请求射门，不能据请求确认触球。 |
| 344376 / 912.736 | 裁判因无人触球重启，双方切到 READY。 |
| 344948 / 925.950 | 看板确认 FINISHED，最终 0:0。 |

完整动作切换见 [guard-transitions.json](keeper-block-continuity-evidence/run01/guard-transitions.json)。该时间线文件保留采集窗口最前六帧的旧终态，正式单场回放已排除这些开赛前帧。

### 失分原因

本场无失分，无可归因的丢球片段。0:0 不能证明本修复阻止了某次失分；且本场没有射门封堵分支，实战触发覆盖不足。原版错误与修复后消失的直接证据来自固定输入回归。

### 未得分原因

裁判事件记录：比赛经过 4.412 秒时我方 3 号触球；7.076 秒时球从 (-3.188, 4.640) 出边线，对方获边线球；16.584 秒时对方 1 号触球。之后球留在我方门前附近，46.870 秒触发 `global_game_stuck`：约 30.01 秒无人触球，重新坠球开赛。本场进攻未持续转化为对方门前机会，较长时间消耗于边线球和无人触球阶段。

这由裁判触球/出界事件和球位置支持；无法仅凭看板的 kicking 标志断定具体踢球失败原因，也不能将无人触球归因于本次未被触发的封堵修改。不自动追加比赛或调整其他战术。

## 数据与限制

- [本场原始回放（gzip，解压后可导入看板）](keeper-block-continuity-evidence/run01/dashboard-team1.json.gz)：2,785 帧，未压缩 61,628,797 字节，压缩 4,762,209 字节；帧 ID 342172–344956，仿真时间 840.060–926.130，无时钟回退。起止包含 READY/SET/FINISHED，所以跨度大于裁判主时间。
- [完整采集窗口](keeper-block-continuity-evidence/run01/capture-window.json.gz) 额外保留开赛前 6 帧旧终态，未丢弃原始采集证据。正式回放以首次 READY 为开赛边界，固定结束帧导出。
- 最大相邻仿真帧间隔 0.338 秒，遥测为尽力传输，不保证每个决策帧完整；不能从动作请求推断动作执行成功。
- [裁判结果](keeper-block-continuity-evidence/run01/result.json)、[连续状态](keeper-block-continuity-evidence/run01/samples.json)、[事件](keeper-block-continuity-evidence/run01/events.json)、[采集摘要](keeper-block-continuity-evidence/run01/summary.json)。本地另保留未压缩 dashboard-team1.json。
- 按 AGENTS.md 保留看板历史，未执行赛前/赛后清空。本场已结束，未追加比赛。
- 继续侧移不保证能挡住来球；安全路径全段受阻时仍允许保持原地。静态路径检查及保守速度模型不能涵盖所有动态接触情形。
