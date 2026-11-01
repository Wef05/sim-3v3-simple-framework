# 场外静止修复与验证记录

## 范围与基线

- 用户批准：修复开局 READY 入场及处罚解除后场外静止，执行一次 1 分钟仿真，导出看板并提交 PR。
- 基线：远端大写 `MAIN`，`e8b4d9d74f90530f0690146f8b523f85d84ab767`。
- 分支：`codex/outside-field-recovery`。
- 工作区：`/Users/limingzhe/.codex/worktrees/outside-field-recovery/sim-3v3-simple-framework`。
- [源码摘要](outside-field-recovery-evidence/source-identity.json)。主工作区已有修改没有带入本任务。

## 问题与修复

1. A* 原本只找起点周围两格内的场内节点。较远的场外起点直线受阻时无网格入口；局部先转后走又可能不断换向。新增真实起点所在行/列，不按场外距离扩展网格；每条边不得增大越界量，所有障碍物线段检查保留。
2. 导航安全边界外采用保持朝向的全向平移，入场后恢复原有朝向控制，避免入口转向循环；最终预测碰撞与队内复核保持生效。
3. PLAYING 中可行动球员位于实际场外时，先返回最近场内安全点，再恢复战术角色；该行为不依赖球观测，覆盖开球等待者。READY 继续走编号对应阵型。
4. 暂停标志优先于 READY。受罚、倒地、模式未就绪、缺失/无效位置时不能通过回场分支获得行动权限；下发前仍复查处罚等状态。对方重开球保护距离沿用协调层。

新增直接证据：[参考回放帧及离线复算](outside-field-recovery-evidence/reference-replay.json)来自另一任务已结束的失败开局，不能算本任务实测。帧 607050–607051 的三名机器人均在 `y≈4.98`，向外估计速度约 0.000015–0.000028 m/s，旧导航连停车都报 `field_boundary`。修复后允许不向外的命令在同一时刻停车预测的漂移范围内制动，不增加实体碰撞豁免；这些帧复算后均可向内移动。

## 离线验证

- 新增 `checks/test_field_reentry.py`：8 项测试，涵盖 READY 三个固定站位、处罚解除且无球、决策后再次受罚、停止阶段、开球等待者、对方重开保护、四边绕障和四种朝向的连续入场，以及场外制动漂移/禁止主动外移。
- 与现有导航测试合计 37 项通过：[输出](outside-field-recovery-evidence/focused-tests.txt)。
- 新增测试复制到原始基线后 6 项失败：[回归证据](outside-field-recovery-evidence/regression-on-baseline.txt)。
- 全套检查：基线 71 项中 70 项通过、1 项导入错误；本次 79 项中 78 项通过、同一项导入错误。原因均为仓库缺少 `checks.goalkeeper_replay_cases`，没有补造该回放数据。[基线](outside-field-recovery-evidence/baseline-tests.txt)、[本次](outside-field-recovery-evidence/current-tests.txt)。
- `node checks/test_dashboard_guard.cjs`、`git diff --check` 通过。
- [离线逐帧轨迹](outside-field-recovery-evidence/offline-trajectories.json)：初始 `(0, 5.5)`，入口障碍 `(0, 4.7)`，目标 `(0, 0)`。四种朝向分别在 34、46、34、19 个 0.05 秒积分步后到达 `|y| < 4`。只积分指令，不包含实际步态、通信延迟、摔倒和物理碰撞，不作为仿真成绩。

## 仿真与四类分析

使用 `booster-sim-test`，保留历史模式。另一任务已结束并导出后才切换 Studio 到本工作区、构建部署、通过官方 Reset 重置。重置后等待超过 30 秒，再核验双方 Active、裁判 ready、看板 age < 0.05 秒后开赛。双方 Agent 为 `com.example.lqinternalmatch` / `com.booster.default3v3ai`。43 个其他 Python 源文件在工作区、构建包和部署目录中摘要一致；顶层 `__init__.py` 仅含说明，构建工具将其置空，已单列核验。[运行身份](outside-field-recovery-evidence/run01/identity-check.json)、[日志](outside-field-recovery-evidence/run01/runner.log)。

