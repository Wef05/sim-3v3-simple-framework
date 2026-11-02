# 普通进攻自动踢球限时交接

## 范围与结论

按用户最终批准，仅实施方案 1：收紧普通进攻自动踢球的进入、持续和退出，增加看板状态。方案 2（新的球路受阻兜底、门柱/球网脱困）不实施。原有 `blocked_attack_target` 选择逻辑保留，选择出的方向同样必须满足准备条件。守门员继续使用独立清球策略。

原来的普通进攻只按距球 ≤2.0 m 进入、>2.5 m 退出；进入时没有站位/朝向要求，也没有单次接管预算。SDK 自动踢球独占底盘，因而“进入踢球”可能实际变成长期由底层接近、转向。新逻辑把接近和对齐交还已有导航，再交接短时踢球请求。未改 SDK 内部控制器、角色分配、球路评分或导航架构。

本次验证了限时退出和看板记录，**没有验证原 1:1 比赛的门柱场景已经彻底解决**。本场普通进攻球员未进入门柱 1 m 范围；用户暂缓的脱困策略仍是未实施项。

## 行为与修改文件

- `src/utils/attack_kick.py`、`src/player.py`、`src/param.py`：每个球员持有普通进攻请求状态；距球 ≤0.55 m、球在身前、朝向误差 ≤20°、球后 0.35 m 准备点误差 ≤0.15 m 才进入。观测与延迟补偿球位均需通过，观测年龄 ≤0.25 s。准备点必须在允许区域且接近路线安全。
- 持续时允许距球 ≤0.75 m、朝向误差 ≤35°；球在身后、观测/目标失效、接近受阻、角色/阶段切换、执行拒绝或时钟回退取消请求。单次请求达到 1.2 仿真秒后的首个控制帧取消，退出后冷却 0.5 仿真秒，重复帧不会刷新预算。
- 不满足进入条件时，用已有导航自主接近和对齐；准备点不可用则停止。这可能增加近门柱等待，不能当作脱困方案。
- `src/actions/controller.py`、`src/actions/executor.py`、`src/framework/robot_backend.py`：区分请求、接受、拒绝、失败和取消失败。启动/更新异常后尝试关闭 SDK；关闭失败保留底层占用标记，禁止普通速度抢占，后续释放重试。
- `src/telemetry.py`、`tools/tactics_dashboard/dashboard.js`：新增 `auto_kick` 和 `kick_control`，显示准备状态、预算、冷却、退出原因、朝向/站位误差、准备点。明确“后端接受不代表已出脚或触球”，旧回放缺字段显示未记录。
- `checks/test_auto_kick.py` 新增回归；更新开球、受阻进攻、看板用例，以及两个已过时的重启防守测试替身，保留其行为断言。说明见 [看板文档](tactics-dashboard.md)。

## 代码及运行身份

- Worktree：`/Users/limingzhe/.codex/worktrees/bounded-auto-kick/sim-3v3-simple-framework`；分支 `codex/bounded-auto-kick`；基线 `ddf350a74c5ae4cb584d1160e690b41f1b4e1a27`（MAIN）。任务代码在提交前构建运行。
- Studio 通过本 worktree 正式构建部署；容器 `4x2a0-k1`。产物 `com.example.lqinternalmatch-2.0.3.agent`，SHA256 `253cdb07746c07170e1b61e19191906c9933b07f6f807b822108382913f97c89`。
- 46 个 Python 文件逐个核对源码/产物/实际部署：45 个完全一致，顶层 `__init__.py` 仅被打包器清空说明文字。摘要见 [identity-check.json](bounded-auto-kick-evidence/run01/identity-check.json)。
- 实验后唯一源码变化为辅助类类型标注、类型导入及更准确的计时说明；去除这些非行为项后的 AST 相同，见 [验证记录](bounded-auto-kick-evidence/run01/post-run-source-check.json) 与 [实际部署版本](bounded-auto-kick-evidence/run01/deployed-attack-kick.py)。没有根据比分调整战术或追加场次。
- 双方分别为 `com.example.lqinternalmatch` / `com.booster.default3v3ai`，runner 记录双方 Active。运行标识：`local-match` + `startedAtWallTime=1793588817.711352`。未设置/控制随机种子。本场基线与原 1:1 片段的运行分支不同，不作为新旧策略的受控胜率比较。
- 原主工作区已有修改未转移或覆盖。Studio 在本 worktree 生成的 `.booster-studio/project.json` 修改不纳入提交。

