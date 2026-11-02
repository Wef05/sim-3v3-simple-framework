"""集中调参入口。

这里放策略、走位、踢球和避障的可调参数。数值迁移自当前代码文件,
以后优先在本文件调整参数。
"""

from __future__ import annotations

import math


# ======================================================================
# 踢球力度
# ======================================================================

# Executor.kick() 会把力度夹到这个范围内。如果超过范围，因为控制接口不支持，可能会摔倒。
KICK_POWER_MIN = 1.0
KICK_POWER_MAX = 10.0

# 三档动态力度：近传 1、中传 3、远传/射门/解围/其他 5。
KICK_POWER_SHORT_PASS = 1.0
KICK_POWER_MEDIUM_PASS = 3.0
PASS_SHORT_DISTANCE_M = 3.0
PASS_MEDIUM_DISTANCE_M = 6.0
KICK_POWER_DEFAULT = 5.0
KICK_POWER_BACKFIELD = 5.0


# ======================================================================
# Player 走位控制
# ======================================================================

ARRIVE_DIST = 0.15             # 到达目标点阈值 (m)
OMNI_DIST = 1.0                # 距目标小于该值时使用全向近距控制
TURN_THRESHOLD = 0.5           # 远距离行走时,角差超过该值先原地转向 (rad)
MAX_LINEAR = 2.0               # 指令前进速度上限 (m/s)
MAX_ANGULAR = 2.0              # 指令转向速度上限 (rad/s)
LINEAR_GAIN = 1.5              # 平移 P 增益
ANGULAR_GAIN = 2.0             # 转向 P 增益


# ======================================================================
# Player 踢球 / 射门规划
# ======================================================================

KICK_ENTER_M = 2.0             # 距球小于该值进入踢球状态
KICK_EXIT_M = 2.5              # 踢球中距球大于该值才退出踢球状态
RIGHT_FOOT_KICK_CORRECTION_DEG = -5.0  # 右脚出球向左偏 5°，指令统一向右补偿 5°
CHASE_BEHIND_M = 0.35          # 追球时站到球后方的距离


# ======================================================================
# Player 技术动作参数：守门 / 支援 
# ======================================================================

GUARD_SET_BALL_X = -0.7       # 无近时对手触球威胁时的预站位区域分界
GUARD_POST_MARGIN_M = 0.25     # 横向封堵目标与门柱的距离
GUARD_CLEAR_EXTRA_M = 0.6     # 解围范围在小禁区前沿外扩的距离
GUARD_CLEAR_REACH_M = 1.8     # 仅处理守门员附近的门前球
GUARD_KICK_EXIT_M = 0.85
GUARD_GOAL_LINE_MARGIN_M = 0.25
GUARD_PASS_MAX_DIST_M = 6.0
GUARD_PASS_LANE_MARGIN_M = 0.35  # 传球通道在机器人半径外的余量

# 新守门策略：有效能力是待实测的保守初值，不是 SDK 指令上限。
GUARD_FORWARD_SPEED = 0.75
GUARD_LATERAL_SPEED = 0.45
GUARD_BACKWARD_SPEED = 0.40
GUARD_TURN_SPEED = 1.0
GUARD_BODY_RADIUS_M = 0.22
GUARD_ROBOT_CLEARANCE = 0.50
GUARD_REACTION_S = 0.15
GUARD_SHOT_HORIZON_S = 4.0
GUARD_BLOCK_STEP_S = 0.025
GUARD_BLOCK_ARRIVE_M = 0.02
GUARD_ANTICIPATE_S = 1.5
GUARD_PREPOSITION_ADVANCE_M = 0.3
GUARD_TARGET_DEADBAND_M = 0.12
GUARD_OBSERVATION_MAX_AGE_S = 0.25
GUARD_CONTROL_DRIFT_M = 0.15
GUARD_KICK_PREPARE_S = 0.6
GUARD_STAGE_TOLERANCE_M = 0.10
GUARD_RECEIVER_DRIFT_M = 0.40
GUARD_KICK_TIMEOUT_S = 1.2
GUARD_RETRY_S = 0.5
GUARD_APPROACH_BALL_MARGIN_M = 0.33
GUARD_APPROACH_RADIUS_M = 0.6
# 射门前按对手→球延长线预站位；只有近球对手才视为即将出脚。
GUARD_SHOOTER_REACH_M = 1.2
GUARD_SHOOTER_MIN_GAP_M = 0.08
GUARD_SHOOTER_BALL_SPEED = 0.8
GUARD_SHOT_HOLD_S = 0.7
GUARD_RAY_GOAL_MARGIN_M = 0.35
GUARD_RAY_EXIT_MARGIN_M = 0.6
# 积极出脚：仅约束脚前接触几何，不按球速或对手距离优势拒绝直接出脚。
GUARD_DIRECT_REACH_M = 0.60   # 可直接请求出脚的脚前距离，不代表已触球
GUARD_ESCAPE_MOVE_M = 0.25    # 近门防乌龙侧向逃逸的最大准备位移
GUARD_CLOSE_CLEAR_ANGLE_RAD = 0.65
GUARD_CLOSE_CLEAR_BEARING_RAD = 0.75
GUARD_CLOSE_CLEAR_LENGTH_M = 3.0


