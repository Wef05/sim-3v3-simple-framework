"""Compare held-out motion against a baseline frozen before validation starts.

The baseline uses median trial means, including unstable trials; comparisons are
exploratory unless the corresponding baseline passes its qualification criteria.
No forward-speed claim is made because forward motion was not calibrated alone.
"""
import argparse
import json
from pathlib import Path

from analyze import summarize


def validate(reference: dict, paths: list[str]) -> list[dict]:
    lookup = {tuple(r['command']): r for r in reference['response']}
    results = []
    for path in paths:
        run, _ = summarize(path)
        for trial in run['trials']:
            for segment in trial['segments']:
                if not segment.get('complete'):
                    continue
                comparisons = []
                for axis in (1, 2):
                    command = [0., 0., 0.]
                    command[axis] = segment['command'][axis]
                    if not command[axis] or tuple(command) not in lookup:
                        continue
                    base = lookup[tuple(command)]
                    predicted = base['median_trial_mean']
                    measured = segment['measured_mean'][axis]
                    comparisons.append(dict(axis=axis, predicted=predicted,
                        measured=measured, error=measured-predicted,
                        relative_error=(measured-predicted)/abs(predicted)
                            if predicted else None,
                        baseline_qualified=base['qualified']))
                results.append(dict(source=path, trial=trial['trial'],
                    kind=trial['kind'], phase=segment['phase'],
                    steady_detected=segment['steady_detected'],
                    command=segment['command'], comparisons=comparisons,
                    dynamics=segment['dynamics'], braking=trial['braking']))
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('raw', nargs='+')
    parser.add_argument('--reference', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    reference = json.loads(Path(args.reference).read_text())
    result = validate(reference, args.raw)
    Path(args.output).write_text(json.dumps(result, indent=2))
