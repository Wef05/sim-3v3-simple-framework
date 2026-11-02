# 自由人协防交付报告（离线验证）

## 范围与版本

用户批准实现自由人协防、测试、1 分钟仿真和 PR。2026-11-02 在 Studio 工作区信任步骤受到自动审批拒绝后，用户明确选择“暂不信任，先交付代码和测试”。因此本次交付范围缩小为实现、离线测试和草稿 PR，仿真留待后续授权。

- 基线 origin/MAIN：aaee819；创建前 fetch 成功，本地 MAIN 与远端无分歧。
- 工作区：`/private/tmp/freeman-cover-20261102`。
- 分支：`codex/freeman-cover`；实现提交：`fc38356`。
- 主工作区已有 `.gitignore` 与 `.booster-studio/project.json` 修改未动。
- Studio 在本任务 worktree 自动更新 project.json 时间戳，保留未提交，不纳入策略变更。
- 设计、参数和限制见 [设计说明](freeman-cover.md)。

## 实现

新增独立 `src/utils/freeman_cover.py`，接入 `Player.freeman()`；只在正常比赛生效，不改变角色分配或重启职责。三态抢先预测、位置方向回退、退出迟滞、角色和时钟重置、当前球路可达性检查、门侧补位、守门目标路径避让和逐帧遥测均接入。

`threat.py` 排除明确不可行动队员对全队结果的干扰，并在速度未估计时保留未知。看板显示协防原因与目标，协防时不画进攻接应区域，兼容旧回放。

## 测试

- 基线完整 Python：140 通过、1 导入错误。
- 变更后完整 Python：147 通过、1 相同导入错误（缺少 `checks.goalkeeper_replay_cases`）；不是全套通过。
- 自由人/协防/守门优先级/连续性专项：31 通过，其中新增协防 7 项，覆盖多个表驱动场景。
- 看板 guard（含新增协防卡片验证）、freeman、navigation、shot_selection 四个 Node 检查通过；JavaScript 语法和 diff 空白检查通过。
- [基线日志](freeman-cover-evidence/baseline-tests.txt)、[完整日志](freeman-cover-evidence/tests.txt)、[专项日志](freeman-cover-evidence/focused-tests.txt)、[看板日志](freeman-cover-evidence/dashboard-tests.txt)。

## 仿真与四类分析状态

未点击构建部署按钮，未启动本任务比赛。打开工作区后 Studio 自动准备开发环境，不等于构建、部署或仿真成功。自动审批拒绝信任 `/private/tmp/freeman-cover.code-workspace`，理由是启用任务、调试、设置和扩展需要明确授权；用户随后选择暂不信任。没有绕过限制。

| 项目 | 结果 |
| --- | --- |
| 比分 | 本任务未开赛，无双方比分、比赛时长或终态数据。 |
| 机器人行为复现 | 无本任务回放，无法评价实际回撤、封堵、迟滞及避让表现。 |
| 失分原因 | 未运行，不能评价或声称无失分。 |
| 未得分原因 | 未运行，不能评价进攻接应减少等影响。 |

看板导出帧数和文件大小不适用；没有清空已有看板历史，也没有停止或覆盖其他任务比赛。检查到的上场 finished/0:0 属于其他任务，不能作为本任务证据。

## 剩余工作与风险

1. 明确允许信任这个具体工作区后，经 Studio 构建部署、逐文件核验源码/构建包/部署摘要。
2. 重新检查共享资源无人占用，等待模拟器至少 30 秒并核实就绪；按裁判计时跑一次 60 秒，主动结束并导出固定边界回放。
3. 保留历史、补齐双方身份、计时和事件证据，完成四项分析。

0.6 秒、0.2 m/s、2 秒和 0.9 m 为保守初值，尚未标定。直接封堵依赖简化轨迹、直线路径及估计到达时间，不能保证实际触球；对手先触球或时间未知时回退补位。守门员路径让位只检查当前意图，动态碰撞由已有导航保护。单元测试不证明战术提升，草稿 PR 尚未完成实场验收。

## PR #15 冲突解决（2026-11-02）

按用户批准的局部任务，将 MAIN `cd1d49d` 合入 PR 原提交 `2a2642e`。独立工作区 `/private/tmp/pr15-conflict-resolution`，本地分支 `codex/pr15-conflict-resolution`，结果推送原 PR 分支 `codex/freeman-cover`。

- `src/player.py` 同时保留自由人协防与进攻者接球状态初始化、重置逻辑。
- `src/param.py` 同时保留协防参数与传射风险差阈值。
- `checks/test_dashboard_guard.cjs` 同时保留协防卡片与重启撤离检查。自动合并的遥测与看板代码已核对。
- 协防、接球、重启撤离、守门优先级与连续性专项共 47 项通过；全部 5 个 `test_dashboard_*.cjs` 脚本通过，JavaScript 语法检查通过。
- 合并后完整 Python 共 209 项：206 通过、1 失败、2 错误。最新 MAIN 基线共 202 项：199 通过、1 失败、2 错误。两边均为缺少 `checks.goalkeeper_replay_cases`，以及 `test_pass_selection` 中两个预期踢球而获得停止意图的用例；本次未修改这些既有问题。
- 原始日志：[合并后检查](freeman-cover-evidence/conflict-resolution-tests.txt)、[MAIN 基线检查](freeman-cover-evidence/conflict-resolution-main-tests.txt)。
- 本次仅解决冲突，按批准范围不运行仿真、不合并 PR；原仿真待验证限制仍然有效。
