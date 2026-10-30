from __future__ import annotations
"""全队分工层：根据比赛阶段和可行动状态，为每名球员产生 RoleTask。

本层维护阶段与角色的跨帧记忆，只决定谁负责什么，不调用 attack/guard，
也不计算具体站位。截球评分由运行时提前计算，本层只比较同一帧的结果。
所有球员都必须出现在返回值中，包括罚下、倒地和没有定位的球员。
"""
import math
from enum import Enum
from .framework.types import KICKING_TEAM_NONE, Context, GameState, SetPlay
from .intents import Role, RoleTask
from .param import ATTACKER_KEEP_TIME_MARGIN_S, GUARD_PLAYER_ID
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .player import Player

class Phase(Enum):
    """比赛阶段。顶层状态机,决定当前是正常拼抢/开球/定位球/准备/停止。"""
    NORMAL = "normal"              # PLAYING 正常拼抢
    OUR_KICKOFF = "our_kickoff"    # 我方开球（PLAYING 开球窗口；SET 仍停止）
    OPP_KICKOFF = "opp_kickoff"    # 对方开球(避让)
    OUR_SET_PLAY = "our_set_play"  # 我方定位球(任意球/角球/球门球)
    OPP_SET_PLAY = "opp_set_play"  # 对方定位球(避让)
    READY = "ready"                # READY 走位
    STOPPED = "stopped"            # SET(非开球重开) / INITIAL / FINISHED / stopped

def get_phase(context: Context) -> Phase:
    """根据裁判机状态判断当前比赛阶段。"""
    g = context.game
    if g is None:
        return Phase.STOPPED

    state = g.state

    # READY:走 ready 位
    if state == GameState.READY:
        return Phase.READY

    # PLAYING:正常拼抢 or 开球/定位球执行中
    if state == GameState.PLAYING and not g.stopped:
        # 定位球:set_play != NONE,kicking_team 指示哪方
        if g.set_play != SetPlay.NONE and g.kicking_team != KICKING_TEAM_NONE:
            our_team = context.team_id
            if g.kicking_team == our_team:
                return Phase.OUR_SET_PLAY
            else:
                return Phase.OPP_SET_PLAY

        # 开球:secondary_time > 0(倒计时窗口),kicking_team 指示哪方
        if g.secondary_time > 0 and g.kicking_team != KICKING_TEAM_NONE:
            our_team = context.team_id
            if g.kicking_team == our_team:
                return Phase.OUR_KICKOFF
            else:
                return Phase.OPP_KICKOFF

        # 正常拼抢
        return Phase.NORMAL

    # SET / INITIAL / FINISHED / stopped:站定
    return Phase.STOPPED

def get_set_play_type(context: Context) -> SetPlay:
    """当前生效的定位球类型;无定位球(或无裁判机数据)时返回 ``SetPlay.NONE``。

    直接读裁判机的 ``set_play`` 字段,不区分是哪方主罚 —— 哪方由 :func:`get_phase`
    (OUR_SET_PLAY / OPP_SET_PLAY)判定。这里只回答"是什么类型的定位球"。

    共 7 种可能返回值(见 framework.types.SetPlay):
    - ``NONE``:无定位球(正常比赛/开球等)
    - ``DIRECT_FREE_KICK``:直接任意球(可直接射门得分)
    - ``INDIRECT_FREE_KICK``:间接任意球(须先触碰他人才能进球)
    - ``PENALTY_KICK``:点球
    - ``THROW_IN``:界外球(踢入)
    - ``GOAL_KICK``:球门球
    - ``CORNER_KICK``:角球
    """
    g = context.game
    if g is None:
        return SetPlay.NONE
    return g.set_play

def _clear_normal_sticky(store) -> None:
    # 离开正常阶段清除进攻者保持记录。
    store.normal_attacker = None
    store.normal_attack_decision = None

def _select_future_attacker(
    context: Context,
    players: list[Player],
    preferred_id: int | None = None,
    decision: dict | None = None,
) -> Player:
    """选择预计最早拿到球的球员，时间优势不足阈值时保留当前 attacker。

    ``players`` 非空、已就绪、pose 已知。无球时优先保持角色。
    """
    # 评分是排序依据，不是截球成功保证；无有效预测用 inf，避免把未知当成最快。
    # 全员未知时优先保持旧角色；没有旧角色时按编号打破同分，结果可复现。
    ranked = [(p, p.ball_intercept['score_s']
               if p.ball_intercept and p.ball_intercept['score_s'] is not None
               else math.inf) for p in players]
    best, best_time = min(ranked, key=lambda item: (item[1], item[0].id))
    preferred = next((item for item in ranked if item[0].id == preferred_id), None)
    # 小幅领先不足以触发交接，避免球位噪声导致两人轮流抢球、反复转身。
    keep = (
        preferred is not None
        and (preferred[1] == best_time
             or preferred[1] < best_time + ATTACKER_KEEP_TIME_MARGIN_S)
    )
    selected = preferred[0] if keep else best
    if decision is not None:
        decision.update(
            selected_id=selected.id, best_id=best.id, previous_id=preferred_id,
            margin_s=ATTACKER_KEEP_TIME_MARGIN_S,
            reason=('no_estimate' if not math.isfinite(best_time) else
                    'hysteresis' if keep and selected.id != best.id else 'fastest'),
            candidates=[dict(id=p.id, score_s=t if math.isfinite(t) else None) for p, t in ranked],
        )
    return selected

