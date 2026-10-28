"""正常比赛的双人配合：追球、落位、传球、射门。坐标进攻方向为 +x。"""
from dataclasses import dataclass
import math

from .param import (
    ATTACKER_KEEP_DIST_MARGIN_M, CHASE_BEHIND_M, RESTART_POWER,
    COOP_HOLD_S, COOP_SWITCH_M, COOP_PASS_WAIT_S, COOP_RECEIVE_S,
    COOP_MOVED_M, COOP_CONTROL_M, COOP_SHOT_RANGE_M, COOP_LANE_M,
    COOP_PASS_MIN_M, COOP_PASS_MAX_M, COOP_MARGIN_M,
)
from .restart import distance, xy


@dataclass
class PassPlan:
    passer: int
    receiver: int
    target: tuple
    origin: tuple
    started: float
    moved_at: float | None = None


def clear_lane(ctx, origin, target, excluded=()):
    dx, dy = target[0]-origin[0], target[1]-origin[1]
    length2 = dx*dx+dy*dy
    if length2 < 0.01:
        return False
    robots = list(ctx.opponents.values()) + [
        r for pid, r in ctx.teammates.items() if pid not in excluded]
    for r in robots:
        if r.pose is None:
            continue
        t = max(0, min(1, ((r.pose.x-origin[0])*dx+(r.pose.y-origin[1])*dy)/length2))
        if distance((r.pose.x, r.pose.y), (origin[0]+t*dx, origin[1]+t*dy)) < COOP_LANE_M:
            return False
    return True


def support_target(ctx, attacker, supporter):
    ball = (ctx.ball.x, ctx.ball.y)
    xmax, ymax = ctx.field.length/2-COOP_MARGIN_M, ctx.field.width/2-COOP_MARGIN_M
    options = []
    for dx in (1.5, 0, -1.5):
        for dy in (-2.0, 2.0):
            target = (max(-xmax, min(xmax, ball[0]+dx)),
                      max(-ymax, min(ymax, ball[1]+dy)))
            if distance(ball, target) < COOP_PASS_MIN_M:
                continue
            space = min((distance(target, (r.pose.x, r.pose.y))
                         for r in ctx.opponents.values() if r.pose), default=4)
            score = (3*clear_lane(ctx, ball, target, (attacker.id, supporter.id))
                     + min(space, 3) + 0.4*dx - 0.25*distance(xy(supporter), target))
            options.append((score, target))
    return max(options)[1] if options else xy(supporter)


def chase(player, ball, aim):
    """只移动；不因进入 SDK 的大范围自动追球半径而盲目射门。"""
    angle = math.atan2(aim[1]-ball[1], aim[0]-ball[0])
    player.action = "move:chase"
    player.walk_to((ball[0]-CHASE_BEHIND_M*math.cos(angle),
                    ball[1]-CHASE_BEHIND_M*math.sin(angle)),
                   face=angle, avoid_robots=True)


