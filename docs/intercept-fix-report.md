# 截球区间搜索与参数校验修复（部分完成）

日期：2026-11-02。任务分支 `codex/intercept-continuous`；worktree `/private/tmp/intercept-continuous`。
基线 `aaee819`，实施前已 fetch，确认与 origin/MAIN 无分歧。主工作区的 project.json、.gitignore 修改未转移。

## 问题与实现

旧算法每 0.05 秒判断一个时刻。真实 BallMotion 中，球从 (-0.5,0) 以观测速度 20 m/s 接近原点机器人，25 ms 时已到脚下，却可能返回窗口末尾的远处 fallback。
现在对每段求到达时间保守下界，只有下界晚于区间末尾才剪枝；其余按时间顺序二分。远近距切换处不计转向下界，半圆最近点使用凸投影残差的非扩张性质构造位移界。保留 3 秒时域、现有移动/转向模型及结果字段。

叶区间长至多 1 ms，检查中点和末端，命中须使用实际预测重新验证。未排除的更早区间若超过 1 ms 误差范围，返回 unavailable/search_resolution_limit。单次最多 2048 个缓存时间点，超限返回 unavailable/search_budget_exhausted。既有调用者将 unavailable 视为未知（不参与最快排序/概率评估，追球可停止），不会将未知当成不可达或零概率。

**契约限制**：区间包围假设预测轨迹为直线、单向运动，可减速和停止；当前 BallMotion 满足此条件。未来增加反弹、折返或曲线预测必须更新区间包围算法，不能直接复用。

linear_speed、angular_speed 必须是有限正数，非法值统一 ValueError，含缺球/无位姿提前返回路径。共享 MAX_ANGULAR 从 2 改为 1 rad/s；BALL_CONTROL_RADIUS_M 从 0.35 改为 0.2 m。影响移动控制、抢球、守门封堵和传射风险估计；TURN_THRESHOLD、踢球触发距离不变。

## 离线验证

- 新增 14 项测试通过：真实快球、半圆、静止与停止球、窗口端点、完整无解、非法参数、精度/预算耗尽、全向阈值、独立 0.1 ms 细网格对照、共享参数及控制器限幅。
- 完整检查运行 155 项：154 通过，1 项导入错误。修改前 141 项同样仅有该错误：缺 checks.goalkeeper_replay_cases，不能宣称全套通过。
- `node checks/test_dashboard_guard.cjs` 通过。
- `git diff --check` 通过。

[修改前日志](intercept-fix-evidence/baseline-tests.txt)、[修改后日志](intercept-fix-evidence/after-tests.txt)。

## 计算开销

固定种子 42 的 100 个场景，各运行 10 遍；两种算法使用相同新参数，隔离算法开销。旧算法平均 0.097 ms，新算法平均 0.256 ms、P95 0.519 ms、最大 1.826 ms，最多 65 次时间点求值。新算法输出 92 fallback、7 intercept、1 unavailable；该未知是精度边界，不冒称完整解。

三名我方、三名对手的静态 prepare_frame 均值从 0.332 ms 增至 1.342 ms（200 次）。这是帧准备阶段，不是包含战术评估/导航的完整运行循环。机器负载影响计时，非实时保证。

[原始数值](intercept-fix-evidence/benchmark.json)、[复现脚本](intercept-fix-evidence/benchmark.py)：在仓库根目录运行 `PYTHONPATH=. python3 docs/intercept-fix-evidence/benchmark.py`。

## 仿真计划与阻碍

计划：本任务代码构建部署，一次裁判计时 60 秒后主动结束，验证入口/运行稳定性、拦截结果及未知比例，导出看板原始数据并复盘。保留原历史，记录 session 与起止 ID；不将短时比分解释成完整比赛胜负或算法收益。

当前 **未构建、未部署、未启动本任务比赛**。首次只读查询发现共享仿真正处于 active/READY。后续裁判显示结束，但资源占用任务 `01a19b17-0c4c-7590-810e-f4c72f7af3d5` 仍 active，明确刚部署并复位，准备开新一场完整比赛。按仓库约定不覆盖或接管其运行。

[资源冲突证据](intercept-fix-evidence/resource-conflict.json)。其中比分和计时属于其他任务，不能作为本次实验数据。

| 必需分析 | 本任务结果 |
| --- | --- |
| 比分 | 未开赛，双方身份/初始终态比分/实际比赛时长/结束方式均无本任务证据 |
| 机器人行为复现 | 无本任务回放，不能分析 |
| 失分原因 | 无本任务比赛，不能分析；不等于未失分 |
| 未得分原因 | 无本任务比赛，不能分析 |

本任务导出帧数与大小：无导出文件；采集范围：未开始；数据缺口：全部仿真数据。
[源码摘要](intercept-fix-evidence/source-hashes.json)记录待部署实现身份；没有构建包/实际部署摘要，不宣称部署完成。未清理共享看板，未停止其他任务。

## 剩余事项

资源释放后仍需完成规定的一次 60 秒仿真、运行产物身份核验、原始回放与四类分析。PR 以草稿交付，整体任务部分完成。范围内未改变其他战术模型，未添加依赖；不能从离线测试推断实战收益。
