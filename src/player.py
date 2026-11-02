"""角色行为层：决定去哪、朝向哪、是否踢球和踢向什么对象。

模块级 decide 是 main 唯一调用的整队入口；Player.decide 是内部单人分派。
决策函数可以更新行为记忆和调试标签，但只能返回意图，不能执行底盘命令。
共享观测与截球结果由 prepare_frame 准备；先完成所有个人决策，再统一协调。
"""
from __future__ import annotations
import math
from dataclasses import replace
from .utils.interception_risk import estimate_interception_risk
from .utils.kick_lane import check_kick_lane
from .utils.blocked_attack import blocked_attack_target
from .utils.scoring_probability import evaluate_scoring
from .utils.shot_selection import select_shot
from .utils.pass_selection import select_pass
from .utils.attack_targets import evaluate_attack_targets, TeammateMotion
from types import MappingProxyType
from .framework.types import GameState, Penalty, SetPlay
from .param import *
from .role_allocator import Phase, get_phase
from .intents import (Role, RoleTask, KickTarget, MoveIntent, FaceIntent,
                      KickIntent, StopIntent, RecoverIntent)
from .actions.controller import ActionController
from .actions.kick_planner import resolve_target
from .utils.geom import (angle_to, clamp, dist, own_goal, own_goal_area_center,
                         opponent_goal, behind_ball, goalkeeper_home)
from .utils.free_position import nearest_free_position, shot_triangle
from .utils.ball_motion import BallMotion
from .utils.restart_defense import restart_intent, kickoff_targets

