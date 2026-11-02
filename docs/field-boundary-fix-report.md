# 场地边界导航修复与验证报告

日期：2026-11-02。任务分支：`codex/field-boundary-fix`；基线：`ddf350a`；实际构建部署源码提交：`81bd57737d8405adc487027a4fd7a3e73a76e930`。工作区：`/Users/limingzhe/.codex/worktrees/field-boundary-fix/sim-3v3-simple-framework`。用户已批准方案。本报告后的提交仅补充测试与证据，未改变本场运行源码。

## 变更与范围

`083a62fe` 引入的导航在目标裁剪、搜索范围和预测速度三处将场地矩形当作硬边界。`f45fe102` 的场外行为覆盖和单向入场补丁只处理回场，并会抢占场外开球任务。

- `src/player.py`：撤销 `return_to_field` 行为覆盖，不再清除原角色记忆；追球保留真实球后点，允许边线、角区与门内站位。
- `src/actions/motion_controller.py`：保留请求目标，移除越界直线路径拦截及场外固定朝向全向移动特判。
- `src/utils/navigation.py`：移除场地边界速度检查及其场外制动例外；保留门柱、球网、机器人、球与预约轨迹检查。
- `src/utils/path_planner.py`、`src/param.py`：移除目标裁剪与只许向内的边限制；搜索覆盖场地和起终点，四周增加 1.5 m，最多每轴 80 格，极远目标增大格距。
- 更新 `checks/test_field_boundary.py`、`checks/test_field_reentry.py`、`checks/test_navigation.py`、`checks/test_blocked_attack.py` 及 [导航说明](navigation-policy.md)。没有新功能或遥测字段，看板保持兼容。

保留 `f45fe102` 中独立的暂停保护；不回滚其历史报告和原始数据。不修改角色分配、队内优先级、实体安全距离或核心架构。主工作区已有配置与忽略规则修改未纳入任务。

## 离线验证

证据目录：[field-boundary-fix-evidence](field-boundary-fix-evidence/)。

| 检查 | 结果 |
| --- | --- |
| 修改前完整检查 | 95 项，92 通过、3 错误：缺少 `checks.goalkeeper_replay_cases`，另两项为强制回场分支访问测试替身不存在的属性。 |
| 最终专项检查 | 73 项全部通过，包含边界、回场、导航稳定性、角区出球和重开保护。 |
| 最终完整检查 | 105 项，104 通过、1 错误：仍缺少基线回放数据模块。没有新增失败；未将缺失模块计为通过。 |
| 看板检查 | navigation、guard、freeman 三个 Node 检查通过。 |
| 差异检查 | `git diff --check` 通过。 |

命令：`python3 -m unittest checks.test_field_boundary checks.test_field_reentry checks.test_navigation checks.test_navigation_stability checks.test_blocked_attack checks.test_restart_defense -q`；完整检查使用 `python3 -m unittest discover -s checks -p 'test_*.py'`。看板检查分别运行 `node checks/test_dashboard_navigation.cjs`、`node checks/test_dashboard_guard.cjs`、`node checks/test_dashboard_freeman.cjs`。

覆盖要点：两侧边线、两侧角区、两侧球门开口双向通行；普通与直线目标保持精确坐标；场外 A* 绕障；真实边球/角球进攻决策保留场外球后点；READY 和 PLAYING 解除处罚后的角色恢复及简化积分回场；执行前重新受罚仍停车；场外朝向要求不被吞掉；球网、门柱与身体碰撞仍拒绝；远端坐标搜索规模有界。

将最终功能测试移植到 `ddf350a` 的临时源码副本（只排除基线不存在的搜索网格辅助函数测试）执行了 8 项测试、出现 17 个失败断言/子场景，证明旧代码不能满足新的跨线行为要求，见 `final-regression-on-baseline.txt`。此验证早于最后补充的 PLAYING 解除处罚测试。`regression-before.txt` 保留初次测试构造记录，不作为最终失败计数依据。

测试调整保持物理约束：原选取的门内 x=±7.3 m 目标落入后网膨胀区，规划净距约 -0.17 m，因此保留“该终点不可达、已有重叠可向场内脱离”的测试；另用净距为正的 x=±7.1 m 覆盖门口通行。对方重开保护距离严格断言为既有规则 `max(circle_radius, OPPONENT_RESTART_AVOID_M)`，未削弱避障。

