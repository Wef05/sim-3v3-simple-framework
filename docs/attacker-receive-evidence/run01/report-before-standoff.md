# Attacker 先拦球后出球

## 实现与版本

基线 `aaee819`；worktree `/private/tmp/attacker-receive-first`；任务分支 `codex/attacker-receive-first`，PR 目标 `MAIN`。主工作区已有修改未转入本任务。Studio 自动更新的本工作区 `.booster-studio/project.json` 不纳入提交。

普通 attacker 直接前往共享预测器的截球点 I，不再预先绕到出球方向的球后方。以延迟补偿的当前球位 B 为原点，最终出球目标为 T；非退化向量 `(I-B)·(T-B)<0`（严格大于 90°）时跳过拦球。补射在观测和补偿球位都不超过 2 m 时直接交接自动踢球；远处仍用球后 0.35 m 接近点。90° 或退化夹角进入正常接球。

正常接球需两个球位均在 0.35 m 内，最近有效观测区间球速不超过 0.3 m/s，并在新观测时间上持续 0.15 s。重复观测不增加稳定时间；未知球速不当作静止。阈值为待校准初值。进入自动踢球后保持至球超出 2.5 m，防角度噪声取消动作；角色变化、阶段变化、时间回退、超过 0.5 s 的决策间隔和观测失效清理记忆。`auto_kick` 表示策略请求，不证明 SDK 执行成功。`contact_confirmed=false`，近身低速不等于身体触球确认。

出球点选择、传球力度、角色竞争与守门职责沿用原逻辑。结构阻挡无安全替代时停止并清理接球记忆。预测 `fallback` 点是窗口末尾追赶参考，不代表模型已找到可达截球。

修改范围：`src/utils/attacker_receiving.py` 新接球状态逻辑；`src/player.py` 接入；`src/utils/ball_motion.py` 提供有有效性约束的观测球速；`src/param.py` 稳定阈值；帧准备、遥测及看板增加 `attack_receive`；对应 Python 和 Node 回归测试。现有选点/结构保护测试明确以已交接踢球为前提，边线测试改验未裁剪的拦球点，开球力度测试通过真实连续静止球观测进入踢球。

## 验证

- 基线：141 项，140 通过，1 项缺少 `checks.goalkeeper_replay_cases` 导入错误。
- 最终全量：155 项，154 通过，同一导入错误；不计为全量通过。
- 专项：44 项通过，覆盖新接球、夹角边界、稳定时间、重复观测、失效重置、补射、选点、边界和开球力度。
- 4 个 Node 看板检查全部通过，含旧回放兼容及新增接球阶段/角度/稳定时间显示。
- JavaScript 语法及 `git diff --check` 通过。

原始结果在 [证据目录](attacker-receive-evidence/)，包括 [基线](attacker-receive-evidence/baseline-tests.txt)、[全量](attacker-receive-evidence/full-tests.txt)、[专项](attacker-receive-evidence/focused-tests.txt)、[看板](attacker-receive-evidence/dashboard-tests.txt)。

## 实验目标与边界

一次 60 秒裁判计时短时测试，确认阶段切换和实际动作表现；不作为胜率/成功率统计。保留原有看板历史，按 session 与开始/结束 ID 导出。对手为官方 AI，随机种子未控制。没有做基线对照赛或参数迭代。

仿真使用已批准行为的构建包。运行时只在工作区为公开接球函数补充类型声明和相应类型导入，没有改变控制逻辑；已保存 [实测接球源码](attacker-receive-evidence/run01/attacker_receiving.py.tested)、[实测差异](attacker-receive-evidence/run01/source-diff.patch) 和 [类型声明差异](attacker-receive-evidence/post-run-types.patch)，最终版本重新通过离线检查。运行版本与最终类型声明版本的产物身份分别记录，不将两者摘要混为一致。

实验结果待附。
