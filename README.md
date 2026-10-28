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

### 开球后的双人配合进攻

正常比赛使用 `src/cooperative_attack.py`，固定守门员仍由原守门逻辑控制。
开球传接完成、接应球员靠近球后转入正常传射决策。

- 移动：`move:chase` 追球并调整到球后；`move:position` 向侧前、侧方或侧后空当落位。
- 踢球：`kick:shoot` 在射程内且球路畅通时射门；`kick:pass` 向线路畅通、位置更好或压力下可回传的队友传球。
- 传球指令提交不代表球已踢出：球朝接应方向移动 0.25 m 后，原传球者跑位、接应者追球。
- 角色短时保持；另一人明显更接近球、接应超时、球偏离、球员不可用或对手近球占优时解除旧计划。
- 对手近球占优时，一人追球施压，另一人使用原补防站位。失去球观测时停止动作。
- 定位球、停止和准备阶段清除普通进攻计划，原裁判阶段限制继续优先。

参数集中在 `src/param.py` 的 `COOP_*`。初始射程 4.5 m、传球距离 1.5–4 m，
传球沿用原定位球力度 1.0；这些是待实赛标定的启发式参数。
球路判定使用当前机器人位置，没有预测对手运动；四动作模型不含独立带球动作，
没有可用传射机会时继续调整追球与接应位置。

验证：`python3 -m unittest discover -s tests -q`。本次为本地逻辑验证，未部署或进行完整仿真比赛。
