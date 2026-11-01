import importlib.util,json,urllib.parse,io,unittest,gzip
from pathlib import Path
from unittest.mock import patch
sp=importlib.util.spec_from_file_location('exporter',Path(__file__).with_name('export-dashboard-final.py'));m=importlib.util.module_from_spec(sp);sp.loader.exec_module(m)
evidence=Path(__file__).resolve().parents[1]
frames=[]
for run in ('run02','run03'):
 with gzip.open(evidence/run/'dashboard-team1.json.gz','rt') as f:frames.extend(json.load(f)['frames'])
boundary=json.loads((evidence/'run02/start-boundary.json').read_text())
def fake(url,timeout):
 q=urllib.parse.parse_qs(urllib.parse.urlparse(url).query);after=int(q['after'][0]);limit=int(q['limit'][0]);batch=[f for f in frames if f['id']>after][:limit]
 return io.BytesIO(json.dumps(dict(session=boundary['session'],last_id=frames[-1]['id'],recording_hz=30,frames=batch)).encode())
class BoundsTests(unittest.TestCase):
 def test_fixed_end_excludes_next_match(self):
  with patch.object(m.urllib.request,'urlopen',fake):
   d=m.export('http://test',1,boundary['last_id'],boundary['session'],313432)
  self.assertEqual(len(d['frames']),1361);self.assertEqual(d['frames'][-1]['id'],313432)
 def test_unbounded_mixed_window_rejected(self):
  with patch.object(m.urllib.request,'urlopen',fake),self.assertRaisesRegex(ValueError,'clock reset'):
   m.export('http://test',1,boundary['last_id'],boundary['session'])
 def test_wrong_session_rejected(self):
  with patch.object(m.urllib.request,'urlopen',fake),self.assertRaisesRegex(ValueError,'session changed'):
   m.export('http://test',1,boundary['last_id'],'other',313432)
 def test_future_end_rejected(self):
  with patch.object(m.urllib.request,'urlopen',fake),self.assertRaisesRegex(ValueError,'Invalid end'):
   m.export('http://test',1,boundary['last_id'],boundary['session'],99999999)
unittest.main()
