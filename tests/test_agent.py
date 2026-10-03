import json
import tempfile
import unittest
from pathlib import Path
from whiteboardos.agent import Runner
from whiteboardos.workspace import Workspace

class LocalClient:
    def __init__(self,workspace):self.workspace=workspace
    def call(self,name,args=None,call_id=None):
        return self.workspace.dispatch(name,args or {},call_id)
class FakeGemini:
    def __init__(self):self.calls=[]
    def generate(self,contents,tools):
        self.calls.append(json.loads(json.dumps(contents)))
        if len(self.calls)==1:
            return {'candidates':[{'content':{'role':'model','parts':[{'thoughtSignature':'opaque',
                'functionCall':{'name':'search_files','args':{'query':'Atlas'}}}]}}],
                'usageMetadata':{'promptTokenCount':15,'candidatesTokenCount':7}}
        return {'candidates':[{'content':{'role':'model','parts':[{'text':'Found the Atlas proposal.'}]}}],
                'usageMetadata':{'promptTokenCount':25,'candidatesTokenCount':8}}
class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.base=Path(self.temp.name)
        self.root=self.base/'docs';self.root.mkdir()
        (self.root/'a.txt').write_text('Atlas proposal version 2')
        self.w=Workspace(self.root,self.base/'state')
        self.client=LocalClient(self.w)
    def tearDown(self):self.w.db.close();self.temp.cleanup()
    def test_protocol_signatures_usage_and_checkpoint(self):
        provider=FakeGemini()
        task=Runner(self.client,self.base,provider).run('Find Atlas')
        self.assertEqual(task['status'],'done')
        self.assertEqual(task['usage']['promptTokenCount'],40)
        self.assertEqual(task['usage']['candidatesTokenCount'],15)
        self.assertEqual(provider.calls[1][1]['parts'][0]['thoughtSignature'],'opaque')
        self.assertIn('functionResponse',provider.calls[1][2]['parts'][0])
        self.assertTrue((self.base/'tasks'/(task['id']+'.json')).exists())
    def test_resume_pending_mutation_replays_once(self):
        r=self.w.list_files()['files'][0]
        class Mutator:
            def generate(inner,contents,tools):
                return {'candidates':[{'content':{'role':'model','parts':[{'functionCall':{
                    'name':'move_file','args':{'file_id':r['id'],'destination':'b.txt','expected_hash':r['hash']}}}]}}]}
        task=Runner(self.client,self.base,Mutator(),lambda n,a:True).run('Move',max_rounds=1)
        self.assertEqual(task['status'],'paused')
        self.assertTrue((self.root/'a.txt').exists())
        class Done:
            def generate(inner,contents,tools):
                return {'candidates':[{'content':{'role':'model','parts':[{'text':'Done'}]}}]}
        resumed=Runner(self.client,self.base,Done(),lambda n,a:True).run(task_id=task['id'])
        self.assertEqual(resumed['status'],'done')
        self.assertTrue((self.root/'b.txt').exists())
        self.assertEqual(len(self.w.history()['operations']),1)
    def test_user_denial_stops_mutation(self):
        r=self.w.list_files()['files'][0]
        class Denied:
            def __init__(inner):inner.count=0
            def generate(inner,contents,tools):
                inner.count+=1
                if inner.count==1:
                    return {'candidates':[{'content':{'role':'model','parts':[{'functionCall':{
                        'name':'move_file','args':{'file_id':r['id'],'destination':'b.txt','expected_hash':r['hash']}}}]}}]}
                return {'candidates':[{'content':{'role':'model','parts':[{'text':'Declined'}]}}]}
        t=Runner(self.client,self.base,Denied()).run('Move')
        self.assertTrue((self.root/'a.txt').exists())
        self.assertEqual(t['contents'][2]['parts'][0]['functionResponse']['response']['error'],
            'Local user declined; do not repeat without a new request')
    def test_provider_failure_is_resumable(self):
        class Fail:
            def generate(self,*args):raise RuntimeError('offline')
        task=Runner(self.client,self.base,Fail()).run('Find Atlas')
        self.assertEqual(task['status'],'paused')
        self.assertEqual(task['error'],'offline')

if __name__=='__main__':unittest.main()
