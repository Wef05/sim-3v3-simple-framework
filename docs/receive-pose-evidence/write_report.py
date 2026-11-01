import json
from pathlib import Path
root=Path('/Users/limingzhe/.codex/worktrees/pass-coordination/sim-3v3-simple-framework')
e=root/'docs/receive-pose-evidence'
a=[json.loads((e/r/'analysis.json').read_text()) for r in ('run01','run02')]
perf=json.loads((e/'planner-cost.json').read_text())
s='''# 接球姿态优化：实现、离线检查与受阻的仿真验证

日期：2026-11-01（Asia/Shanghai）。状态：实现和离线检查已完成，构建/部署身份核实；有效 60 秒仿真及实战效果验证未完成。不能宣称已实现比赛中的左上接应或提升进攻。

## 变更与源码

基线 `6b41db7`；实现 `fdab7bf`，候选遥测容量控制与界面检查 `50bdc92`。运行源码均为 `50bdc92dd0421b218521c8225dbb3f351be4c7a2`，任务分支 `codex/pass-coordination`。仅使用任务 worktree `/Users/limingzhe/.codex/worktrees/pass-coordination/sim-3v3-simple-framework`。

- 新增纯计算 `src/utils/receive_pose.py`：接球点 C、接应站位 R、球后点 K 和进攻方向联合选择；有限八方向搜索，开局安全候选中优先左上且远离对手。
- `src/coordination.py`：固定开局参考点、独立接应就绪条件、冻结姿态复核、传后支援避让绕球和进攻走廊。
- `src/receiving.py`：按实测轨迹更新接应侧与球后路径，接近制动和首次出球门槛保留；计划可准备姿态，但不自行产生踢球指令。
- `src/param.py`、看板及回归检查：有界参数、接应/球后点和方向、选点拒绝理由及时间预算。

详见 [设计与契约](receive-pose-design.md)、[协作说明](pass-coordination.md)。路线验证为有限静态折线与动态导航复核，不是联合物理轨迹保证；没有 SDK 停球反馈。对手按 2 m/s 最早到达、球按 2–5 m/s 假设，均未实测标定。

## 检查

专项组合 **102 项通过**（15 项新增姿态检查，加既有传接球、接球安全、导航、阻挡进攻、力度、仿真时钟）。看板 JavaScript 语法和两组界面检查通过，包括新姿态字段和旧回放兼容。

完整检查 **129 项、1 个导入错误**：缺少 `checks.goalkeeper_replay_cases`。在基线 6b41db7 独立临时目录运行相同检查环境：114 项，同一个导入错误。未删减失败项，不声明全套通过。现有传球测试的到位夹具改为 R，真实开球管线夹具改为合法己方半场；另加明确回归确保站在 C 上不能冒充已到 R。

证据：[专项](receive-pose-evidence/focused-checks.txt)、[完整](receive-pose-evidence/full-checks.txt)、[基线](receive-pose-evidence/baseline-checks.txt)、[界面](receive-pose-evidence/dashboard-checks.txt)。

## 历史开局复算与重要限制

使用上一场 run02 的 frame **494401 / sim 55.374**，3 号实际位置约 (-0.5823, 3.4047)。原逻辑选 (0, 2.9047)，即右下；新逻辑评估 26 个不同候选，**没有批准任何候选**。最终候选理由：9 个接球后争抢余量不足、7 个球路不安全、7 个开球等待点不在己方半场、3 个进攻走廊或球后点受阻。这是最后评估方向的候选概括；各方向的拒绝计数保存在原始诊断。

因此：合成场景已经验证“安全左上候选优先”，但这份真实历史开局没有出现可批准的左上点。完整预算包括双方准备、飞行、接球稳定、绕球、转向和姿态确认，保守性明显增加；目前还没有对重开阶段对手受规则限制的运动单独建模，也未标定力度与球的到达/停止位置。不会为强行得到左上结果而降低争抢门槛。

离线复算不能替代实际执行效果。完整历史现场文件仍在 [上一场原始回放](receive-safety-evidence/run02/dashboard-team1.json.gz)，本次 [复算诊断](receive-pose-evidence/opening-reconstruction.json) 与脚本保留。

'''
s+=f'''同一历史帧各运行 20 次：旧选择器平均 {perf['baseline']['mean_ms']:.2f} ms，新选择器平均 {perf['pose']['mean_ms']:.2f} ms、最大 {perf['pose']['max_ms']:.2f} ms。新搜索成本更高，完整重选间隔至少 0.5 s；单次耗时可能超过一帧预算，容器持续比赛中的实际开销尚未验证。详见 [测量](receive-pose-evidence/planner-cost.json)。

## 实验条件与运行身份

目标：一次有效 60 s 裁判计时验证，重点看开局位置、C/R/K、实际接触偏差、进攻准备耗时和对手争抢；不以单场比分评估胜率。无可控随机种子，记录真实数据，不筛除失败。

容器 `4x2a0-k1`，官方 Studio 工作区构建/部署/重置。我方 Challenger / `com.example.lqinternalmatch` 2.0.3，对手 Booster AI / `com.booster.default3v3ai`，runner 已 Active；每次重置后等待至少 60 s 系统时间再开赛，主计时始终采用裁判 `timeRemaining`。

两次运行的源码、构建包和部署目录逐文件核对：47 个 Python 文件中 46 个逻辑文件一致；顶层 `__init__.py` 只有说明文字，构建工具置空，单列差异。包 SHA256 `5f1e07c29b79e345ee5df310020194db7489bac582c2595f8ddab95cde1f41e2`。源码工作区干净，身份见各场 `identity.json`。构建引起的任务配置更新时间已恢复，不改主工作区。

原有看板历史保留，分别记录 session、起止帧并限定导出范围；未清空服务、未停止共享容器。两次均已核实 FINISHED。

## 比分与样本有效性

| 尝试 | 主计时经过 | 比分 | 结束方式 | 有效性 | 帧数 / gzip 字节 |
| --- | ---: | --- | --- | --- | ---: |
'''
for i,x in enumerate(a,1):
 su=x['summary'];s+=f"| run0{i} | {su['elapsed']:.3f} s | 0:0 → 0:0 | operator_finish，FINISHED | 我方三人仍在出生区，无效开局 | {su['frames']} / {su['bytes']:,} |\n"
