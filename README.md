Simplified Version of Booster Champion 3v3 Soccer Tournament agent

This strategy framework has been greatly simplified to make it easier for players to implement, but it still needs optimization to achieve the best results. Players should try it out thoroughly in a simulation environment and only use it in the competition after successful debugging.

How to use：
1. Download Booster Studio
2. Open this project from Booster Studio
3. Waiting for environment prepare
4. Select a Virtual Robot
5. Just click "Activate, build, deploy and run agent"

more info:
https://booster.feishu.cn/wiki/RwYkw3004iV39hk6uJOcu2ctnah

## 策略分层

`src/main.py` 每帧只串联三个步骤：

```python
assignments = role_allocator.assign(context, players, store)
intents = player.decide(context, players, assignments, store)
actions.execute(context, players, intents)
```

- `src/role_allocator.py`：比赛阶段、角色分配、进攻者保持、守门员交接。返回每个球员的 `RoleTask`，不调用角色行为。
- `src/player.py`：整队统一入口 `decide()`，内部调用各球员的角色决策，再调用 `coordinate()` 协调全队意图。角色行为只返回意图，不下发机器人指令。
- `src/intents.py`：不可变的角色任务和动作意图。移动意图包含目标、朝向和避障选项；踢球意图包含球门、队友或坐标目标及出球类型。
- `src/actions/kick_planner.py`：将踢球目标解析成目标点，再计算场地方向和力度。
- `src/actions/motion_controller.py`：移动路径、避障、速度和转向控制。
- `src/actions/controller.py`：统一执行入口，检查就绪状态、切换动作、取消残留踢球。
- `src/actions/executor.py`：状态读取、身体坐标转换、右脚方向补偿及 SDK 指令边界。
- `src/utils/ball_motion.py`、`interception.py`：共享球运动估计、轨迹预测和每名球员的截球结果。工具层不下发指令。

球轨迹默认按提供的离线拟合公式先滑动再滚动：`tb = 0.06430*v0`，
滑动速度为 `v0 - 3.86194*t`，随后为 `max(0, 0.78855*v0 - 0.57366*t)`。
对速度分段积分得到位移，停止后位置保持不变；第二段时间仍从运动起点计。
`src/param.py` 的 `BALL_MOTION_MODE = 'pure_rolling'` 可切换到
`max(0, v0 - 0.57323*max(0, t - 0.07886*v0))` 的纯滚动拟合。
其初期平台只描述离散实验接触瞬态，不代表固定物理延迟。

预测器不要求出球初速，也不把首次观测当作出球起点。每次有效新观测都以最新球位为锚点，
通过最近 4 个观测区间的速度及其中点时间估计当前球速、减速度与滑动/滚动阶段。
阶段未知时暂用滚动减速度；观测到明显滑动减速后切入滑动阶段。
滑动剩余时间不能由未知起点的局部观测唯一确定，因此用截图关系给出的时间上界倒计时，
一旦观测支持滚动即切换。纯滚动模式直接从当前估计速度减速，不重复加入初期接触平台。
明显加速、转向、停球或观测中断会清除旧拟合；新数据继续修正预测。
看板轨迹和截球规划共用此预测；遥测 `velocity` 仍为相邻观测平均速度，
`current_speed_mps` 为最新观测时刻的在线速度估计，`phase` 为估计阶段。
`stop` 给出无后续触球、碰撞时的停止坐标，以及相对当前帧的剩余时间、距离；
不受 3 秒截球时域限制。勾选看板“球预测轨迹”可看到粉色停止点和连线，说明区显示数值。
画面外停止点以边缘标记提示，真实坐标仍保留；速度未知或旧回放缺少字段时不显示停止点。
公式来源是初速 1、3、5、7、10 m/s、0–20 s 的离线拟合，实际比赛效果尚需验证；
不预测未来触球、碰撞或反弹，也不由踢球力度推算初速。出球风险评估中的假设球速仍由独立参数配置。
- `src/framework/decision_frame.py`：运行循环在分配角色前更新一次共享球观测和截球结果。
- `src/visualization.py`、`telemetry.py`：执行后绘图和发布同一帧的决策信息。

### 添加队友意图协作

在 `player.coordinate(context, assignments, preliminary)` 内读取全队初步意图并返回最终意图字典。初步字典及意图不可原地修改；需要调整时复制字典并替换对应球员的意图，必须保留所有球员编号。这样传球者和接应者可以在同一阶段协商目标点，执行层只接收最终结果。

当前协调默认透传，不额外改变战术。正常比赛仍动态分配进攻、守门和自由人；我方开球仍由 1 号传给 3 号、2 号守门。准备站位和守门出球选择属于角色行为。

### 离线调用与检查

离线调用完整三步流程前，先调用 `prepare_frame(context, players, store)`；正式运行时由 runtime 自动完成。直接调用 `guard()`、`attack()` 等只会返回意图，需要显式交给 `player.controller.execute(intent)` 才会执行。

```sh
python3 -m unittest discover -s checks -p 'test_*.py'
```

这些检查验证决策及指令转换；比赛表现仍需通过 Booster 仿真验证。


### 看板球预测准确率

看板下方新增「运动中球 · 预测准确率」，按 0.5、1、2、3 秒分别统计。
只有预测发出时速度估计有效且球速 ≥ 0.1 m/s 才取样，每 0.5 秒最多一次。
使用当时已记录的预测点，对照后续实际球观测；位置误差 ≤ 0.5 米算命中，
同时显示平均欧氏距离误差、有效样本数、待验证数及缺失对照数。

对齐采用 Agent 时钟和球观测时间，允许间隔不超过 0.2 秒的相邻观测线性插值。
缺测、缺少预测点及运行重置造成的不可对照样本不进入准确率分母。
预测发出后的停球、踢球、碰撞不会排除，因此该指标衡量现有预测在比赛中的实际表现。

统计范围为当前队伍已加载的录制（或整个导入文件），不随回放游标变化。
历史同步未完成时会提示统计尚不完整；窗口滚动或清除录制后按剩余录制重新计算。
没有有效样本显示「暂无有效对照」，不会显示虚假的 0% 或 100%。

离线验证：`node checks/test_dashboard_accuracy.cjs`。

### 仿真时间

策略的 `Context.now/dt`、观测的 `last_seen_at`、球和队友运动预测、候选目标刷新、
踢球观测统计和起身节流均使用 `/clock` 仿真秒。ROS 桥接节点启用 `use_sim_time`。
首次收到 `/clock` 前不运行策略，也不回退到系统时间；零秒是合法仿真时刻。
时钟回退会清空旧观测、预测历史和候选缓存，重新积累数据。

真值消息目前是无时间戳的 `Pose2D`，因此观测标记为回调执行时最新收到的 `/clock`
时间；这消除了本地时间倍率的影响，但仍受跨话题消息到达顺序和时钟发布频率影响。
`received_at` 单独保存本地单调接收时间，仅供通信过期检查。
控制循环调度、线程等待和遥测发送频率继续按本地时间运行。

本地专项检查：`python3 -m unittest checks.test_simulation_clock -v`。
