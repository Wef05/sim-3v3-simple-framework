"""Package a capture spool as an importable, lossless dashboard replay."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path


def package(source: Path, output: Path) -> dict:
    count = 0
    first_id = last_id = first_time = last_time = None
    gaps = []
    with source.open() as stream, gzip.open(output, 'xt') as target:
        target.write('{"format":"soccer-tactics-replay","version":1,'
                     '"team_id":1,"recording_hz":30,"frames":[')
        for line in stream:
            frame = json.loads(line)
            now = frame['packet']['context']['now']
            if last_id is not None:
                if frame['id'] <= last_id or now < last_time:
                    raise ValueError('Frame ordering or simulation clock reset')
                if frame['id'] != last_id + 1:
                    gaps.append([last_id, frame['id']])
                target.write(',')
            else:
                first_id, first_time = frame['id'], now
            target.write(line.strip())
            last_id, last_time = frame['id'], now
            count += 1
        target.write(']}')
    if not count:
        raise ValueError('Empty capture')
    return dict(file=str(output), frames=count, first_id=first_id,
                last_id=last_id, first_sim_time=first_time,
                last_sim_time=last_time, id_gaps=gaps,
                bytes=output.stat().st_size,
                sha256=hashlib.sha256(output.read_bytes()).hexdigest())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    info = package(args.source, args.output)
    args.output.with_suffix('.metadata.json').write_text(json.dumps(info, indent=2))
    print(json.dumps(info))