class Player:
    """跨帧存活的角色行为对象，通过组合持有执行控制器。

    _guard_memory 保存守门站位迟滞及有限时出球承诺。
    is_kicking 属于执行状态：由控制器维护，行为层只读取它来选择距离阈值。
    不把两种状态合并，避免「想踢球」被误认为「已经下发踢球」。
    """
    def __init__(self, player_id, config, _backend):
        self.controller = ActionController(player_id, config, _backend)
        # 构造时提供空估计器；正式逐帧运行时会替换成全队共享实例。
        self.motion = BallMotion()
        self.ball_intercept = None
        self.threat = None
        self.action = 'init'
        self.tactical_target = None
        self.freeman_region = None
        self._guard_memory = None

    # 以下属性只转发观测和运行时接线，不提供 walk_to/kick 等执行接口。
    # backend 的读写兼容框架注入与关闭流程，角色行为不应直接使用它下命令。
    @property
    def id(self):
        return self.controller.id

    @property
    def config(self):
        return self.controller.config

    @property
    def pose(self):
        return self.controller.pose

    @property
    def mode(self):
        return self.controller.mode

    @property
    def is_fallen(self):
        return self.controller.is_fallen

    @property
    def fall_down_state(self):
        return self.controller.fall_down_state

    @property
    def penalty(self):
        return self.controller.penalty

    @property
    def is_penalized(self):
        return self.controller.is_penalized

    @property
    def is_kicking(self):
        return self.controller.is_kicking

    @property
    def context(self):
        return self.controller.context

    @context.setter
    def context(self, value):
        self.controller.context = value

    @property
    def _backend(self):
        return self.controller._backend

    @_backend.setter
    def _backend(self, value):
        self.controller._backend = value

    @property
    def _mode(self):
        return self.controller._mode

    @_mode.setter
    def _mode(self, value):
        self.controller._mode = value

    @property
    def _fall_down_state(self):
        return self.controller._fall_down_state

    @_fall_down_state.setter
    def _fall_down_state(self, value):
        self.controller._fall_down_state = value

    def decide(self, task):
        """根据任务产生一个初步意图；本方法不读取队友尚未完成的决策。

        需要根据队友意图改变动作时，在模块级 coordinate 中统一处理，
        不要在这里读取正在逐个填充的意图表，否则会依赖球员遍历顺序。
        每次清空本帧显示目标，避免上一个角色的目标残留在新角色上。
        """
        restart = task.role in (Role.OPP_KICKOFF, Role.OPP_SET_PLAY)
        if not restart:
            self._restart_memory = None
        if task.role != Role.GUARD:
            self._guard_memory = None
        self.guard_decision = None
        self.action = task.role.value
        self.tactical_target = None
        self.freeman_region = None
        if task.role == Role.ATTACKER:
            # 调试阶段：我方开球固定传给 3 号，其余阶段默认射门。
            target = 3 if get_phase(self.context) == Phase.OUR_KICKOFF else 0
            return self.attack(target)
        if task.role == Role.GUARD:
            return self.guard(task.pass_to)
        if task.role == Role.FREEMAN:
            return self.freeman()
        if task.role == Role.READY:
            # 分配层只给 slot；把任务解释成「去哪里、面向哪里」是角色层职责。
            field = self.context.field
            if not task.our_kickoff and task.slot in (0, 2):
                target = kickoff_targets(self.context)[0 if task.slot == 0 else 1]
                return MoveIntent(target, face=0.0, avoid_ball=True, avoid_robots=True,
                                  ball_keepout=field.circle_radius)
            shift = OUR_KICKOFF_THREE_SHIFT_M / math.sqrt(2)
            slots = [
                (-field.circle_radius if task.our_kickoff else -field.circle_radius - .5, 0.0),
                goalkeeper_home(self.context),
                (-.5 - shift, field.circle_radius + 2 + shift) if task.our_kickoff else
                (-field.length / 2 + field.penalty_area_length, 0.0),
            ]
            return MoveIntent(slots[task.slot], face=0.0, avoid_ball=True, avoid_robots=True)
        if restart:
            intent, self._restart_memory, label = restart_intent(
                self.context, self.pose, task.slot, task.role == Role.OPP_KICKOFF,
                getattr(self, '_restart_memory', None))
            self.action = f'{task.role.value}:{label}'
            self.tactical_target = getattr(intent, 'target', None)
            return intent
        if task.role == Role.UNAVAILABLE:
            self.action = task.reason
            return RecoverIntent()
        self.action = task.reason or 'stay'
        return StopIntent()

    def attack(self, kick_target: int | tuple[float, float] = 0):
        """追球并踢球：0 为敌方球门，1–3 为对应编号队友的当前位置。

        支持指定坐标目标；队友位置未知时停止。
        踢球迟滞:距球 ≤ ENTER 进入踢球;进入后距球 > EXIT 才退出(EXIT > ENTER),
        防边界抖动。追球使用预计最早可达的未来球位；窗口内不可达时使用窗口末尾
        球位。到该预测位置后方 ``CHASE_BEHIND_M`` 处准备踢球，走位目标保留真实球后站位。
        踢球仍须观测球位和补偿延迟后的当前球位均在近距阈值内，不能对未来球位踢空。
        """
        self.tactical_target = None
        self.attack_choice = None
        ball = self.context.ball if self.context is not None else None
        current_ball = self.motion.predict_ball_position(0.0)
        if ball is None or self.pose is None or current_ball is None:
            return StopIntent()

        # 复用候选概率缓存；正常射门随后按扣分后分数选点。
        cached = getattr(self, '_candidate_cache', None)
        receivers = tuple(sorted(pid for pid in getattr(self, 'guard_receivers', ())
                                 if pid != self.id and pid in self.context.teammates
                                 and self.context.teammates[pid].pose is not None))
        signature = (receivers, tuple(getattr(self, 'evaluation_freeman_ids', ())), tuple((pid, r.pose is not None) for pid,r in sorted(self.context.teammates.items())),
                     tuple((pid, r.pose is not None) for pid,r in sorted(self.context.opponents.items())),
                     (self.ball_intercept or {}).get('status'))
        if (cached is None or signature != getattr(self, '_candidate_signature', None)
                or not 0 <= self.context.now-cached['evaluated_at'] < ATTACK_TARGET_REFRESH_S):
            tracker = getattr(self, 'teammate_motion', None)
            if tracker is None:
                tracker = TeammateMotion()
                tracker.update(self.context)
            cached = evaluate_attack_targets(self.context,self.id,self.pose,self.motion,
                                              self.ball_intercept or {},tracker)
            self._scoring_cache = evaluate_scoring(self.context,self.pose,self.motion,cached,
                                                   getattr(self,'evaluation_freeman_ids',()), receivers)
            self._candidate_cache, self._candidate_signature = cached, signature
        self.attack_candidates = cached
        self.scoring_probability = dict(self._scoring_cache, decision_enabled=False)

        origins = ((ball.x, ball.y), current_ball)
        automatic_pass = False
        if kick_target == 0:
            self.attack_choice = select_shot(self.context.field, cached, origins)
            if get_phase(self.context) == Phase.NORMAL:
                self.attack_choice = select_pass(self.context, cached, self._scoring_cache,
                                                 self.attack_choice, origins, receivers)
                self.scoring_probability['decision_enabled'] = True
            self.action = 'attack:' + self.attack_choice['reason']
            selected = self.attack_choice['selected']
            if selected is not None:
                kick_target = tuple(selected['target'])
                automatic_pass = selected['kind'] == 'pass'

        aim = (KickTarget.goal() if kick_target == 0 else
               KickTarget.position(kick_target) if isinstance(kick_target, tuple) else
               KickTarget.teammate(kick_target))
        # 追球需要知道出球方向，因而也使用相同的目标解析工具来选球后点。
        # 这里只使用几何目标；真正的踢球力度、坐标变换仍留给执行阶段。
        target = resolve_target(self.context, aim)
        if target is None:
            return StopIntent()

        # 在追球前选择替代方向，保证球后站位与最终出球方向一致。
        # 执行器以观测球位规划，因此观测与延迟补偿球位都必须通过检查。
        lane = next((result for origin in origins
                     if (result := check_kick_lane(self.context.field, origin, target))['blocked']), None)
        # 队友目标必须保留传球语义，开球传给 3 号也按距离调度力度。
        style = 'pass' if automatic_pass or aim.kind == 'teammate' else 'shot'
        if lane is not None:
            fallback = blocked_attack_target(self.context, self.id, origins)
            if fallback is None:
                if self.attack_choice is not None:
                    self.attack_choice.update(actual_target=None, execution='blocked_stop')
                self.action = 'attack:blocked:no_safe_alternative:' + lane['reason']
                return StopIntent()
            target, label = fallback
            aim, style = KickTarget.position(target), 'pass'
            self.action = 'attack:fallback:' + label

        self.tactical_target = target
        if self.attack_choice is not None:
            self.attack_choice.update(actual_target=target, execution=style)

        self.interception_risk = estimate_interception_risk(
            self.pose, self.motion, self.context.opponents, target, self.ball_intercept,
            ball_speed=KICK_RISK_BALL_SPEED_MPS)

        # 同时检查观测球位和延迟补偿球位，取更远距离，避免球已离开却仍踢空。
        # 此处只是决定是否请求踢球，不修改控制器的 is_kicking。
        d = max(
            dist(self.pose.x, self.pose.y, ball.x, ball.y),
            dist(self.pose.x, self.pose.y, *current_ball),
        )
        should_kick = d <= (KICK_EXIT_M if self.is_kicking else KICK_ENTER_M)

        if should_kick:
            return KickIntent(aim, style=style)
        else:
            # 使用本帧与角色分配共享的截球结果。
            if self.ball_intercept is None or self.ball_intercept['status'] == 'unavailable':
                return StopIntent()
            predicted_ball = self.ball_intercept['target']
            chase = behind_ball(*predicted_ball, target, CHASE_BEHIND_M)
            # 保留球后接近点；边球、角球及门内出球可能需要场外站位。
            # 实体障碍仍由导航层检查。
            return MoveIntent(chase)

    def guard(self, pass_to: int | None = None):
        """守门策略独立维护预站位、限时封堵和出球记忆。"""
        from .utils.goalkeeper import decide_guard
        return decide_guard(self, pass_to)

    def freeman(
        self, ball_radius: float = FREEMAN_BALL_RADIUS_M,
        opponent_radius: float = FREEMAN_OPPONENT_RADIUS_M,
    ):
        """自由人：到最近接球位置等待，就位后面朝敌方门线中心。

        站位须在场内、距球不超过 ball_radius、距每个已知对手至少
        opponent_radius，且在敌方门线前至少 1 米；只取以球为原点、
        坐标轴平行场地坐标轴、包含敌方门中心的四分之一圆。
        球的水平延长线穿过敌方大禁区（含边界，即 |ball.y|<=大禁区宽/2）时，
        改取朝向敌方球门的半圆，不限制相对球位的 y 方向。
        排除本帧 attacker 与敌方两门柱的三角形，并留出身体及球体安全余量。
        每帧重算；无可行点或观测不足时停止，不主动踢球。
        """
        ctx, pose = self.context, self.pose
        exclusion = getattr(self, "freeman_shot_triangle", ())
        self.freeman_region = dict(shot_triangle=exclusion, shot_clearance=FREEMAN_SHOT_CLEARANCE_M, ball_radius=ball_radius, opponent_radius=opponent_radius,
                                   goal_line_margin=FREEMAN_GOAL_LINE_MARGIN_M)
        target = None if ctx is None or pose is None else nearest_free_position(
            ctx, pose, ball_radius, opponent_radius, FREEMAN_GOAL_LINE_MARGIN_M,
            exclusion=exclusion, shot_clearance=FREEMAN_SHOT_CLEARANCE_M,
        )
        self.tactical_target = target
        if target is None:
            self.action = "freeman:stop"
            return StopIntent()
        face = angle_to(pose.x, pose.y, ctx.field.length / 2.0, 0.0)
        distance = dist(pose.x, pose.y, *target)
        if distance <= 1e-9:
            self.action = "freeman:wait"
            return FaceIntent(face)
        self.action = "freeman:move"
        # 不使用普通站位的到达容差提前停在可行域外。
        return MoveIntent(target, face=face, avoid_ball=True, avoid_robots=True,
                     arrive_dist=min(0.02, distance / 2.0))

    def support(self):
        """支援:站在 球→己方门中心 连线上距球 ``SUPPORT_DIST_M`` 处补防。

        站位点在球与己方门之间的封堵线上。
        """
        ctx = self.context
        if ctx is None:
            return StopIntent()

        ball = self.context.ball
        if ball is None:
            return StopIntent()
        gx, gy = own_goal(ctx)
        bx, by = (ball.x, ball.y)
        dx, dy = gx - bx, gy - by
        d = math.hypot(dx, dy)
        if d < 1e-6:
            ux, uy = -1.0, 0.0
        else:
            ux, uy = dx / d, dy / d
        along = min(SUPPORT_DIST_M, d)
        tx = bx + ux * along
        ty = by + uy * along

        # 不越过己方底线(x >= 门线 + 0.3),横向夹进场内
        half_l = ctx.field.length / 2.0
        half_w = ctx.field.width / 2.0 - 0.3
        tx = clamp(tx, -half_l + 0.3, half_l)
        ty = clamp(ty, -half_w, half_w)
        return self.move_to_position((tx, ty))

    def move_to_position(self, target: tuple[float, float] | None):
        """走到站位点(支援/防守/避让),面向球,避球+避机器人。"""
        if target is None:
            return StopIntent()
        face = None
        ball = self.context.ball if self.context is not None else None
        if ball is not None and self.pose is not None:
            face = angle_to(self.pose.x, self.pose.y, ball.x, ball.y)
        return MoveIntent(target, face=face, avoid_ball=True, avoid_robots=True)


