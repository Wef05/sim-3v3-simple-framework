"""二维球路与球门结构检查；连续线段，无球网采样空隙。"""
import math
from ..param import GOAL_DEPTH, POST_RADIUS, KICK_LANE_BALL_RADIUS_M
from .obstacles import goal_net_rectangles


def point_segment(p, a, b):
    dx, dy = b[0]-a[0], b[1]-a[1]
    length = dx*dx+dy*dy
    t = max(0, min(1, ((p[0]-a[0])*dx+(p[1]-a[1])*dy)/length)) if length else 0
    return math.hypot(p[0]-a[0]-t*dx, p[1]-a[1]-t*dy)


def segment_distance(a, b, c, d):
    def cross(p,q,r):return (q[0]-p[0])*(r[1]-p[1])-(q[1]-p[1])*(r[0]-p[0])
    distances=[point_segment(a,c,d),point_segment(b,c,d),point_segment(c,a,b),point_segment(d,a,b)]
    if min(distances)<1e-10:return 0
    if cross(a,b,c)*cross(a,b,d)<0 and cross(c,d,a)*cross(c,d,b)<0:return 0
    return min(distances)


def check_kick_lane(field, origin, target):
    """禁止穿柱/网；朝球门内的球路必须从场内正向穿过门口。"""
    hl, hw = field.length/2, field.goal_width/2
    for side in (-1,1):
        front, back = side*hl, side*(hl+GOAL_DEPTH)
        for y in (-hw,hw):
            for x in (front,back):
                if point_segment((x,y),origin,target)<=POST_RADIUS+KICK_LANE_BALL_RADIUS_M:
                    return dict(blocked=True,reason='goal_post')
        if side*target[0]>=hl-1e-9 and abs(target[1])<=hw:
            if side*origin[0]>=hl-1e-9:
                return dict(blocked=True,reason='goal_side_entry')
            fraction=(front-origin[0])/(target[0]-origin[0])
            crossing_y=origin[1]+fraction*(target[1]-origin[1])
            if not 0<=fraction<=1 or abs(crossing_y)>=hw-POST_RADIUS-KICK_LANE_BALL_RADIUS_M:
                return dict(blocked=True,reason='goal_side_entry')
    for xmin,ymin,xmax,ymax in goal_net_rectangles(field):
        corners=[(xmin,ymin),(xmax,ymin),(xmax,ymax),(xmin,ymax)]
        inside=any(xmin<=p[0]<=xmax and ymin<=p[1]<=ymax for p in (origin,target))
        if inside or any(segment_distance(origin,target,corners[i],corners[(i+1)%4])
                         <=KICK_LANE_BALL_RADIUS_M for i in range(4)):
            return dict(blocked=True,reason='goal_net')
    return dict(blocked=False,reason=None)