s+='''
第一次失败后按技能规定的场外出生点恢复规则，通过官方入口重置后重试一次；代码、参数与包未变。恢复尝试仍失败，因此没有继续增加比赛，没有达到 60 s 有效仿真要求。两场均不计入自然完赛或胜率统计。

## 机器人行为复现

run01：READY 的 746 个采样帧中，1/2/3 号均记录 `field_boundary` 阻挡。PLAYING 时分别仍约在 (-4.003,4.984)、(-5.005,4.985)、(-5.994,5.005)，离预定 READY 点约 5.577/5.084/5.697 m。主计时 0.040 s 结束，没有传球计划事件，也没有执行选点。定位见 `run01/opening-check.json`、`startup-analysis.json` 和原始回放；例如帧 534004（导出序号 600）处于 READY，三人的导航 blocker 均为 field_boundary。

run02：READY 的 710 个采样帧中，三人同样报告 field_boundary；PLAYING 时离 READY 点约 5.579/5.090/5.692 m。主计时 0.096 s 结束；仅有一次 no_safe_pose 选点记录，没有建立/释放传球计划。两场对方三人均已在场内。

直接证据是机器人持续位于出生区、READY 导航被场地边界阻挡；新增姿态搜索没有参与 READY 入场路径。此次没有改动底层导航。日志还出现 SDK 服务等待/RPC 超时，但没有 Python Traceback；不能仅凭这些日志断定它们就是入场失败根因。原始日志和状态已保留。

## 失分原因分析

两次均 0:0，无失分事件。由于几乎在开球瞬间结束，不具备防守效果样本，不能据此说明防守可靠。

## 未得分原因分析

两次都在入场有效性检查失败后结束，没有形成可分析的传接球进攻机会。不能将 0 进球归因于新接球点算法。历史开局复算另显示完整争抢预算会拒绝本次关注的开球传球，可能导致进攻过度保守；这是离线风险，不是已完成 60 s 比赛的结果。

## 采集范围与数据缺口

'''
for i,x in enumerate(a,1):
 c=x['capture'];s+=f"- run0{i}：帧 {c['first_id']}–{c['last_id']}，sim {c['first_sim']:.3f}–{c['last_sim']:.3f}，原始 JSON {c['raw_bytes']:,} 字节；最大相邻仿真间隔 {c['max_sim_gap']:.3f} s、接收间隔 {c['max_receive_gap']:.3f} s。包含开赛前终态、READY/SET 和短暂开球；不是这些秒数的有效比赛。\n"
s+='''
导出范围仿真时间无回退，服务 session 未更换。遥测尽力传输，不能证明无丢帧；没有实际控球传感器证据。两次比赛都缺少有效比赛主体和接球后进攻样本。

可导入回放：[run01 gzip](receive-pose-evidence/run01/dashboard-team1.json.gz)、[run02 gzip](receive-pose-evidence/run02/dashboard-team1.json.gz)；本地未压缩版本为 `results/receive-pose/run01/dashboard-team1.json` 与 `results/receive-pose/run02/dashboard-team1.json`。每场保留 before/start/end/result、events、samples.json.gz、runner、agent.log.gz、边界和源码身份；分析与运行脚本位于 `receive-pose-evidence/`。

## 剩余工作

1. 单独修复/恢复 READY 出生区的场地边界导航阻挡，再执行有效 60 s 验证；本次不夹带导航行为修改。
2. 校准/审查完整争抢时间模型，尤其是开球规则约束、实测球速、接球姿态转向时间；当前没有满足真实历史开局左上接应的效果要求。
3. 有效比赛中测量实际接触偏差、球后姿态到位时间、对手争抢及计算开销。再决定是否调整模型，不自动补场或调参。

已有任务 PR 保持草稿：[PR #3](https://github.com/Wef05/sim-3v3-simple-framework/pull/3)。实现完成、检查通过、构建部署成功与效果达标分开记录。
'''
(root/'docs/receive-pose-report.md').write_text(s)
print('report written')