小规模耗时抽样（同机各 7 次，非实时性保证）：无遮挡直线中位数 0.0044→0.0040 ms；中央单障碍绕行 2.77→3.52 ms；场外绕障 0.56→0.99 ms，但旧版裁剪了目标，两者场外耗时不可直接等价比较。原始结果和脚本见 `benchmark-*.json`、`benchmark.py`；测试时仿真运行中，存在系统负载噪声。

## 实验身份与过程

- 本次仅启动一次比赛，未追加场次。运行目标为虚拟机器人 `4x2a0`、容器 `4x2a0-k1`；赛前容器已停止、无其他测试调度进程。赛前证据保存在 `attempt-01/container-before.json` 和 `processes-before.txt`。
- 通过 Booster Studio 官方入口构建、部署、运行本任务工作区。双方 runner 均 Active，看板新鲜，三名我方球员均为 walk 后开赛。模拟器窗口打开确认时间为北京时间 10:13:39，实际等待 83.062 秒后开赛；该等待不计入比赛时长。
- 我方 `com.example.lqinternalmatch` 2.0.3，对手 `com.booster.default3v3ai`。逐文件比较源码、构建 `.agent` 包和 runner 实际目录，共 45 个 Python 文件；唯一差异是构建器将仅含说明文字的顶层 `src/__init__.py` 置空，已检查无执行语句，其余一致。见 `attempt-01/identity.json`、`source.patch.gz`、`working-tree.patch` 与 runner 日志。
- 构建包保留在本任务 `build/com.example.lqinternalmatch-2.0.3.agent`；完整包及源文件 SHA-256 在身份记录中。运行未设置随机种子，无法保证严格复现同一球路。
- 匹配身份以 `matchId=local-match` 加 `startedAtWallTime=1793585702.112163` 联合校验；连续裁判样本未出现身份变化或计时回退。
- 默认赛制为 600 秒，本次按已说明的短时测试方式，在裁判剩余时间降至 539.818 秒后主动结束，实际经过 **60.182 秒**。`operator_finish`，`isFinal=true`，裁判结果与回放 FINISHED 终态均核实；不是自然完赛，不纳入完整比赛战绩。

## 比分

Challenger（我方）对 Booster AI，初始 **0:0**，结束 **0:0**。事件与结果见 [events.json](field-boundary-fix-evidence/attempt-01/events.json)、[result.json](field-boundary-fix-evidence/attempt-01/result.json)、`end-status.json`。短时无进球不能说明长期进攻或防守效果。

## 机器人行为复现分析

以下 context.now 为仿真时钟，包含 READY/SET 及摆球暂停；裁判用时另列，不能混用。裁判事件通过墙钟对齐最近回放帧，误差保留在 `analysis.json`，不把动作请求等同于触球。

| 帧 / 仿真时间 | 阶段与观测、决策、表现 |
| --- | --- |
| 787429 / 46.608 s | READY，三名我方球员初始 y≈4.981 m，位于边线外；均执行编号阵型任务。3 号一度因队内预约让行，并非边界拦截。 |
| 788340 / 66.634 s | 首个 PLAYING 帧，我方 1/2/3 号约在 (-1.51,-0.11)、(-5.92,-0.09)、(-1.11,4.29)，对方三人也已入场。READY 回场实际发生，没有场外出生点加载失败。 |
| 788439 / 70.428 s | 我方 1 号请求向 3 号开球；裁判事件 7 在用时 3.984 s 确认 robot1 触球。 |
| 788833 / 80.934 s | 对方边球保护阶段，2 号 guard 的目标 (-6.5,1.05) 附近出现 `goal_structure` 拦截，证明物理球门检查仍启用。此状态不是实际碰撞证据。 |
| 789123 / 94.460 s | 3 号 `attack` 位于 (-1.12,-4.58)，球在 (1.46,-4.63)，移动目标 (1.18,-4.85) 保留在场外；没有 `return_to_field` 覆盖。 |
| 789193 / 96.648 s | 3 号在 (0.51,-5.29) 继续进攻并请求朝球门出球。裁判用时 27.200 s 确认对方 robot4 触球；事件中的前次触球键为 home:3，但未提供我方 3 号对应的独立触球时间，不能精确归因哪一帧出球成功。 |
| 789249→789250 / 99.492→99.542 s | 3 号已变为 `freeman:move`，目标在场内；y 从 -4.528 到 -4.488，实际跨线返回场内。此处是普通角色回场，不是罚下解除事件。 |
| 789615 / 118.960 s | 我方门球阶段 1 号在 (-6.20,-1.89) 请求出球；裁判用时 46.558 s 确认 robot1 触球并开放球权。该门球位置在场内，不等同于球门内部出球验证。 |
| 789926 / 133.126 s | 回放终帧为 FINISHED，与裁判主动结束终态一致。 |

