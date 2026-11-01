"""Fixed input comparison; offline commands are not simulator outcomes."""
import json
import math
from statistics import mean
from time import perf_counter
from checks.test_navigation import context, controller, frame
from src.intents import MoveIntent
from src.utils.obstacles import Obstacle
from src.utils.path_planner import plan_global_path, segment_clear

cases = []
for start, target in (((0., 4.), (0., 5.5)), ((6.5, 3.), (8., 3.))):
    for direct in (False, True):
        ctx = context((start,))
        c = controller(ctx)
        command, arrived = c.plan_walk(MoveIntent(target, direct_path=direct, maintain_face=True), frame(ctx))
        cases.append(dict(start=start, target=target, direct=direct, command=command,
                          arrived=arrived, navigation=c.navigation_status))
wall = [Obstacle(0., -.4*i, .25) for i in range(-11, 12)]
benchmarks = []
for name, obstacles in [('single', [Obstacle(0., 0., .25)]), ('wall', wall)]:
    samples = []
    path = None
    for _ in range(30):
        before = perf_counter()
        path = plan_global_path(context(), (-1., 0.), (1., 0.), obstacles, omni=True)
        samples.append((perf_counter()-before)*1000)
    benchmarks.append(dict(name=name, mean_ms=mean(samples), p95_ms=sorted(samples)[math.ceil(.95*len(samples))-1],
                           samples_ms=samples, path=path,
                           safe=all(segment_clear(a,b,obstacles) for a,b in zip(path,path[1:])) if path else None))
print(json.dumps(dict(cases=cases, benchmarks=benchmarks), indent=2))
