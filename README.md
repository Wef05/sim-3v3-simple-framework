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
