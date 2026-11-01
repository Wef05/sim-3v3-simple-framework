# 本机运行参考

以下是 2026-11-01 在 winter3 项目验证过的线索，使用前重新核实。

## 启动与身份

项目曾位于 `/Users/limingzhe/Desktop/winter3/sim-3v3-simple-framework`。`.booster-studio/project.json` 选择虚拟机器人 `4x2a0`，实际比赛场景为 `football_match_pitch_6_K1`，容器为 `4x2a0-k1`。`agent-dev-` 开头的是项目开发容器。

通过 CUA 获取 Booster Studio 当前界面，点击“激活、构建、部署和运行代理”。不要复用历史元素索引。官方运行时管理 owner lease；单独 `docker start` 不能保证持续运行，不自行伪造 heartbeat。

容器部署日志曾为 `/tmp/booster_agent_deploy/football3v3_runner/football3v3-run.log`，开头包含双方 Agent 路径。本次测试我方 `com.example.lqinternalmatch`，对手 `com.booster.default3v3ai`；实际身份按本场日志核验。

## 裁判 API

在仿真容器内访问 `http://127.0.0.1:38383`，宿主机未必映射端口：

| 请求 | 用途 |
| --- | --- |
| GET `/status` | `game.state/timeRemaining/regularDurationSeconds`、双方比分、match 身份 |
| POST `/match/start` | 开始比赛；保存响应并核验状态推进 |
| POST `/match/end` | 主动结束，`finishReason=operator_finish` |
| GET `/result` | 完赛结果；未完成时可能返回非成功状态 |
| GET `/events/history` | 裁判事件 |

可用 `docker exec CONTAINER curl --fail --silent --max-time 8 URL` 或容器内 Python 读取，设置网络和命令超时。

该版本 start 接口不读取请求体中的时长。源码虽声明 `regular_duration_seconds`、`GAME_CONTROL_REGULAR_DURATION_SECONDS` 等启动参数，不代表 start API 支持设置时长；不要修改安装包或绕过场景约束。本次使用默认 600 秒赛制，在主时间下降约 60.11 秒后主动结束。

## 看板 API 与格式

优先复用现有服务。确认未运行后才从项目启动 `python3 tools/tactics_dashboard/server.py`。曾使用宿主机 HTTP 8765、UDP 9871，Agent 目标 IPv4 为 `192.168.65.254`。检查 `src/telemetry_config.py` 和实际路由；容器的 localhost 不代表宿主机。

- GET `/api/state`：按队伍返回 `packet` 和 `age`，开赛前确认新鲜且持续变化。
- GET `/api/history?team=1&after=0&limit=1`：取得服务 `session` 和队伍 `first_id/last_id`。这里的 last_id 是整个队伍缓存末端，不是本页末端。
- POST `/api/history/clear`：使用 `Content-Type: application/json`，请求体为 `{"team":1}`（替换为本次队伍）。清除该队伍历史和最新快照，服务 `session` 会改变；其他队伍不受影响。前置清理只在确认没有测试运行时执行；赛后清理只在导出已落盘且校验通过后执行。清理后重新取得 session。
- 清除接口不会停止 Agent 或 UDP 接收，发送源持续运行时马上会出现新帧。赛后先通过官方入口停止本次 Agent/遥测发送，再清理并复查；赛前则记录清理后的边界来排除新产生的待机帧。
- 分页上限 500，下一页使用本页最后一帧 id。全局 id 可跨队伍跳号，跳号本身不等于丢帧。
- 服务只在内存缓存历史，重启前先备份；丢失历史不能从最新快照恢复。
- 回放顶层含 `format: soccer-tactics-replay`、`version: 1`、`team_id`、`exported_at`、`recording_hz`、`frames`；每帧保留 `id/time/recorded_at/packet`。
- 当时前端上限 54000 帧；导入大小上限及录制频率以当前代码为准。看板为尽力传输，不代表完整无丢帧的官方比赛记录。

若 Docker 或本机网络被执行权限阻止，使用工具的权限机制；不要把权限失败误判为服务未启动。

## Studio 界面连接恢复与 worktree（2026-11-01 验证）

- `getApp` 返回 `timeoutReached` 时，不能据此断言 Studio 网络故障或崩溃。先只读核对应用是否运行，再用实际 bundle ID `com.boosterrobotics.boosterstudio` 重新取得界面；最多做两次针对性的重新连接，持续失败则保留错误并报告。不同轮次可能需要重新 `getApp`，旧句柄遇到 “Computer Use is not active” 时不要继续发送输入。
- 跨对话框操作应先取得新界面，再填写或确认；快捷键后立即连续粘贴/回车可能落到旧控件。每次从当前 AX 状态获取索引，不假定上一次按钮仍有效。
- 本次原生“打开文件夹”对话框中 Open 一直禁用，不能确认通用原因。已验证的备选方式：创建只含目标 worktree 绝对路径的临时 `.code-workspace` 文件，在 Studio 的快速打开（Cmd+P）输入该文件完整路径，回车进入编辑器，再点击实际显示的“打开工作区”。这样由官方 UI 加载同一个目录，不复制或改写主工作区源码。
- 工作区目录和 `.code-workspace` 文件可能分别需要信任。核对确为本任务代码和自己生成的工作区文件，在已有运行授权下按适用权限流程处理；只信任具体任务文件/目录，不扩大到整个 `/tmp` 或主目录，也不全局关闭信任保护。成功后确认受限模式已退出、窗口指向本任务工作区，再使用正式构建部署按钮。
- 若部署动作被自动审批拦截，不换入口绕过。补充资源占用证据后再评估：相关任务状态、裁判状态、容器 Running/Paused/Pid/FinishedAt 及调度进程。容器已停止时，`docker exec /status` 失败可能只说明裁判进程不存在；结合其他证据排除运行中/待开赛测试，不能无限等待已停止容器的接口。仍不能排除冲突时向用户说明。
- Python 未混淆构建可逐文件比较 `src/`、`.agent` ZIP 中的 `site-packages/<package>/`、runner 指向的容器解包目录。必须记录实际路径和摘要。构建工具可能将仅含说明文字的顶层 `__init__.py` 置空；只有读取并确认其没有执行逻辑后才可将该差异单列，不能豁免任意代码差异。

## 结束边界与时钟重置

保留旧历史时，仿真重置不一定更换看板服务 session。2026-11-01 实测，上一场结束后由用户立即重置开赛，同一次导出窗口混入 `context.now` 从 77.096 回退到 0.012 的另一场，而帧 `time`（接收端时间）始终递增。必须在本场结束时立即记录结束帧 ID，使用 `--until-id` 导出。脚本现在拒绝含仿真时钟回退的范围；遇到拒绝时保存原始采集窗口，结合裁判事件和明确的时钟重置点确定边界，不能将混合文件称为单场回放。