SUPPORT_DIST_M = 3.0

# 自由人接球站位（米），也可由 freeman(ball_radius=..., opponent_radius=...) 覆盖。
FREEMAN_BALL_RADIUS_M = 7.0
FREEMAN_OPPONENT_RADIUS_M = 3.0
FREEMAN_GOAL_LINE_MARGIN_M = 1.0

# ======================================================================
# Normal 阶段策略
# ======================================================================

GUARD_PLAYER_ID = 2  # READY 初始站位离己方球门最近的球员；各阶段固定防守

ATTACKER_KEEP_TIME_MARGIN_S = 0.3  # 预计接球时间优势不足该值时保留当前 attacker
BALL_INTERCEPT_HORIZON_S = 3.0     # 角色分配只在短期轨迹内寻找接球机会
BALL_INTERCEPT_STEP_S = 0.05       # 接球时间搜索间隔（秒）
BALL_CONTROL_RADIUS_M = 0.35      # 进入此范围视为有机会拿到球，并非踢球触发距离
BALL_MOTION_MAX_GAP_S = 0.5       # 观测中断后不沿用旧速度
BALL_MOTION_MAX_SPEED_MPS = 20.0  # 超过此值视为重置/跳变；需按仿真球速校准
BALL_MOTION_MODE = 'sliding_then_rolling'  # 或 pure_rolling；阶段按实时观测估计
BALL_SLIDE_TRANSITION_S_PER_MPS = 0.06430
BALL_SLIDE_DECEL_MPS2 = 3.86194
BALL_ROLL_SPEED_FACTOR = 0.78855
BALL_ROLL_DECEL_MPS2 = 0.57366
BALL_PURE_ROLL_DECEL_MPS2 = 0.57323
BALL_PURE_ROLL_PLATEAU_S_PER_MPS = 0.07886  # 离散实验接触瞬态拟合，不是真实固定延迟
BALL_MOTION_FIT_SAMPLES = 4      # 最近速度区间，用于在线估计减速度
BALL_MOTION_SPEED_RESET_MPS = 0.75  # 明显加速视作新触球，清除旧阶段


# ======================================================================
# 定位球策略
# ======================================================================

OPP_SET_WALL_DIST_M = 2.0 # 对方开球时，在多少距离之后阻挡


# ======================================================================
# 站位 / 避让
# ======================================================================

OPPONENT_RESTART_AVOID_M = 1.65
CIRCLE_MARGIN_M = 0.3


# ======================================================================
# 踢球目标几何
# ======================================================================

GOAL_TARGET_DEPTH_M = 0.25              # 踢球目标点在球门内的深度 (m)

# ======================================================================
# 障碍物几何参数
# ======================================================================

BALL_OBSTACLE_RADIUS = 0.11             # 比赛场景球碰撞体真实半径 (m)
OPPONENT_RADIUS = 0.25                  # 对手机器人按占地直径 0.5m 近似 (m)
TEAMMATE_RADIUS = 0.25                  # 队友机器人按占地直径 0.5m 近似 (m)
SAFETY_MARGIN = 0.22                    # 通用安全余量 (m)

GOAL_DEPTH = 0.6                        # 前后门柱中心距 (m)
POST_RADIUS = 0.055                     # 门柱 capsule 碰撞体真实半径 (m)
NET_RADIUS = 0.025                      # 球网 box 碰撞体真实半厚度 (m)
NET_POST_INSET_M = 0.05                 # 侧网中心相对门柱向内偏移 (m)
NET_SIDE_END_EXTENSION_M = 0.01         # 侧网在前后门柱中心之外的延伸 (m)
# 来源：football_match_pitch_6_K1.bscene，builtin_revision=2026.08.2。
NET_STEP = 0.35                         # 球网离散化步长 (m)

START_IGNORE = 0.0                      # 起点忽略半径 (m)；起点附近障碍不参与规划
TARGET_IGNORE = 0.0                     # 终点忽略半径 (m)；终点附近障碍不参与规划

# ======================================================================
# 全局路径规划器 (A* Grid Planner)
# ======================================================================