def act_cooperative(ctx, players, store):
    if not players or ctx.ball is None:
        store.attack_pass = None
        store.normal_attacker = None
        for p in players:
            p.action = "move:no_ball"
            p.stop()
        return
    ball = (ctx.ball.x, ctx.ball.y)
    by_id = {p.id: p for p in players}
    nearest = min(players, key=lambda p: (distance(xy(p), ball), p.id))
    ours = distance(xy(nearest), ball)
    theirs = min((distance((r.pose.x, r.pose.y), ball)
                  for r in ctx.opponents.values() if r.pose), default=math.inf)
    lost = theirs < COOP_CONTROL_M and theirs + 0.3 < ours
    plan = getattr(store, "attack_pass", None)
    if plan:
        if plan.passer not in by_id or plan.receiver not in by_id or lost:
            store.attack_pass = plan = None
        elif plan.moved_at is None:
            dx, dy = ball[0]-plan.origin[0], ball[1]-plan.origin[1]
            if math.hypot(dx, dy) >= COOP_MOVED_M:
                # 移动方向也必须指向接应点；被踢偏/抢断后重新选追球人。
                if dx*(plan.target[0]-plan.origin[0])+dy*(plan.target[1]-plan.origin[1]) > 0:
                    plan.moved_at = ctx.now
                    store.normal_attacker = plan.receiver
                    store.attacker_since = ctx.now
                else:
                    store.attack_pass = plan = None
            elif ctx.now-plan.started > COOP_PASS_WAIT_S or not clear_lane(
                    ctx, ball, plan.target, (plan.passer, plan.receiver)):
                store.attack_pass = plan = None
        if plan is None:
            store.pass_cooldown_until = ctx.now + COOP_HOLD_S
        if plan and plan.moved_at is not None:
            receiver = by_id[plan.receiver]
            if (ctx.now-plan.moved_at > COOP_RECEIVE_S
                    or distance(xy(receiver), ball) <= COOP_CONTROL_M
                    or ours + COOP_SWITCH_M < distance(xy(receiver), ball)):
                store.attack_pass = plan = None
                store.pass_cooldown_until = ctx.now + COOP_HOLD_S
        if plan:
            passer, receiver = by_id[plan.passer], by_id[plan.receiver]
            if plan.moved_at is None:
                receiver.action = "move:position"
                receiver.move_to_position(plan.target)
                passer.action = "kick:pass"
                passer.pass_to(plan.target, RESTART_POWER)
            else:
                passer.action = "move:position"
                passer.move_to_position(support_target(ctx, receiver, passer))
                chase(receiver, ball, (ctx.field.length/2, 0))
            return

    preferred = by_id.get(getattr(store, "normal_attacker", None))
    attacker = nearest
    if preferred and not lost:
        margin = COOP_SWITCH_M if ctx.now-getattr(store, "attacker_since", -math.inf) < COOP_HOLD_S else ATTACKER_KEEP_DIST_MARGIN_M
        if distance(xy(preferred), ball) <= ours+margin:
            attacker = preferred
    if attacker.id != getattr(store, "normal_attacker", None):
        store.attacker_since = ctx.now
    store.normal_attacker = attacker.id
    supporters = [p for p in players if p is not attacker]
    if lost:
        chase(attacker, ball, (ctx.field.length/2, 0))
        for p in supporters:
            p.action = "move:defend"
            p.support()
        return

    shot = attacker.shot_target()
    if shot is not None and distance(ball, shot) <= COOP_SHOT_RANGE_M and clear_lane(ctx, ball, shot, (attacker.id,)):
        attacker.action = "kick:shoot"
        attacker.attack(shot)
    else:
        receiver = supporters[0] if supporters else None
        target = xy(receiver) if receiver else None
        pressure = theirs < 1.8
        can_pass = (target is not None
                    and COOP_PASS_MIN_M <= distance(ball, target) <= COOP_PASS_MAX_M
                    and abs(target[0]) <= ctx.field.length/2-COOP_MARGIN_M
                    and abs(target[1]) <= ctx.field.width/2-COOP_MARGIN_M
                    and (target[0] > ball[0]+0.5 or (pressure and target[0] >= ball[0]-1.5))
                    and ctx.now >= getattr(store, "pass_cooldown_until", -math.inf)
                    and clear_lane(ctx, ball, target, (attacker.id, receiver.id)))
        if can_pass and distance(xy(attacker), ball) <= COOP_CONTROL_M:
            attacker.action = "kick:pass"
            if attacker.pass_to(target, RESTART_POWER):
                store.attack_pass = PassPlan(attacker.id, receiver.id, target, ball, ctx.now)
                attacker.action = "kick:pass"
                receiver.action = "move:position"
                receiver.move_to_position(target)
                return
        else:
            chase(attacker, ball, shot or (ctx.field.length/2, 0))
    for p in supporters:
        p.action = "move:position"
        p.move_to_position(support_target(ctx, attacker, p))
