# Attacker 传射路线 30 个百分点阈值

正常比赛且未显式指定踢球目标时，先沿用现有中央偏好规则选择直接射门落点，然后比较该落点的原始被截率与队友传射路线。中央偏好扣分不计入风险差。

- 综合被截率 = 1 − 传球成功率 × 队友第二脚射门成功率。
- 仅当直接射门被截率减综合被截率至少为 0.30 时传球；恰好 30 个百分点通过。12 位小数比较只消除浮点尾差。
- 所有本帧可行动、有定位的队友均可成为候选，包括守门员；排除自己。复用帧准备生成的可行动集合，倒地、受罚、模式不就绪不参与。可用集合变化触发重算。
- 多名队友满足时选择综合风险最低者，同分按编号。目标为已有运动预测器估算的接球点；当前 lead 配置为 0。
- 两段风险来自现有拦截计算器。第二脚使用沿传球轨迹推进后的对手分布，并从现有球门候选中选择最优后续射门。
- 未知/部分观测不能当作零风险；直射风险未知时不触发自动传球。过期/异时刻评估、越界接球点、不可用队友、当前球路结构阻挡均拒绝。
- 传球使用位置目标和 pass 力度，追球球后站位沿相同目标。开球固定传人、定位球、显式目标及现有结构阻挡替代流程保持原行为。

`scoring_probability.via_teammate` 记录全部接球队友，保留 `via_freeman` 供旧看板兼容。`attack_choice` 增加 `pass_options/direct_risk/best_pass_risk/risk_improvement/required_margin`，选择模型为 `pass_risk_margin`。看板单列两段成功率、综合风险和百分点差；概率刷新仍为 0.5 秒，不持有两脚战术承诺。

模型假设接球后立即射门；第二脚对手分布未按第一脚成功事件重新加权，未模拟接球失误、转身耗时及球速标定。因此是简化模型估计，不能等同实测进球率。接球队员接球后按最新角色和局面重新决策，可能不射门。

实现位置：`src/utils/pass_selection.py`、`src/utils/scoring_probability.py`、`src/player.py`、`src/param.py`、`tools/tactics_dashboard/dashboard.js`。测试入口：`python3 -m unittest discover -s checks -p 'test_pass_selection.py'`、`node checks/test_dashboard_pass.cjs`。
