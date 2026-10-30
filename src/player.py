"""角色行为层：决定去哪、朝向哪、是否踢球和踢向什么对象。

模块级 decide 是 main 唯一调用的整队入口；Player.decide 是内部单人分派。
决策函数可以更新行为记忆和调试标签，但只能返回意图，不能执行底盘命令。
共享观测与截球结果由 prepare_frame 准备；先完成所有个人决策，再统一协调。
"""
from __future__ import annotations
import math
from .utils.interception_risk import estimate_interception_risk
from .utils.kick_lane import check_kick_lane
from .utils.scoring_probability import evaluate_scoring
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
                         opponent_goal, behind_ball)
from .utils.free_position import nearest_free_position
from .utils.ball_motion import BallMotion

class Player:
    """跨帧存活的角色行为对象，通过组合持有执行控制器。

    _guard_threatened 属于战术迟滞：决定门将继续封堵还是回中。
    is_kicking 属于执行状态：由控制器维护，行为层只读取它来选择距离阈值。
    不把两种状态合并，避免「想踢球」被误认为「已经下发踢球」。
    """
    def __init__(self, player_id, config, _backend):
        self.controller = ActionController(player_id, config, _backend)
        # 构造时提供空估计器；正式逐帧运行时会替换成全队共享实例。
        self.motion = BallMotion()
        self.ball_intercept = None
        self.action = 'init'
        self.tactical_target = None
        self.freeman_region = None
        self._guard_threatened = False

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
            slots = [
                (-field.circle_radius if task.our_kickoff else -field.circle_radius - .5, 0.0),
                (-field.length / 2 + field.goal_area_length, 0.0),
                (-.5, field.circle_radius + 2) if task.our_kickoff else
                (-field.length / 2 + field.penalty_area_length, 0.0),
            ]
            return MoveIntent(slots[task.slot], face=0.0, avoid_ball=True, avoid_robots=True)
        if task.role == Role.OPP_KICKOFF:
            r = self.context.field.circle_radius
            self.action = 'opp_kickoff:ready'
            return MoveIntent([(-r-.5, 0.0), (-r-2, .5)][task.slot],
                              avoid_ball=True, avoid_robots=True)
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
        球位。到该预测位置后方 ``CHASE_BEHIND_M`` 处准备踢球，走位目标限制在场内。
        踢球仍须观测球位和补偿延迟后的当前球位均在近距阈值内，不能对未来球位踢空。
        """
        self.tactical_target = None
        ball = self.context.ball if self.context is not None else None
        current_ball = self.motion.predict_ball_position(0.0)
        if ball is None or self.pose is None or current_ball is None:
            return StopIntent()

        # 观测评估先于既有目标解析，结果不传入任何动作选择分支。
        cached = getattr(self, '_candidate_cache', None)
        signature = (tuple(getattr(self, 'evaluation_freeman_ids', ())), tuple((pid, r.pose is not None) for pid,r in sorted(self.context.teammates.items())),
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
                                                   getattr(self,'evaluation_freeman_ids',()))
            self._candidate_cache, self._candidate_signature = cached, signature
        self.attack_candidates = cached
        self.scoring_probability = self._scoring_cache

        aim = (KickTarget.goal() if kick_target == 0 else
               KickTarget.position(kick_target) if isinstance(kick_target, tuple) else
               KickTarget.teammate(kick_target))
        # 追球需要知道出球方向，因而也使用相同的目标解析工具来选球后点。
        # 这里只使用几何目标；真正的踢球力度、坐标变换仍留给执行阶段。
        target = resolve_target(self.context, aim)
        if target is None:
            return StopIntent()

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
            # 实际出脚按当前球位检查，不能用未来截球点掩盖眼前球网/门柱。
            lane = check_kick_lane(self.context.field, current_ball, target)
            if lane['blocked']:
                self.action = 'attack:blocked:' + lane['reason']
                return StopIntent()
            return KickIntent(aim)
        else:
            # 使用本帧与角色分配共享的截球结果。
            if self.ball_intercept is None or self.ball_intercept['status'] == 'unavailable':
                return StopIntent()
            predicted_ball = self.ball_intercept['target']
            chase = behind_ball(*predicted_ball, target, CHASE_BEHIND_M)
            field = self.context.field
            # 球轨迹允许出界，但不能让预测点直接把机器人带出场地。
            chase = (
                clamp(chase[0], -field.length / 2 + ARRIVE_DIST, field.length / 2 - ARRIVE_DIST),
                clamp(chase[1], -field.width / 2 + ARRIVE_DIST, field.width / 2 - ARRIVE_DIST),
            )
            return MoveIntent(chase)

    def _guard_pass_target(self, teammate_id: int | None):
        """选择可向前短传的指定队友；通道或接球点被占据时放弃。"""
        ctx = self.context
        if ctx is None or ctx.ball is None or teammate_id is None or teammate_id == self.id:
            return None
        receiver = ctx.teammates.get(teammate_id)
        if receiver is None or receiver.pose is None:
            return None
        if ctx.game is not None:
            state = ctx.game.get_player_state(ctx.team_id, teammate_id)
            if state is not None and state.penalty != Penalty.NONE:
                return None
        ball, pose = ctx.ball, receiver.pose
        dx, dy = pose.x - ball.x, pose.y - ball.y
        length2 = dx * dx + dy * dy
        if (
            dx < GUARD_PASS_MIN_FORWARD_M
            or length2 > GUARD_PASS_MAX_DIST_M ** 2
            or abs(pose.x) >= ctx.field.length / 2.0
            or abs(pose.y) >= ctx.field.width / 2.0
        ):
            return None

        # 点到球→队友线段的距离，同时覆盖传球线路和接球点附近的防守者。
        blockers = [(r, OPPONENT_RADIUS) for r in ctx.opponents.values()]
        blockers.extend(
            (r, TEAMMATE_RADIUS) for pid, r in ctx.teammates.items()
            if pid not in (self.id, teammate_id)
        )
        for robot, radius in blockers:
            if robot.pose is None:
                continue
            rx, ry = robot.pose.x - ball.x, robot.pose.y - ball.y
            t = clamp((rx * dx + ry * dy) / length2, 0.0, 1.0)
            if math.hypot(rx - t * dx, ry - t * dy) <= radius + GUARD_PASS_LANE_MARGIN_M:
                return None
        return pose.x, pose.y

    def guard(self, pass_to: int | None = None):
        """每帧执行一次守门决策：门前出球优先，其次封堵，最后回中待命。

        参数：
            pass_to: 希望优先传给的队友编号，例如 guard(3) 表示寻找传给
                3 号的机会；None 表示不考虑传球，门前球直接向前场解围。
                传入编号并不强制传球，仍须通过 _guard_pass_target 的检查。

        坐标约定：
            原点为球场中心，+x 朝对方球门，-x 朝己方球门，y 为横向坐标。
            默认 14 米场地中，己方门线 x=-7，小禁区中央 home=(-6.5, 0)。
            球 x<=-1 表示进入己方半场至少 1 米，并非距己方门线 1 米。

        执行顺序：
            1. 缺少自身定位时停止；没有球观测时回小禁区中央。
            2. 根据球的位置及跨帧迟滞状态，计算横向封堵目标。
            3. 比赛允许主动触球且球在门前近处时，优先接近并传球/解围；
               此分支返回出球意图，避免被后面的回位决策覆盖。
            4. 不需要出球时，执行先前算好的封堵或回中移动。

        本方法只根据当前球位计算几何封堵线，不预测球速或射门落点。
        action 用于显示当前动作；威胁记忆保留在角色层，踢球状态由执行层管理。
        """
        # 1. 安全入口：没有上下文或自身位置就无法计算移动和出球方向。
        # 停止意图由执行层取消踢球并下发零速度。
        ctx, pose = self.context, self.pose
        if ctx is None or pose is None:
            self._guard_threatened = False
            self.action = "guard:stop"
            return StopIntent()

        # 默认先选择小禁区中央；下面只在需要封堵或出球时覆盖 target。
        home = own_goal_area_center(ctx)
        ball = ctx.ball
        goal_x, _ = own_goal(ctx)
        target = home
        can_clear = False
        self.action = "guard:home"
        if ball is None:
            # 丢球后清除威胁记忆；本帧仍会走到末尾执行回中、面向前场。
            self._guard_threatened = False
        else:
            # 2. 威胁状态采用两个阈值，避免球在边界附近时反复回中/封堵：
            # 未进入封堵：球 x<=-1.0 才进入；已在封堵：直到 x>-0.7 才退出。
            # 这只是球所处区域的判断，不表示已检测到对方射门。
            threshold = (
                GUARD_THREAT_EXIT_X if self._guard_threatened
                else GUARD_THREAT_ENTER_X
            )
            self._guard_threatened = ball.x <= threshold
            if self._guard_threatened:
                # 固定纵向站位 x=home.x，只调整横向 y。
                # 球在站位线前方时，利用相似三角形求“球→己方门中心”
                # 与 x=home.x 的交点：
                # target_y = ball.y * (home.x-goal_x) / (ball.x-goal_x)。
                # 球已到站位线或其后方时直接取 ball.y，避免反向投影或除零。
                y = ball.y
                if ball.x > home[0]:
                    y *= (home[0] - goal_x) / (ball.x - goal_x)
                # 将横向目标限制在门柱内侧；默认门宽 2.6 米，留 0.25 米
                # 余量后 y 限于 [-1.05, 1.05]。这是目标点约束，不是路径约束。
                limit = max(0.0, ctx.field.goal_width / 2.0 - GUARD_POST_MARGIN_M)
                target = (home[0], clamp(y, -limit, limit))
                self.action = "guard:block"

            # 3. 判断本帧是否允许主动处理球：必须有裁判数据、处于 PLAYING
            # 且比赛未暂停；对方定位球/开球倒计时期间不主动接近出球。
            # can_clear 也供末尾移动使用，决定是否把球当作需要避让的障碍。
            game = ctx.game
            can_clear = (
                game is not None and game.state == GameState.PLAYING
                and not game.stopped
                and not (
                    game.kicking_team != ctx.team_id
                    and (game.set_play != SetPlay.NONE or game.secondary_time > 0)
                )
            )
            # 出球范围同时要求球在门前矩形内、守门员也离球足够近。
            # 默认场地：x∈[-7, -5.4]、|y|<=2，并且人球距离<=1.8 米。
            # 球越过门线后不在此范围，守门员不会为了追球继续进入球门深处。
            near_goal = (
                goal_x <= ball.x <= goal_x + ctx.field.goal_area_length + GUARD_CLEAR_EXTRA_M
                and abs(ball.y) <= ctx.field.goal_area_width / 2.0
                and dist(pose.x, pose.y, ball.x, ball.y) <= GUARD_CLEAR_REACH_M
            )
            if can_clear and near_goal:
                # 先准备默认解围方案：踢向前场中心，默认目标为 (3.5, 0)。
                # 仅在此门前处理分支考虑传球，不会为接应队友离开守门区域。
                clear_target = (ctx.field.length / 4.0, 0.0)
                # helper 检查队友位置/罚下状态、是否在球前方至少 0.75 米、
                # 距球是否在 6 米以内，以及传球线段和接球点附近有无机器人。
                # None、自己编号或任一检查不通过，都保留上面的解围方案。
                pass_target = self._guard_pass_target(pass_to)
                aim = KickTarget.position(clear_target)
                style = 'clear'
                if pass_target is not None:
                    clear_target = pass_target
                    aim = KickTarget.teammate(pass_to)
                    style = 'pass'
                kick_dir = angle_to(ball.x, ball.y, *clear_target)
                # 守门独立使用近距离踢球阈值，不复用 attack 的 2 米阈值。
                # 初次<=0.65 米进入；已在踢球时允许保持到 0.85 米，减少抖动。
                kick_dist = GUARD_KICK_EXIT_M if self.is_kicking else GUARD_KICK_ENTER_M
                if dist(pose.x, pose.y, ball.x, ball.y) <= kick_dist:
                    self.action = f"guard:pass:{pass_to}" if pass_target is not None else "guard:clear_kick"
                    # 首次启动前停止普通行走，随后由踢球控制器接管底盘；
                    # 已在踢球时 backend 会忽略普通速度命令，保持踢球独占。
                    return KickIntent(aim, style=style)
                # 尚未到踢球距离：先走向“球→出球目标”反方向 0.35 米处，
                # 让球尽量位于自己与目标之间，面向预定出球方向接近。
                stage = behind_ball(ball.x, ball.y, clear_target, CHASE_BEHIND_M)
                # 球后点可能落入球门或偏出小禁区侧面，因此限制接近目标：
                # x 至少在门线前 0.25 米，y 不超过小禁区左右边界。
                target = (
                    max(goal_x + GUARD_GOAL_LINE_MARGIN_M, stage[0]),
                    clamp(stage[1], -ctx.field.goal_area_width / 2.0,
                          ctx.field.goal_area_width / 2.0),
                )
                self.action = f"guard:pass_approach:{pass_to}" if pass_target is not None else "guard:clear_approach"
                # 触球是本分支的目的，所以不避球；仍避开机器人及球门结构。
                # maintain_face=True 使远距离也全向移动并保持出球朝向。
                # walk_to() 会先取消上一帧踢球；本帧发出接近指令后直接返回。
                return MoveIntent(target, face=kick_dir, avoid_ball=False,
                             avoid_robots=True, maintain_face=True)

        # 4. 未进入门前出球分支：执行封堵或回中，默认面向球；
        # 球不可见或关闭 GUARD_FACE_BALL 时面向 +x（角度 0，即前场）。
        face = 0.0
        if GUARD_FACE_BALL and ball is not None:
            face = angle_to(
                pose.x, pose.y, ball.x, ball.y,
            )

        # 只有“存在威胁且允许主动触球”时才关闭避球；其余情况保持避球。
        # 始终避机器人并保持指定朝向。walk_to() 会取消残留踢球状态，
        # 因此球离开处理范围后会自然结束传球/解围并恢复站位。
        return MoveIntent(target, face=face,
                     avoid_ball=ball is None or not self._guard_threatened or not can_clear,
                     avoid_robots=True, maintain_face=True)

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
        每帧重算；无可行点或观测不足时停止，不主动踢球。
        """
        ctx, pose = self.context, self.pose
        self.freeman_region = dict(ball_radius=ball_radius, opponent_radius=opponent_radius,
                                   goal_line_margin=FREEMAN_GOAL_LINE_MARGIN_M)
        target = None if ctx is None or pose is None else nearest_free_position(
            ctx, pose, ball_radius, opponent_radius, FREEMAN_GOAL_LINE_MARGIN_M,
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
    """整队协调扩展点：输入完整初步意图快照，返回最终意图表。

    当前直接透传以保留既有战术，并未实现传球接应或冲突消解。
    后续例如看到 1 号传给 3 号，可同时替换 1 号的出球目标和 3 号的接应点。
    需要两人锁定同一落点时，可用 KickTarget.position 与 MoveIntent 共享坐标。

    使用 dict(preliminary) 创建结果，再替换条目，不要修改输入对象。
    所有调整都基于同一批初步意图，不边改结果边让后面的队友读取；若有多个
    候选配合，需制定统一优先级并一次解决冲突，避免按遍历顺序抢占任务。
    这里不能下发命令，也不能遗漏或增加球员编号；最终统一交给 actions。
    """
    return dict(preliminary)

def decide(context, players, assignments, store):
    """main 调用的整队唯一入口：收集初稿 → 协调 → 保存并返回最终意图。

    prepare_frame 已为所有球员绑定同一帧观测；本阶段不推进球运动估计。
    store 中分别保留初步和最终意图，便于检查协作阶段究竟改变了什么。
    不可变性覆盖当前这些标量/元组字段；未来增加可变嵌套字段时也需冻结它们。
    """
    preliminary = {}
    freeman_ids = tuple(pid for pid, task in assignments.items() if task.role == Role.FREEMAN)
    for p in players:
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
