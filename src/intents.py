"""Immutable contracts between allocation, team decisions and execution."""
from dataclasses import dataclass
from enum import Enum
from .param import ARRIVE_DIST

class Role(str, Enum):
    ATTACKER = 'attack'
    GUARD = 'guard'
    FREEMAN = 'freeman'
    KICKOFF = 'kickoff'
    READY = 'ready'
    OPP_KICKOFF = 'opp_kickoff'
    STAY = 'stay'
    UNAVAILABLE = 'unavailable'

@dataclass(frozen=True)
class RoleTask:
    role: Role
    slot: int = 0
    pass_to: int | None = None
    our_kickoff: bool = False
    reason: str = ''

@dataclass(frozen=True)
class KickTarget:
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
    target: tuple[float, float]
    face: float | None = None
    avoid_ball: bool = False
    avoid_robots: bool = False
    arrive_dist: float = ARRIVE_DIST
    maintain_face: bool = False

@dataclass(frozen=True)
class FaceIntent:
    theta: float

@dataclass(frozen=True)
class KickIntent:
    target: KickTarget
    style: str = 'shot'

@dataclass(frozen=True)
class StopIntent:
    pass

@dataclass(frozen=True)
class RecoverIntent:
    pass

ActionIntent = MoveIntent | FaceIntent | KickIntent | StopIntent | RecoverIntent
