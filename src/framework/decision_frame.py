"""三步决策前的帧准备：整队共享球运动观测，各自计算截球能力。

历史球观测存于 store.ball_motion，跨角色和比赛阶段连续保留。
预测器内部处理丢球、过期和异常跳变；这里不因角色变化重建预测器。
"""
from ..utils.ball_motion import BallMotion
from ..utils.interception import estimate_ball_intercept

def prepare_frame(context, players, store):
    """运行时每帧调用一次；离线直接运行三步流程时，也应先调用本函数。

    只更新观测与计算结果，不执行起身或任何底盘动作。
    角色分配、个人决策和看板共用同一轨迹及截球结果，避免重复估计造成分歧。
    """
    if not hasattr(store, 'ball_motion'):
        store.ball_motion = BallMotion()
    store.ball_motion.update(context)
    for p in players:
        p.context = context
        p.motion = store.ball_motion
        # 球轨迹共享，但机器人位姿和到达能力不同，所以截球结果按人计算。
        p.ball_intercept = estimate_ball_intercept(
            p.pose, store.ball_motion, fallen=p.is_fallen, penalized=p.is_penalized)
        # 清除的是本帧输出，不是跨帧行为记忆或踢球状态。
        # 否则换角色后会显示旧目标，或破坏原有迟滞与动作切换。
        p.freeman_region = None
        p.tactical_target = None
        p.controller.tactical_target = None
