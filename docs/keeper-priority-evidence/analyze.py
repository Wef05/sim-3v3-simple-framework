"""Summarize recorded observations only; kick requests do not confirm contact."""
import collections
import gzip
import json
from pathlib import Path

root = Path(__file__).resolve().parent / 'run01'
with gzip.open(root / 'dashboard-team1.json.gz', 'rt') as f:
    replay = json.load(f)
frames = replay['frames']
counts, navigation, clearance, rejects = (collections.Counter() for _ in range(4))
interesting, transitions = [], []
last = None
for fr in frames:
    p = fr['packet']
    keeper = next((x for x in p['players'] if x['id'] == 2), {})
    ctx = p['context']
    action = keeper.get('action')
    counts[action] += 1
    if action != last:
        transitions.append(dict(id=fr['id'], sim=ctx['now'], remaining=(ctx.get('game') or {}).get('secs_remaining'), action=action))
        last = action
    d = keeper.get('guard_decision') or {}
    if d.get('close_clear_target_status'): clearance[d['close_clear_target_status']] += 1
    if d.get('close_clear_rejection'): rejects[d['close_clear_rejection']] += 1
    if (action or '').startswith('guard:block'):
        navigation[(keeper.get('navigation') or {}).get('state', 'none')] += 1
        interesting.append(fr)
    for player in p['players']:
        if (player.get('navigation') or {}).get('state') == 'kick_yield':
            interesting.append(fr)
            break
    if d.get('close_clear_target_status') == 'reselected': interesting.append(fr)
summary = dict(frames=len(frames), first_id=frames[0]['id'], last_id=frames[-1]['id'],
               sim_range=[frames[0]['packet']['context']['now'],frames[-1]['packet']['context']['now']],
               max_sim_gap=max(b['packet']['context']['now']-a['packet']['context']['now'] for a,b in zip(frames,frames[1:])),
               guard_actions=counts, block_navigation=navigation, clearance=clearance,rejections=rejects,
               transitions=transitions, interesting_ids=sorted({f['id'] for f in interesting}))
(root/'analysis.json').write_text(json.dumps(summary,indent=2))
print(json.dumps(summary,ensure_ascii=False,indent=2))