## 测试

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 修改前完整 Python 检查 | 95 项中 92 通过，3 个导入/测试替身错误 | [基线](bounded-auto-kick-evidence/baseline-tests.txt) |
| 修改后完整 Python 检查 | 111 项中 110 通过，1 个既有导入错误 | [结果](bounded-auto-kick-evidence/current-tests.txt) |
| 自动踢球、导航、开球、受阻进攻、重启防守专项 | 63 项通过 | [专项](bounded-auto-kick-evidence/focused-tests.txt) |
| 看板守门/自动踢球、导航、自由人 Node 检查 | 全部通过 | [看板检查](bounded-auto-kick-evidence/dashboard-tests.txt) |
| 实际浏览器 | 成功导入本场 2768 帧；显示请求 0.86 s / 预算剩余 0.34 s；无脚本错误 | [截图](bounded-auto-kick-evidence/run01/dashboard.png) |

完整检查的剩余错误为仓库缺少 `checks.goalkeeper_replay_cases`，导致 `test_goalkeeper_replay` 无法导入；基线中同样存在。未计为通过，也未删除该测试。另两个基线错误来自不完整的 `SimpleNamespace`，已用实际 Player 测试对象替换。补全类型说明后再次运行完整检查，结果相同。`git diff --check` 通过。

## 一次 1 分钟仿真

### 比分与有效性

- 初始 0:0，结束 **0:1**（Challenger / Booster AI）；裁判计时从 600.000 降至 539.938，实际 **60.062 仿真秒**。
- 固定 600 秒赛制下按裁判计时主动结束，`endReason=operator_finish`、`isFinal=true`。不是自然完赛，不计入完整比赛胜负统计。系统运行约 226.525 秒，不能替代仿真计时。
- 模拟器窗口就绪后等待超过 30 秒，再验证双方 Active、裁判就绪及新鲜遥测。首次 PLAYING 帧 800572 中双方六名球员均已在场内，位置见 [analysis.json](bounded-auto-kick-evidence/run01/analysis.json)。没有场外出生点加载失败。
- 按仓库要求保留共享看板历史，没有清除。session `f8f657e9de8b479cbaf4b6c299a0c010`，导出边界 `(799689,802457]`，共 **2768 帧**。导出 JSON 64,185,561 字节，gzip 5,614,436 字节；包含准备阶段与终态。
- 录制系统时间跨度 227.662 秒，`context.now` 范围 59.430–143.566，未回退；最大相邻录制间隔 1.007 秒、最大相邻仿真间隔 0.572 秒。遥测不是每个控制帧，不能声称无丢帧或从回放精确测出 SDK 的物理停止瞬间。

### 机器人行为复现

以下 ID 是导出帧 ID；看板显示的 Agent sequence 可从 [keyframes.json](bounded-auto-kick-evidence/run01/keyframes.json) 对照。`now` 为仿真时钟，非比赛已用时。

| 球员与帧 | 行为证据 |
| --- | --- |
| 1 号，800717 → 800742 | 开球传 3 号；进入时距球 0.457 m、朝向 0.223°、站位误差 0.148 m。`now=85.778` 请求，86.982 已退出并冷却 0.5 s，退出原因 timeout。观测到的请求→退出间隔 1.204 s。 |
| 1 号，800772 → 800812 | 重新满足条件后请求；裁判在剩余 591.142 s 记录 home:1 触球（事件 7）。随后开球阶段结束，因 phase_changed 退出。 |
| 3 号，800897 → 800913 | 接续进攻向球门出球，首个捕获请求帧已过去 0.08 s。朝向误差到 35.837°时退出，记录 misaligned。 |
| 1 号，801852 → 801876 | 重新获得近球机会，进入时距球 0.405 m、朝向 0.673°、站位误差 0.137 m；1.230 s 后首个观测退出帧，原因 timeout。裁判稍后在剩余 562.078 s 记录 home:1 触球，说明停止请求与物理触球不能视为同一瞬间。 |

共捕获 4 段普通进攻后端接受请求；2 段超时、1 段阶段切换、1 段朝向退出。请求中最大记录 elapsed 为 1.178 s，退出帧均已 `accepted=false`；这些是请求/控制层证据，不是踢脚动作完成证明。守门员未触发自动踢球。

### 失分原因分析

裁判事件 21 在剩余 **543.512 s** 确认失球。

