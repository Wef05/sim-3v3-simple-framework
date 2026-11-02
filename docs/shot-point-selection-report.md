# 按被截概率选择射门点

状态：实现与离线检查完成；Studio 界面连接失败，构建、部署、1 分钟仿真及效果验证未完成。以草稿 PR 交付，不满足完整开发流程完成条件。

## 基线与范围

- 用户确认基线：`fa90cfc8a18418fd71d40aa7f274e79f561a66ee`。
- worktree：`/private/tmp/shot-point-selection`；分支：`codex/shot-point-selection`；PR 目标：`MAIN`（远端核实与基线相同）。
- 主工作区 `.booster-studio/project.json`、`.gitignore` 的未提交修改没有转入本任务。
- 入口是 `Player.attack(0)`。显式队友/坐标目标保留原流程；没有新增自动传自由人路线决策。

## 行为与数据

复用球门内 7 点及现有被截概率计算器，评分为 `max(0, 1 - 被截概率 - penalty)`。最左、最右两个候选点 `goal_edge=true`，penalty 为 `SHOT_EDGE_PENALTY=0.10`，即减 10 个百分点，其余为 0。例如两端点原始 80% 得 70%，中央原始 75% 得 75%，选择中央。只有 status=ok 且完整、有效概率的点参与；未知、partial、结构阻挡、零分排除，不因其他点未知而抹去有效点。

每帧使用观测球位和延迟补偿球位复核候选的门柱/球网通道。分数保留 12 位小数比较以消除浮点尾差；同分优先靠近中央，再按稳定编号。最终射门点同时用于 KickIntent 的显式目标和追球球后方向，仍按 shot 力度执行。没有可用正分点时使用 `KickTarget.goal()` 向中央射门。中央本身被实体结构挡住时，原有安全传球替代/停止保护依旧生效。

遥测新增 `attack_choice`，记录原始被截概率、未扣分成功率、惩罚、扣分后评分、各项排除原因、选择点、实际目标和执行类型；帧准备清空旧输出，避免换角色遗留。看板新记录显示扣分明细；旧数据渲染保留。`scoring_probability` 仍表示原始路线估计，不将原始概率混写成扣分后的评分。

修改文件：`src/utils/shot_selection.py`（纯选择逻辑）、`src/utils/attack_targets.py`（端点标记）、`src/param.py`（惩罚参数）、`src/player.py`（实际目标）、`src/framework/decision_frame.py`、`src/telemetry.py`、`tools/tactics_dashboard/dashboard.js`；测试与本报告随附。

## 离线验证

- 基线 105 项：104 项通过，1 项加载错误。`checks/test_goalkeeper_replay.py` 依赖未跟踪的 `checks.goalkeeper_replay_cases`，独立 worktree 中不存在。没有从主工作区复制测试依赖或掩盖错误。
- 变更后 116 项：115 项通过，同一项加载错误。原场界测试改为检查 attack 角色前缀和中央兜底原因，其真实追球入场检查保留。
- 专项 20 项全部通过，包含新增 11 项射门测试、8 项结构阻挡测试、1 项开球力度测试。
- 4 个 Node 看板检查通过，覆盖本次评分/兜底/阻挡及已有守门员、导航、自由人展示；JavaScript 语法检查和 `git diff --check` 通过。
- 原始结果：[基线](shot-point-selection-evidence/baseline-tests.txt)、[全量](shot-point-selection-evidence/full-tests.txt)、[专项](shot-point-selection-evidence/focused-tests.txt)、[看板](shot-point-selection-evidence/dashboard-tests.txt)。

## 仿真尝试及四类分析

尝试标识：`shot-point-selection-preflight-20261102`，仅赛前检查，没有开赛。

本地 Docker 的 `4x2a0-k1` 正在运行；[赛前裁判原始状态](shot-point-selection-evidence/preflight-status.json) 显示旧比赛已 finished，旧比分 0:1，剩余 539.624 秒，结束方式 operator_finish。该数据来自本任务部署之前，不计为本次实验。Studio 初次读取成功显示旧比赛结束；切换项目窗口时超时，随后两次按实际 bundle ID 重连都返回 `Computer Use server error -10005: timeoutReached`。依技能的有限恢复规则停止重试。未重置、未终止其他比赛、未构建部署本任务代码，未清除看板历史。

| 必填项 | 本次结果 |
| --- | --- |
| 比分 | 没有启动本任务比赛；双方运行 Agent 身份未核实，初末比分不适用，仿真时长 0 秒，结束方式不适用，无本场终态。旧场 0:1 不归属本任务。 |
| 机器人行为复现分析 | 没有本场回放，不能提供实际机器人时间线；离线意图检查不能证明触球或实际射门。 |
| 失分原因分析 | 没有本场数据，无法分析；未引用旧场失分归因。 |
| 未得分原因分析 | 没有本场进攻片段，无法分析；不能认定未得分是策略问题或成功率不足。 |

本场导出帧数 0，无回放文件，大小/采集范围不适用。源码文件摘要见 [source-sha256.json](shot-point-selection-evidence/source-sha256.json)。没有构建包或部署产物，不能核实三方摘要一致；未控制随机种子。运行环境见 [runtime.json](shot-point-selection-evidence/runtime.json)。

## 风险与剩余工作

被截模型采用假设球速、有限采样、敌人运动占位模型；扣 10 个百分点为用户批准的策略设定，未标定为实际射门精度。缓存仍每 0.5 秒刷新，期间概率可能滞后；实际目标的较细风险估计可能与选点粗评不同，选点不会根据其再次重排。没有新增跨帧迟滞，同分处理确定但分数变化可改变目标。

恢复 Studio 界面后，需再次核验资源归属，加载本 worktree、构建部署并核验源码/包/运行身份，再运行一次按裁判计时的 1 分钟测试，保留历史并按本场边界导出回放，补齐四类分析。完成前 PR 保持草稿，不宣称效果达标。
