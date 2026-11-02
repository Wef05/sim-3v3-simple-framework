"""Combine recorded runs without hiding failed or missing protocol items."""
import argparse
import collections
import json
import statistics
from pathlib import Path

from analyze import summarize


def aggregate(paths):
    runs = [summarize(path)[0] for path in paths]
    groups = collections.defaultdict(list)
    segments = []
    for run in runs:
        for trial in run['trials']:
            for segment in trial['segments']:
                if 'command' not in segment:
                    continue
                row = dict(source=run['source'], trial=trial['trial'],
                           kind=trial['kind'], repeat=trial['repeat'], **segment)
                segments.append(row)
                if trial['kind'] in ('lateral', 'turn') and segment['complete']:
                    groups[tuple(segment['command'])].append(row)
    response = []
    for command, rows in sorted(groups.items()):
        axis = 1 if command[1] else 2
        values = [r['measured_mean'][axis] for r in rows]
        mean = statistics.mean(values)
        cv = statistics.pstdev(values) / abs(mean) if mean else None
        response.append(dict(command=list(command), repeats=len(rows),
            steady_repeats=sum(r['valid'] for r in rows),
            trial_means=values, median_trial_mean=statistics.median(values),
            min=min(values), max=max(values), relative_std=cv,
            qualified=(len(rows) == 3 and all(r['valid'] for r in rows)
                       and cv is not None and cv <= .15)))
    return dict(runs=[{k: r[k] for k in ('source', 'raw_records',
        'distinct_observations', 'observed_duration_s', 'maximum_gap_s')}
        for r in runs], response=response, segments=segments)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('raw', nargs='+')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    Path(args.output).write_text(json.dumps(aggregate(args.raw), indent=2))
