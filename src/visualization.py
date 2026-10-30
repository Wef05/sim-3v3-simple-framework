from __future__ import annotations
from .framework.types import Context
from .utils import dist
from .telemetry import publish

def _draw_teammate_marker(p: Player) -> None:
    """我方队员可视化:红色。踢球中→方块,否则→球体。

    每帧对所有球员统一调用(不受 phase/判罚/就绪影响)。标签两行:
    - 上:编号 + 当前高层动作(``p.action``),踢球中追加 ``[KICK]``。
    - 通过形状(方块 vs 球体)再次区分是否进入 kick 状态。
    """
    from .framework import debugdraw

    if p.pose is None:
        return
    red = (1.0, 0.2, 0.2)
    if p.is_kicking:
        debugdraw.cube(p.pose.x, p.pose.y, rgb=red, scale=0.38, ns="teammate")
    else:
        debugdraw.point(p.pose.x, p.pose.y, rgb=red, scale=0.3, ns="teammate")
    kick_tag = " [KICK]" if p.is_kicking else ""
    label = f"{p.id}:{p.action}{kick_tag}"
    debugdraw.text(p.pose.x, p.pose.y, label, rgb=(1.0, 0.9, 0.6), ns="teammate_id")

def _analyze_and_draw(context: Context, players: list[Player], store) -> None:
    """每帧:计算球员到球的距离,画可视化。

    不再依赖 analysis 模块;距离改为基于球当前位置。
    """
    from .framework import debugdraw

    ball = context.ball

    # 球不可见:无可视化
    if ball is None:
        return

    # 1. 画球当前位置(绿色点)
    debugdraw.point(ball.x, ball.y, rgb=(0.0, 1.0, 0.0), scale=0.2, ns="ball_current")

    # 2. 球员到球的距离:我方(红标签)+ 敌方(蓝标签)
    for p in players:
        if p.pose is None:
            continue
        d = dist(p.pose.x, p.pose.y, ball.x, ball.y)
        debugdraw.text(
            p.pose.x + 0.3, p.pose.y - 0.3, f"{d:.1f}m",
            rgb=(1.0, 0.6, 0.6), ns="dist_ours",
        )
    for r in context.opponents.values():
        if r.pose is None:
            continue
        d = dist(r.pose.x, r.pose.y, ball.x, ball.y)
        debugdraw.text(
            r.pose.x + 0.3, r.pose.y - 0.3, f"{d:.1f}m",
            rgb=(0.6, 0.6, 1.0), ns="dist_opp",
        )

def finish_frame(context, players, store):
    from .framework import debugdraw
    phase = getattr(store, 'cur_phase', None)
    g = context.game
    debugdraw.text(0.0, context.field.width / 2.0 + .2,
        f"phase={phase.value if phase else 'none'} state={g.state.value if g else 'none'} "
        f"set={g.set_play.value if g else 'none'} secondary={g.secondary_time if g else 0:.1f}",
        rgb=(1, 1, 0), ns='phase')
    _analyze_and_draw(context, players, store)
    for p in players:
        _draw_teammate_marker(p)
    from .utils.kick_error import KickErrorTracker
    if not hasattr(store, "kick_error_tracker"):
        store.kick_error_tracker = KickErrorTracker()
    store.kick_error_tracker.update(context, players)
    publish(context, players, store)