USE_GLOBAL_PATH_PLANNER = True          # 是否启用全局规划器；False 时只使用预测速度避障
GLOBAL_GRID_RESOLUTION_M = 0.35         # 栅格分辨率 (m/格)；越小路径越精细，但计算量越大
GLOBAL_SEARCH_PADDING_M = 1.5           # 场地及起终点之外的搜索余量 (m)，允许跨线绕行
GLOBAL_MAX_GRID_CELLS = 80              # 每轴最多格数；极远目标增大格距以限制搜索规模
GLOBAL_OBSTACLE_MARGIN_M = 0.10         # 障碍物膨胀余量 (m)；在障碍半径上额外扩大多少
GLOBAL_PATH_LOOKAHEAD_M = 0.9           # 从规划路径中提取前方多远的路径点 (m)

# 行走安全层：自身尺寸与障碍半径相加，优先级不覆盖物理间距。
NAV_BODY_RADIUS_M = 0.25
NAV_SAFETY_MARGIN_M = 0.04
NAV_OPPONENT_EXTRA_M = 0.15             # 站位避人额外距离；争球取消此额外距离
NAV_HORIZON_S = 0.9                    # 动态避障前视窗口
NAV_REACTION_S = 0.10                  # 观测/执行延迟预算（须用实测校准）
NAV_BRAKE_S = 0.25                     # 速度过渡预算（须用实测校准）
NAV_PREDICTION_STEP_S = 0.10
NAV_TRACK_MAX_AGE_S = 0.5
NAV_TRACK_MAX_SPEED_MPS = 3.5
NAV_YIELD_MARGIN_M = 0.10              # 低优先级为已规划队友额外留空
NAV_SIDE_HOLD_S = 0.6                  # 局部方向记忆最大年龄 (仿真秒)
NAV_ROUTE_SWITCH_S = 0.35              # 新路线至少节省的预计时间，覆盖改道开销 (s)
NAV_TARGET_RESET_M = 0.5               # 相对本次导航起始目标的累计变化触发重置 (m)
# 沿用守门能力估计作为全队初值；不是 SDK 指令上限，尚需实测标定。
NAV_FORWARD_SPEED_MPS = GUARD_FORWARD_SPEED
NAV_LATERAL_SPEED_MPS = GUARD_LATERAL_SPEED
NAV_BACKWARD_SPEED_MPS = GUARD_BACKWARD_SPEED
NAV_TURN_SPEED_RPS = GUARD_TURN_SPEED

# ======================================================================
# 旧局部 VFH 参数（保留兼容；当前行走使用 NAV_* 预测速度避障）
# ======================================================================

PLAN_LOOKAHEAD = 1.2                    # 前方探测射线长度 (m)；规划器"看"的范围
PLAN_CLEARANCE = 0.35                   # 最小安全余量 (m)；候选方向前方必须大于此值
PLAN_STEP = math.radians(15)            # 候选方向扫描步长 (rad)；越小方向越精细
PLAN_MAX_OFFSET = math.radians(100)     # 最大偏离目标方向的角度 (rad)；扫描范围 ± 该值

# ======================================================================
# 可视化
# ======================================================================

KICK_TARGET_MARK_SIZE_M = 0.18

# 出球风险占位匀速球模型，单位 m/s；不是 SDK 力度，需仿真标定。
KICK_RISK_BALL_SPEED_MPS = 4.0

# 候选目标只用于观测；有限采样与刷新周期控制评估成本。
ATTACK_TARGET_LEAD_M = 0
ATTACK_GOAL_SAMPLES = 7
SHOT_OUTWARD_PENALTY_STEP = 0.05  # 从中央每向外一个候选点，被截率加 5 个百分点。
ATTACK_TARGET_SAMPLES = 16
ATTACK_TARGET_REFRESH_S = 0.5

# 球路与行走避球共用比赛场景的真实球半径。
KICK_LANE_BALL_RADIUS_M = BALL_OBSTACLE_RADIUS

# 仅供最近拦球点计算/看板，不改变策略接球评分。
BLOCK_HORIZON_S = 8.0
BLOCK_STEP_S = 0.025
BLOCK_POSITION_TOLERANCE_M = 0.10  # 从有效接触半径中扣除的误差预算
BLOCK_TIME_MARGIN_S = 0.10       # 提前到位余量

# 自由人射门通道余量（米）：机器人半径 + 球半径 + 2 cm 到达误差。
FREEMAN_SHOT_CLEARANCE_M = NAV_BODY_RADIUS_M + BALL_OBSTACLE_RADIUS + 0.02

# 敌方重启防守剧本：米；保守初值，待仿真实测。
RESTART_TARGET_MARGIN_M = 0.4  # 球排斥半径外留出自身尺寸与制动余量
RESTART_PAIR_GAP_M = 1.0      # 两名外场球员目标点最小间距
RESTART_ANCHOR_DRIFT_M = 0.25 # 小幅球位噪声不重排阵型
OUR_KICKOFF_THREE_SHIFT_M = 1.0  # 看板左上方向的总位移
