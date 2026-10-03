import io
import json
import tempfile
import unittest
from pathlib import Path
from whiteboardos.server import Controller,Handler
from types import SimpleNamespace

class FakeHandler(Handler):
 def send(self,status,data,kind='application/json'):
  self.sent=(status,data,kind)

class ServerTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name)
  root=self.base/'docs';root.mkdir();(root/'a.txt').write_text('Hello Atlas')
  self.ui=self.base/'ui';self.ui.mkdir();(self.ui/'index.html').write_text('WhiteBoardOS')
  self.c=Controller(root,self.base/'state',self.ui,worker=False)
 def tearDown(self):self.c.catalog.db.close();self.tmp.cleanup()
 def request(self,path,data=None,token=True,host='127.0.0.1:8765'):
  h=object.__new__(FakeHandler);h.server=SimpleNamespace(controller=self.c);h.path=path
  h.headers={'Host':host}
  if data is None:h.do_GET()
  else:
   encoded=json.dumps(data).encode();h.rfile=io.BytesIO(encoded)
   h.headers['Content-Length']=str(len(encoded))
   if token:h.headers['X-WhiteBoard-Token']=self.c.token
   h.do_POST()
  return h.sent
 def test_status_never_exposes_api_key(self):
  self.c.set_config({'api_key':'secret-example','timezone':'America/Chicago'})
  self.assertNotIn('secret-example',json.dumps(self.c.status()))
  self.assertEqual((self.base/'state/config.json').stat().st_mode&0o777,0o600)
 def test_mutation_requires_csrf_and_valid_host(self):
  self.assertEqual(self.request('/api/create',{'path':'b.txt','content':'x'},token=False)[0],403)
  self.assertEqual(self.request('/api/status',host='attacker.example')[0],403)
 def test_path_traversal_not_served(self):
  self.assertEqual(self.request('/../../etc/passwd')[0],404)
 def test_open_event_recorded_only_by_explicit_view(self):
  fid=self.c.catalog.files()['files'][0]['id']
  self.request('/document/'+fid)
  self.assertEqual(self.c.catalog.timeline()['events'],[])
  self.request('/api/view',{'file_id':fid})
  self.assertEqual(len(self.c.catalog.timeline()['events']),1)
 def test_import_uses_no_overwrite(self):
  import base64
  data={'name':'a.txt','content':base64.b64encode(b'changed').decode()}
  self.assertEqual(self.request('/api/import',data)[0],400)
  self.assertEqual((self.base/'docs/a.txt').read_text(),'Hello Atlas')
 def test_missing_gemini_key_reports_actionable_error(self):
  code,data,_=self.request('/api/task',{'goal':'find Atlas'})
  self.assertEqual(code,400);self.assertIn('Settings',data['error'])