- 固定 600 秒赛制，结束前采样经过 60.062 秒，最终裁判终态为 **60.112 秒**（余时 539.888 秒）。0:0 → **0:1**，`operator_finish`，`isFinal=true`；不是自然完赛，不计完整比赛胜负统计。[终态](outside-field-recovery-evidence/run01/end-status.json)、[事件](outside-field-recovery-evidence/run01/events.json)。
- 原始回放 **2,924 帧 / 60,224,395 字节**，ID 615712–618635，仿真时间 42.488–122.798 秒。区间包含首次 READY/SET，因此覆盖时长大于主计时时长；进球后的 READY 会扣主时间。READY 1842 帧、SET 480 帧、PLAYING 592 帧、FINISHED 10 帧。FINISHED 包括 start 响应传播前及结束后的边界帧。
- 没有时钟回退；最大相邻仿真时间间隔 0.274 秒，52 处超过 0.1 秒，不能宣称全程无丢帧。随机种子没有指定或控制，其他机器人运动由实际仿真决定。
- [原始 JSON 压缩归档](outside-field-recovery-evidence/run01/dashboard-team1.json.gz)、[导出校验](outside-field-recovery-evidence/run01/export-verification.json)、[统计与关键帧](outside-field-recovery-evidence/run01/analysis.json)。本地同目录保留未压缩 JSON；看板历史没有清空。

### 机器人行为复现

- READY：2 号在帧 615763 / t=43.956 首次进入实际场内；1、3 号在帧 615764 / t=43.984 进入。三人初始均约 y=4.98，未再持续停在出生点。
- 首次 PLAYING：帧 616623 / t=62.598，我方 1/2/3 号分别在 (-1.434,-0.095)、(-5.945,-0.062)、(-0.460,3.399)，对方三人也均在场内。1 号请求向 3 号传球；裁判在 t=66.272 确认 robot1 触球。
- 进球后 READY：连续状态样本显示 1 号从门前回到约 (-1.41,0.06)，2、3 号回到约 (-5.97,0.01)、(-0.45,3.37)，随后进入 SET / PLAYING。
- 本场没有处罚记录，也没有 `return_to_field` 行为帧。因此处罚解除分支只经离线回归验证，不能声称已在真实比赛验证。

### 失分原因

直接证据：t=73.648 裁判记录对方 robot4 触球。帧 617039 / t=83.000，球约 (-7.118,-1.994)，1 号近球踢球接近被 `goal_structure` 拒绝；2 号在 (-6.917,-1.127)，常规站位也被球门结构阻挡。帧 617084 / t=85.958，2 号移动又受 `team:1` 阻挡。帧 617099 / t=86.984，2 号计算出约 0.184 秒后的入门威胁，但直线封堵被 `direct_segment` 拒绝；1 号的踢球请求被让行取消。t=87.806 裁判确认客队进球，帧 617113 显示比分 0:1。

合理推断：己方门柱附近的球门结构与队友占位使清球、封堵没有及时执行，是该次失分的重要限制。裁判 t=86.064 记录最后一条我方 robot1 触球事件，但事件不说明主动踢球还是接触反弹，不能据此认定乌龙踢球或精确归因；对方后续触球是否全部可见也有证据限制。本次不据此自动修改守门战术。

### 未得分原因

直接证据：1 号开球传向 3 号后，帧 616745 / t=66.550 球在 (-0.170,0.930)；帧 616772 / t=67.994 已到 (-1.210,2.209)，3 号在 (-0.736,3.039) 请求射向 (7.25,0)。至帧 616811 / t=70.004，球在 (-2.207,1.215)，仍位于己方半场；随后裁判 t=73.648 记录对方获得触球，转为己方门前防守。回放有 5 段踢球请求开始，但请求不等于射门成功。后续大量主时间处于进球后的 READY/SET，只有较短的再次开球片段，没有形成可验证的得分结果。

合理推断：本次开球后没有把球推进到对方球门附近，传接后的控球/出球环节未转化为有效进攻。无法仅凭这些观测确认具体脚部执行失败或完整球权交接原因。

## 限制

- 有效位置和裁判状态是回场前提；处于处罚中保持停止是预期行为。
- 无安全通路时仍可停车；修复不提供穿越实体、球门结构或重开保护区的豁免。
- 一次短时测试不能保证涵盖处罚解除事件，需按实际数据披露。
