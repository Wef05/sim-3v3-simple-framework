# 路径规划耗时与稳定性验证报告

日期：2026-11-01（Asia/Shanghai）。状态：实现与离线验证完成；真实 1 分钟仿真受环境阻碍，尚未完成验收。

## 源码与范围

- 基线：`d2c84dd77b86cba46167ee30b40f2e4925d91225`，独立分支 `codex/navigation-time-stability`，PR 目标为用户已发布的 `MAIN`（同一基线）。
- Worktree：`/Users/limingzhe/.codex/worktrees/navigation-time-stability/sim-3v3-simple-framework`。
- 修改：`src/utils/path_planner.py`（耗时 A*）、`src/actions/motion_controller.py`（路线保持与局部评分）、`src/actions/controller.py`（状态清理）、`src/param.py`、看板 `dashboard.js`、两份专项测试和导航文档。无新依赖；不改变角色与战术架构。
- 源码摘要、manifest、配置和环境记录：[preflight-01.json](navigation-time-stability-evidence/preflight-01.json)。记录中的摘要对应本次实现；构建/部署未发生，没有可核验的运行包身份。
- 主工作区原有 `.booster-studio/project.json` 修改未纳入。测试与报告原受 `.gitignore` 忽略，将精确强制纳入本任务提交，不改变全局忽略策略。

## 验证结果

- 导航专项：38 项通过（原有 29 项 + 新增 9 项），覆盖连续移动障碍、转身/侧移代价、受阻改道、安全路径、直线限制、任务/阶段/时间重置及累计目标漂移。
- 完整 Python 检查：运行 64 项，63 项通过，1 项导入错误。缺少 `checks.goalkeeper_replay_cases` 导致守门回放测试无法运行；基线导出在相同环境运行 55 项，也有同一项错误（54 项通过）。没有把该错误计为通过。
- 导航看板、守门看板检查通过；JavaScript 语法检查通过。详见证据目录中的 `*-tests.txt`、`dashboard-syntax.txt`。

## 相同输入的前后对比（离线）

固定我方 1 号位置 `(-2, 0, 0)`，目标 `(2, 0)`；障碍为队友 2 号或对手 1 号，位置 `(0, 0.08*sin(0.6*i))`，每帧仿真时间增量 0.05 s。各 160 帧，时间 1.00–8.95 s。以连续引导点 y 符号变化计换边，不能等同真实碰撞或比赛行为。无随机抽样，不筛选帧。

| 场景 | 换边次数（基线 → 修改后） | 单次规划均值 ms | 单次规划 P95 ms |
| --- | --- | --- | --- |
| 移动队友 | 31 → 1 | 0.744 → 2.215 | 0.816 → 2.340 |
| 移动对手 | 31 → 0 | 0.773 → 2.269 | 0.886 → 2.667 |

原始逐帧输入/命令/引导点/耗时保存在 [baseline-benchmark.json.gz](navigation-time-stability-evidence/baseline-benchmark.json.gz) 和 [updated-benchmark.json.gz](navigation-time-stability-evidence/updated-benchmark.json.gz)。运行：`PYTHONPATH=. python3 docs/navigation-time-stability-evidence/benchmark.py`，基线使用同一脚本及 `git archive d2c84dd` 导出源码。耗时是本机一次顺序测量，受主机负载影响；不能推导比赛帧率。

简化闭环积分还验证了机器人可到达目标：队友场景 188 帧、对手场景 202 帧，均到达。运动积分假定指令平移实现 37.5%、旋转实现 50%，不模拟物理接触、步态、跌倒和通信。逐帧证据见 [integration.json.gz](navigation-time-stability-evidence/integration.json.gz)，复现脚本为同目录 `integration.py`。原始 JSON 无损 gzip 压缩入库，解压后可直接读取；本地未压缩副本也保留。

## 默认 1 分钟仿真尝试：准备阶段受阻

使用 [booster-sim-test](/Users/limingzhe/.codex/skills/booster-sim-test/SKILL.md) 的流程，唯一尝试编号 `preflight-01`。只读检查发现虚拟机器人 `4x2a0-k1` 已停止，只有开发容器运行；看板当前队伍数据年龄约 1212 s，不能作为本场新鲜数据。

通过显示名和 bundle ID 连接 Studio 均返回 `Computer Use server error -10005: timeoutReached`，未获得可用界面句柄，无法按技能通过 Studio 构建部署本任务 worktree。未自行伪造运行时 owner lease，也未复用旧包开赛。技能引用的 `docs/tactics-dashboard.md` 在基线缺失，已阅读技能自带运行参考并核对现有服务。未启动或终止比赛，未清除或重启看板，未追加比赛。

| 必须分析项目 | 本次结果 |
| --- | --- |
| 比分 | 比赛未启动；双方实际身份、初始/结束比分均未核验。manifest 预期我方为 `com.example.lqinternalmatch`，对手未核验。实际比赛计时 0 s，没有结束方式或终态。 |
| 机器人行为复现 | 无本场回放，无法给出真实比赛机器人行为结论。离线证据中队友场景仅在约 1.10 s（frame 2）换边一次，之后保持；对手场景未换边。该观察仅属于上述固定位置输入。 |
| 失分原因 | 无比赛、无失分事件数据，无法分析，不等于“零失分”。 |
| 未得分原因 | 无真实进攻片段，无法分析，不等于“进攻没有机会”或“策略未得分”。 |

未导出本场看板 JSON：本场帧数 0，大小 0 字节，采集范围不存在；没有将旧看板历史冒充本次数据。未取得本场 runner 日志或比赛 `/result`。准备失败的原始检查记录已保存；恢复 Studio 后仍需运行一次 60 仿真秒测试并补齐原始回放、双方身份、比分、终态与四类分析。当前不能宣称完整开发流程已完成。

## 风险与限制

- 速度/转向能力是沿用的保守初值，未经本次实测标定；不精确建模加速、步态、近目标减速或全向移动中的朝向变化。预计耗时仅用于相对选路。
- 状态扩展及方向候选增加计算量；当前简单场景均值约 2–3 ms，但拥挤或复杂场景可能更慢，需在真实运行中验证预算。
- 0.35 s 换路门槛可能保留略长路线；安全检查优先，真正堵路时仍可能换边。没有承诺面对任何对手突然变向都不会碰撞。
- 路线保持采用现有 0.04 m 安全余量，新规划采用 0.10 m 余量；变化已进入测试，但定位误差和真实身体运动仍须仿真确认。
- 离线固定位置场景的换边计数和简化运动积分不构成比赛效果或胜率证据。真实仿真与缺失的守门回放测试仍待完成。

## 证据大小

目录：[navigation-time-stability-evidence](navigation-time-stability-evidence/)。

- `baseline-benchmark.json.gz`：10,334 字节（无损压缩原始 JSON）。
- `integration.json.gz`：22,228 字节（无损压缩原始 JSON）。
- `preflight-01.json`：2,529 字节。
- `updated-benchmark.json.gz`：10,030 字节（无损压缩原始 JSON）。
