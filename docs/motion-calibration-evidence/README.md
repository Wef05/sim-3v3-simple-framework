# 标定证据

原始文件 `*.jsonl.gz` 第一行是该段实际协议，后续行是 Agent 的原始观测、请求、状态；解压后可检查。`*-dashboard.json.gz` 和 `baseline-01-replay.json.gz` 为完整原始看板帧，解压后可导入现有看板。每段独立保存，避免跨重启或时钟回退求导。`*.metadata.json` 记录帧数、范围、缺口和 SHA256。

| 数据段 | 驱动项目 | 程序版本 | 结束/续测说明 |
| --- | --- | --- | --- |
| baseline-01 | 基线 1 | v1 | 序列状态错误提前完成，原样保留 |
| baseline-02 | 基线 2–17 | v2 | 第 18 项归位超时，未驱动；官方仿真重置 |
| baseline-03 | 基线 18–42 | v2 | 完成全部剩余基线 |
| validation-01 | 无 | v2 | 第 1 项朝向死区，主动停用 |
| validation-02 | 验证 1–3 | v3 | 第 4 项近点后退不推进，归位超时 |
| validation-03 | 验证 4–9 | v4 | 第 10 项短前进脉冲未归位，超时 |
| validation-04 | 验证 10–12 | v5 | 最后续测段，终态见对应 end-boundary |

`identity-v1/v2/v3/v4.json` 与 `identity.json`（v5）记录源码、包及部署的摘要。v2–v4 的旧版标定源文件与构建包单独保存；最终源文件在本分支，构建包为 calibration-v5.agent。v1 构建包未单独归档，只保留当时的摘要核验及原始数据。旧 identity 的 window_open_observed_utc 字段来自早期模板，未用于实验计时或结论；以原始仿真观测时间为准。

`frozen-baseline.json` 在开始验证前生成，`frozen-baseline.sha256` 和 `validation-freeze.json` 记录冻结身份。验证中只修复归位程序，不更新该基线，不改变恒定驱动/反向幅度、测量时长或验收门槛。

分析入口和复现命令见 [标定说明](../motion-calibration.md)，结论、失败说明与参数建议见 [实验报告](../motion-calibration-report.md)。图在 figures/；`dashboard.png` 是本任务界面验收截图。

采集器保留原始共享看板历史。原始未压缩副本、增量采集 spool 和诊断日志仍在本任务本地 evidence 目录；提交的 gzip 是相应原始记录的无损压缩版本。运行中断和等待不作为比赛时长，历史比分 4:5 不属于标定成绩。
