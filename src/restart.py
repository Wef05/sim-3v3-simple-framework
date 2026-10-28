"""我方定位球协作；使用框架已有的己方球门在 -x 的场地坐标。"""
from dataclasses import dataclass
import math

from .framework.types import SetPlay
from .param import (
    RESTART_WAIT_S, RESTART_MOVED_M,
    RESTART_READY_M, RESTART_LANE_M, RESTART_MARGIN_M,
    RESTART_POWER_PER_M, RESTART_POWER_MAX,
)


@dataclass
class RestartPlan:
    key: str
    taker: int
    receiver: int | None
    target: tuple[float, float]
    origin: tuple[float, float]
    started: float
    stage: str = "prepare"
    sent_at: float | None = None


def distance(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def xy(player):
    return player.pose.x, player.pose.y


def lane_clear(ctx, target, taker, receiver):
    """检查整个传球线段及落点；队友也不能挡在传球线上。"""
    ball = (ctx.ball.x, ctx.ball.y)
    dx, dy = target[0] - ball[0], target[1] - ball[1]
    length2 = dx * dx + dy * dy
    if length2 < 0.25:
        return False
    obstacles = list(ctx.opponents.values()) + [
        r for pid, r in ctx.teammates.items() if pid not in (taker, receiver)
    ]
    for robot in obstacles:
        if robot.pose is None:
            continue
        px, py = robot.pose.x, robot.pose.y
        t = max(0, min(1, ((px-ball[0])*dx + (py-ball[1])*dy) / length2))
        if distance((px, py), (ball[0]+t*dx, ball[1]+t*dy)) < RESTART_LANE_M:
            return False
    return True


def candidates(ctx, key):
    bx, by = ctx.ball.x, ctx.ball.y
    inward = -1 if by > 0 else 1
    if key == SetPlay.CORNER_KICK.value:
        offsets = [(-2, inward*2), (-3, inward*1.5), (-1.5, inward*3)]
    elif key == SetPlay.GOAL_KICK.value:
        offsets = [(3, 2), (3, -2), (2, 0), (1.5, inward*2)]
    elif key == SetPlay.THROW_IN.value:
        offsets = [(1, inward*2), (-1.5, inward*2), (2, inward*1.5)]
    elif key == "kickoff":
        offsets = [(0, 2), (0, -2), (-1, 2), (-1, -2)]
    else:
        offsets = [(2, 1.5), (2, -1.5), (0, 2), (0, -2), (-1.5, inward*2)]
    xmax = ctx.field.length/2 - RESTART_MARGIN_M
    ymax = ctx.field.width/2 - RESTART_MARGIN_M
    for dx, dy in offsets:
        target = (max(-xmax, min(xmax, bx+dx)), max(-ymax, min(ymax, by+dy)))
        if distance((bx, by), target) >= 1:
            yield target


def choose_target(ctx, key, taker, receiver):
    choices = [t for t in candidates(ctx, key)
               if lane_clear(ctx, t, taker.id, receiver.id if receiver else None)]
    if not choices:
        return None
    # 优先队友能较快到达、对手远离落点的空当。
    def score(target):
        clearance = min((distance(target, (r.pose.x, r.pose.y))
                         for r in ctx.opponents.values() if r.pose), default=5)
        return (distance(xy(receiver), target) if receiver else 0) - 0.4*min(clearance, 5)
    return min(choices, key=score)


def act_restart(ctx, players, store, key):
    """players 只含已就绪场上球员；角色在同一次定位球内保持稳定。"""
    if not players:
        store.restart_plan = None
        store.kickoff_taker = None
        return
    if ctx.ball is None:
        for p in players:
            p.action = "restart:no_ball"
            p.stop()
        return
    ball = (ctx.ball.x, ctx.ball.y)
    plan = getattr(store, "restart_plan", None)
    ids = {p.id for p in players}
    if (plan is None or plan.key != key or plan.taker not in ids
            or (plan.receiver is not None and plan.receiver not in ids)):
        for p in players:
            p.stop()  # 取消上一方案可能仍在执行的踢球命令。
        taker = min(players, key=lambda p: (distance(xy(p), ball), p.id))
        receivers = [p for p in players if p.id != taker.id]
        receiver = min(receivers, key=lambda p: (distance(xy(p), ball), p.id)) if receivers else None
        target = choose_target(ctx, key, taker, receiver)
        plan = RestartPlan(key, taker.id, receiver.id if receiver else None,
                           target or ball, ball, ctx.now)
        store.restart_plan = plan
    store.kickoff_taker = plan.taker
    taker = next(p for p in players if p.id == plan.taker)
    receiver = next((p for p in players if p.id == plan.receiver), None)
    for p in players:
        if p.id not in (plan.taker, plan.receiver):
            p.stop()

    if distance(ball, plan.origin) >= RESTART_MOVED_M:
        plan.stage = "receive"
        if plan.sent_at is None:
            plan.sent_at = ctx.now
    if plan.stage == "receive":
        taker.action = "restart:release"
        taker.stop()
        if receiver:
            receiver.action = "restart:receive"
            receiver.attack()
        # 不重复让原主罚者触球；裁判窗口结束后恢复普通进攻。
        return

    if key == SetPlay.PENALTY_KICK.value:
        if receiver:
            receiver.stop()
        taker.action = "restart:penalty"
        taker.attack()
        return

    # 未出球且路线被堵时重新找点，不重置本次定位球的等待时钟。
    if not lane_clear(ctx, plan.target, plan.taker, plan.receiver):
        target = choose_target(ctx, key, taker, receiver)
        if target is not None:
            plan.target = target
    if receiver:
        receiver.action = "restart:run"
        receiver.walk_to(plan.target, face=math.atan2(
            ball[1]-receiver.pose.y, ball[0]-receiver.pose.x),
            avoid_ball=True, avoid_robots=True)

    ready = receiver is not None and distance(xy(receiver), plan.target) <= RESTART_READY_M
    timed_out = ctx.now - plan.started >= RESTART_WAIT_S
    clear = lane_clear(ctx, plan.target, plan.taker, plan.receiver)
    if clear and (ready or timed_out):
        taker.action = "restart:pass" if ready else "restart:fallback"
        power = min(RESTART_POWER_MAX, distance(ball, plan.target)*RESTART_POWER_PER_M)
        if taker.pass_to(plan.target, power):
            plan.stage = "kick"
            if plan.sent_at is None:
                plan.sent_at = ctx.now
    else:
        taker.action = "restart:prepare" if clear else "restart:blocked"
        # 停在球后，等待期间不进入自动踢球控制。
        angle = math.atan2(plan.target[1]-ball[1], plan.target[0]-ball[0])
        taker.walk_to((ball[0]-0.8*math.cos(angle), ball[1]-0.8*math.sin(angle)),
                      face=angle, avoid_ball=True, avoid_robots=True)