def coordinate(context, assignments, preliminary):
    """Assign navigation policy from roles; actions resolves geometric conflicts.

    Do not modify targets here or issue commands. Priority only allocates space;
    every robot still observes physical clearance, including against opponents.
    """
    final = dict(preliminary)
    restricted = get_phase(context) in (Phase.OPP_KICKOFF, Phase.OPP_SET_PLAY)
    for pid, intent in preliminary.items():
        if not isinstance(intent, MoveIntent):
            continue
        role = assignments[pid].role
        priority = {Role.ATTACKER: 80, Role.GUARD: 60, Role.FREEMAN: 20}.get(role, 10)
        contest = role == Role.ATTACKER or (role == Role.GUARD and intent.direct_path)
        final[pid] = replace(intent, motion_priority=max(intent.motion_priority, priority),
            opponent_mode='contest' if contest and not restricted else 'avoid',
            avoid_ball=True if restricted else intent.avoid_ball,
            ball_keepout=max(intent.ball_keepout, context.field.circle_radius) if restricted else intent.ball_keepout)
    return final

def decide(context, players, assignments, store):
    """main 调用的整队唯一入口：收集初稿 → 协调 → 保存并返回最终意图。

    prepare_frame 已为所有球员绑定同一帧观测；本阶段不推进球运动估计。
    store 中分别保留初步和最终意图，便于检查协作阶段究竟改变了什么。
    不可变性覆盖当前这些标量/元组字段；未来增加可变嵌套字段时也需冻结它们。
    """
    preliminary = {}
    freeman_ids = tuple(pid for pid, task in assignments.items() if task.role == Role.FREEMAN)
    attacker = next((context.teammates.get(pid) for pid, task in assignments.items()
                     if task.role == Role.ATTACKER), None)
    exclusion = shot_triangle(context, attacker.pose if attacker is not None else None)
    for p in players:
        p.freeman_shot_triangle = exclusion
        p.evaluation_freeman_ids = freeman_ids
        p.context = context
        preliminary[p.id] = p.decide(assignments[p.id])
    # 收齐所有人之后才开放给协调层；只读映射防止协调函数意外污染初稿。
    snapshot = MappingProxyType(preliminary)
    final = coordinate(context, MappingProxyType(dict(assignments)), snapshot)
    # 漏掉球员可能让旧动作残留；多出编号则说明协作层引用了错误对象。
    if set(final) != set(preliminary):
        raise ValueError('Coordination must return exactly one intent per player')
    # 可视化目标以最终意图为准；协调函数如果替换行为，也应注意调试标签的含义。
    for p in players:
        intent = final[p.id]
        if isinstance(intent, MoveIntent):
            p.tactical_target = intent.target
        elif isinstance(intent, KickIntent):
            p.tactical_target = resolve_target(context, intent.target)
        elif isinstance(intent, (StopIntent, RecoverIntent)):
            p.tactical_target = None
    store.preliminary_intents = snapshot
    store.intents = MappingProxyType(dict(final))
    return final