本场 3 号有 127 个 PLAYING 场外帧；导航 `field_boundary` 拦截为 0。按球员×帧统计，`goal_structure` 73、对手 177、球 188、队友实体 74、队内预约 23 次。这些是状态计数，不是独立事件或实际避障成功次数。

## 失分原因分析

本场无失分，不能提供真实失球归因。后段球曾进入我方底线区域，39.014 s 裁判判给我方门球；该片段体现防守压力，但不能据此认定某个防守缺陷或宣称修复保证不失分。

## 未得分原因分析

1. 开球未转化为机会：3.984 s 我方 1 号触球，6.406 s 裁判记录球越过上边线并判对方边球，`lastTouchSide=home`。回放中 3 号在上边线附近等待/追球。直接证据是球权因出界转移；传球精度、接球时机或 SDK 触球控制各自贡献无法由此单独确认。
2. 中段球在下边线外，3 号确实越线追球并请求出球，27.200 s 对手触球后球路回到场内。没有进球；不能因为存在出球请求就认定完成射门，也不能将场外追球动作本身当作成功执行边球。对手先取得后续触球是明确事件，接触细节不足以唯一归因。
3. 46.558 s 我方门球触球后，55.860 s 对方 robot5 触球，最终未得分。进攻被对方后续触球打断是合理推断；传球落点、其他机器人干扰及脚步误差未完全观测，未据此追加战术改动。

## 原始数据与收尾

- 可导入看板的原始文件：[dashboard-team1.json](field-boundary-fix-evidence/attempt-01/dashboard-team1.json)；PR 中提供无损压缩版 [dashboard-team1.json.gz](field-boundary-fix-evidence/attempt-01/dashboard-team1.json.gz)，解压后导入。未压缩文件仅保留本地，避免重复提交大文件。
- **2501 帧，57,603,792 字节**；gzip **5,084,902 字节**。帧范围 **787426–789926**，context.now **46.524–133.126 s**，无时钟回退。
- 接收跨度约 **194.172 秒**，最大接收间隔 **2.209 秒**，大于 0.2 秒的间隔 **136 个**；最大仿真时钟间隔 **0.542 秒**。保留这些数据缺口，不宣称无丢帧。采集跨度包含准备和暂停，不能代替裁判的 60.182 秒。
- 原始 JSON SHA-256：`9dc6436887b94b0b3b9c130ba50d8378e7c643f672284403005ed38a8eceb8f7`。固定 session 和起止 ID 导出，导出后重新读取、校验终态与无损压缩内容。
- 连续裁判和看板采样以 `status-samples.json.gz`、`state-samples.json.gz` 保存；开赛、结束响应、事件、身份、runner 日志及分析均在 [attempt-01](field-boundary-fix-evidence/attempt-01/)。`files.json` 记录提交证据摘要。
- 导出完成后核实仍为本任务终态，通过 Studio 官方“停止运行 agent”停止双方 runner。UI 后续抓取返回屏幕捕捉错误，但 runner 日志独立确认双方均已销毁，见 `runner-after-stop.log` 与 `cleanup.json`。按仓库要求保留共享看板历史，未清空或重启服务。
- Studio 自动修改的本任务项目配置已备份到 `studio-project-after.json`，未作为源码变更提交。

## 风险、验收边界与剩余工作

实现及专项验证完成，已执行一次规定的短时仿真。实际验证了 READY 入场、PLAYING 跨线追球和按角色返回场内。**本场未发生我方边球、角球或罚下解除，亦未覆盖球门内部出球的完整 SDK 执行**；这些场景目前是离线验证覆盖，不能声称均已实战验收。

解除处罚后恢复原角色，而不是无条件强制回场；无球、开球等待等原任务要求停留时仍会停留。扩大搜索覆盖有额外开销，有界窗口可能遗漏远距离绕路，粗网格可能遗漏窄路；没有可行路径时仍只尝试经安全层验证的局部速度，否则停车。实体模型和短期预测不能保证真实步态、突然变向下绝对无碰撞，SDK 踢球内部步态仍不可完整观测。

完整测试剩余的缺失回放模块为基线问题，未修复。单场 0:0 不构成战术效果或稳定性统计结论。未擅自追加比赛；后续若需实战覆盖上述未出现事件，应另行安排定向实验。