def init_store(store):
    """初始化全队跨帧记忆；个体行为记忆留在 Player，执行状态留在控制器。"""
    store.prev_phase = None
    store.cur_phase = None
    store.kickoff_taker = None
    store.normal_attacker = None
    store.normal_attack_decision = None

def _assign_normal(context, players, store, result):
    """2 号固定防守；其余可行动球员按截球时间分配进攻者和自由人。

    防守球员不可行动时不由队友接替，保留入口分配的不可用任务。
    """
    store.normal_attack_decision = None
    guard = next((p for p in players if p.id == GUARD_PLAYER_ID), None)
    if guard is not None:
        result[guard.id] = RoleTask(Role.GUARD, pass_to=3)
    outfield = [p for p in players if p.id != GUARD_PLAYER_ID]
    decision = {}
    attacker = (_select_future_attacker(context, outfield,
                getattr(store, 'normal_attacker', None), decision) if outfield else None)
    store.normal_attack_decision = decision or None
    store.normal_attacker = attacker.id if attacker is not None else None
    for p in outfield:
        result[p.id] = RoleTask(Role.ATTACKER if p is attacker else Role.FREEMAN)

def assign(context, players, store):
    """统一角色分配入口；调用前须已完成 prepare_frame 的观测更新。

    返回 id → RoleTask，覆盖全部输入球员；此处读取就绪状态但不负责起身。
    起身、切模式和停车属于执行阶段，保证分配阶段没有机器人控制副作用。
    """
    phase = get_phase(context)
    store.prev_phase = getattr(store, 'cur_phase', None)
    store.cur_phase = phase
    result = {}
    active = []
    # 先为每人设保底任务，再覆盖可行动球员，避免无人分配时沿用旧动作。
    # 状态判断有优先级：罚下 > 倒地 > 模式未就绪 > 无定位。
    for p in players:
        reason = ('penalized' if p.is_penalized else 'fallen' if p.is_fallen else
                  'switching_mode' if p.mode != 'walk' else 'no_pose' if p.pose is None else '')
        result[p.id] = RoleTask(Role.UNAVAILABLE, reason=reason) if reason else RoleTask(Role.STAY)
        if not reason:
            active.append(p)
    if phase != Phase.NORMAL:
        _clear_normal_sticky(store)
    # 我方定位球暂时沿用正常分工；敌方定位球除门将外均为自由人。
    # 这不代表完整规则约束已经实现；例如守门行为还会单独检查主动触球权限。
    if phase in (Phase.NORMAL, Phase.OUR_SET_PLAY):
        _assign_normal(context, active, store, result)
    elif phase == Phase.OPP_SET_PLAY:
        # 固定 2 号防守，其余可行动球员为自由人。
        _assign_normal(context, active, store, result)
        for p in active:
            if result[p.id].role != Role.GUARD:
                result[p.id] = RoleTask(Role.FREEMAN)
        _clear_normal_sticky(store)
    elif phase == Phase.OUR_KICKOFF:
        # 1 号担任 attacker，2 号守门；踢球目标由 Player 选择，不可行动时不自动换人。
        store.kickoff_taker = 1
        for p in active:
            result[p.id] = (RoleTask(Role.ATTACKER) if p.id == 1 else
                            RoleTask(Role.GUARD, pass_to=3) if p.id == GUARD_PLAYER_ID else RoleTask(Role.STAY))
    elif phase == Phase.OPP_KICKOFF and active:
        for p in active:
            if p.id == GUARD_PLAYER_ID:
                result[p.id] = RoleTask(Role.GUARD, pass_to=3)
        for slot, p in enumerate(p for p in active if p.id != GUARD_PLAYER_ID):
            if slot < 2:
                result[p.id] = RoleTask(Role.OPP_KICKOFF, slot=slot)
    elif phase == Phase.READY:
        ours = context.game is not None and context.game.kicking_team == context.team_id
        # 只分配站位序号。站位坐标、面向和避障要求都由 player 决定。
        # 按编号固定站位；不可行动球员不能使后续队友的 slot 前移。
        for p in active:
            if 1 <= p.id <= 3:
                result[p.id] = RoleTask(Role.READY, slot=p.id - 1, our_kickoff=ours)
    elif phase == Phase.STOPPED:
        for p in active:
            result[p.id] = RoleTask(Role.STAY, reason='stopped')
    return result