- 帧 802246（剩余约 545 s）：球约 (-4.902,0.775)，2 号约 (-6.377,0.648)，执行 `guard:anticipate:ray`，预判射线到门线 y≈1.229；1 号距球约 2.42 m，3 号仍在中前场。
- 帧 802268（剩余约 544 s）：球已到 (-6.540,0.169)，2 号仍约 (-6.429,0.754)，改为 `guard:block:best_effort`，测量预测门线落点 y≈-0.010、剩余到门时间约 0.252 s。1 号因准备点不可用停止，距球仍约 2.12 m。
- 直接证据：守门预判与随后实际球路有偏移，近门时进入紧急封堵，但仍失球；两名其他球员没有在该窗口完成拦截。
- 合理推断：守门预判调整及横向回补窗口不足参与了失球。缺少底层逐步执行、碰撞和全部触球细节，不能把守门单一分支或新踢球阈值确定为唯一原因。该守门逻辑本次未改。

### 未得分原因分析

- 开球后 1 号先准备，再短时请求；第一次超时尚未产生可辨识球位移动，第二段请求后裁判确认触球。准备加短时预算可能延迟出球，这是收紧接管的实际权衡。
- 3 号随后向球门方向出球；现有偏角观测器估计右偏 15.3°，该估计不是裁判触球确认。球最终由约 (7.146,-2.685) 出端线，裁判剩余 580.336 s 判对方球门球：这次进攻未射入门框范围。
- 1 号第二次中前场机会在剩余约 564–562 s 请求并超时退出，随后有 home:1 触球；剩余 558.160 s 裁判记录对方 3 号触球。没有形成进球，不能仅按请求记录判定射门成功。
- 本场无得分，且时间短、无对照组，不能据此评价新阈值是否提高或降低总体进球率。

## 原始数据与复查

[完整回放 gzip](bounded-auto-kick-evidence/run01/dashboard-team1.json.gz) 解压为 JSON 后直接导入战术看板；本地同目录同时保留未压缩 JSON。原文 SHA256：`7853e5bb412a651e19c87b5c763e48ef556dec99044058db96fa7f07e4979fce`。

[裁判连续样本 gzip](bounded-auto-kick-evidence/run01/status-samples.jsonl.gz)、[事件](bounded-auto-kick-evidence/run01/events.json)、[最终结果](bounded-auto-kick-evidence/run01/result.json)、[runner 日志](bounded-auto-kick-evidence/run01/runner-end.log)、起止响应与边界均保留。运行 `python3 docs/bounded-auto-kick-evidence/analyze_replay.py` 可重算帧范围、退出段和关键帧。

## 风险与剩余工作

1. 0.55/0.75 m、20/35°、1.2/0.5 s 为保守初值；可能增加对齐耗时或截断尚未完成的踢球步态，未经过多场性能调参。
2. 预算按仿真时间在控制帧检查；暂停时不消耗仿真预算。SDK 停止调用失败只能重试并报告，不能保证硬实时释放或物理瞬时停止。
3. 本场普通进攻球员距任一门柱最小距离：1 号 1.370 m、3 号 2.707 m，没有重现原门柱卡住片段。门柱安全检查的回归通过，不等于验收了所有门柱/球网情况。
4. 本次批准范围已实现并验证；原始“球员不会卡在门柱附近”的全面效果仍未达成可验证结论。新的脱困/球路兜底、守门失球改进及补全既有缺失回放测试数据均未实施。

## PR 集成状态（提交后发现）

PR [#10](https://github.com/Wef05/sim-3v3-simple-framework/pull/10) 已创建，尚未合并。创建时目标 MAIN 已前进到 `fa90cfc8a18418fd71d40aa7f274e79f561a66ee`，合入 #9（允许跨场地边线导航、移除强制回场）。本分支基于此前的 ddf350a，GitHub 报告冲突，主要涉及 `src/player.py` 和受阻进攻测试；此外 #9 删除了本分支仍使用的 `clamp_target`，不能只做文本冲突消除。

建议保留 #9 的场外站位语义，去除本分支对旧场内限幅的依赖，同时保留自动踢球距离、朝向、时间及实体障碍检查，并重新执行相关离线回归。按 AGENTS.md 第 7 节，合并/rebase 需单独授权；目前未执行，已请求用户批准。上面的测试与仿真结果只属于本任务提交，不代表与最新 MAIN 集成后的结果。
