"""分层之间的数据契约：角色任务描述职责，动作意图描述本帧期望行为。

统一约定：位置为场地坐标，单位米；朝向为场地角度，单位弧度。
身体坐标转换与右脚补偿只在 executor 中做，上层不能提前补偿。
所有记录使用 frozen=True：协调阶段替换整条意图，不修改别人已经看到的对象。
意图是本帧的请求，不表示动作已完成，也不是可跨帧自动执行的任务队列。
"""
from dataclasses import dataclass
from enum import Enum
from .param import ARRIVE_DIST

class Role(str, Enum):
    ATTACKER = 'attack'
    GUARD = 'guard'
    FREEMAN = 'freeman'
    READY = 'ready'
    OPP_KICKOFF = 'opp_kickoff'
    STAY = 'stay'
    UNAVAILABLE = 'unavailable'

@dataclass(frozen=True)
class RoleTask:
    """分配层给角色层的任务参数，不含路径或 SDK 控制量。

    slot 是该阶段的站位序号，不是球员编号；具体坐标由角色行为决定。
    pass_to 是期望传球对象，守门行为仍会检查通道，再决定传球或解围。
    our_kickoff 用于选择准备阵型；reason 记录停止或不可行动的原因。
    """
    role: Role
    slot: int = 0
    pass_to: int | None = None
    our_kickoff: bool = False
    reason: str = ''

@dataclass(frozen=True)
class KickTarget:
    """保留踢球对象的语义，让规划层负责把对象解析成具体目标点。

    goal 表示对方球门；teammate 用 player_id；point 用显式场地坐标。
    优先通过三个工厂方法创建，避免 kind 与其他字段不一致。
    当前队友目标解析为其观测位置；未来可在规划层增加传球提前量。
    """
    kind: str
    player_id: int | None = None
    point: tuple[float, float] | None = None

    @classmethod
    def goal(cls):
        return cls('goal')

    @classmethod
    def teammate(cls, player_id):
        return cls('teammate', player_id=player_id)

    @classmethod
    def position(cls, point):
        return cls('point', point=point)

@dataclass(frozen=True)
class MoveIntent:
    """移动目的地和约束；路径及速度由运动控制器计算。

    face=None 表示不要求指定的最终朝向，不等于禁止转身。
    maintain_face=True 时，远距离也采用全向移动并维持指定朝向。
    avoid_ball 可在主动接球时关闭；arrive_dist 是允许的到达误差（米）。
    """
    target: tuple[float, float]
    face: float | None = None
    avoid_ball: bool = False
    avoid_robots: bool = False
    arrive_dist: float = ARRIVE_DIST
    maintain_face: bool = False
    direct_path: bool = False  # 限时动作：仅走已验算直线，新增障碍时停止而非绕行

@dataclass(frozen=True)
class FaceIntent:
    """原地转向；执行时先退出踢球，避免踢球控制器继续占用底盘。"""
    theta: float

@dataclass(frozen=True)
class KickIntent:
    """请求按目标出球；style 选择力度策略，不直接携带 SDK 力度。

    现有类型为 shot（5 档）、pass（按距离选择 1/3/5 档）、
    clear（5 档）；角色层负责选择用途。
    """
    target: KickTarget
    style: str = 'shot'

@dataclass(frozen=True)
class StopIntent:
    """明确取消上一动作并停车；不能用遗漏意图来表达等待。"""
    pass

@dataclass(frozen=True)
class RecoverIntent:
    """不可行动时的占位意图；执行层按实时状态尝试起身或切换模式。

    如果到执行时已经恢复就绪，本帧仍停止，下一帧再参与正常角色分配。
    """
    pass

ActionIntent = MoveIntent | FaceIntent | KickIntent | StopIntent | RecoverIntent
