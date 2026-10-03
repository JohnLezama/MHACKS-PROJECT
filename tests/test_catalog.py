import datetime as dt
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo
from whiteboardos.catalog import Catalog

class CatalogTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name)
  self.docs=self.base/'docs';self.docs.mkdir()
  (self.docs/'audio.txt').write_text('Project AudioLab. Raspberry Pi USB microphone audio recording guide.')
  (self.docs/'budget.txt').write_text('Project AudioLab. Hardware budget: microphone and Raspberry Pi.')
  self.c=Catalog(self.docs,self.base/'state')
 def tearDown(self):self.c.db.close();self.tmp.cleanup()
 def file(self,name):return next(f for f in self.c.files()['files'] if f['path']==name)
 def test_local_metadata_validated_and_cached(self):
  calls=[]
  def generate(prompt):
   calls.append(prompt);return {'summary':'USB microphone setup','keywords':['audio','microphone'],
    'project':'AudioLab','document_type':'guide'}
  self.assertTrue(self.c.summarize_one(generate))
  self.assertTrue(self.c.summarize_one(generate))
  self.assertFalse(self.c.summarize_one(generate))
  self.assertEqual(len(calls),2)
  self.assertEqual(self.file('audio.txt')['metadata_status'],'ready')
  self.assertEqual(len(self.c.related_files(self.file('audio.txt')['id'])['related']),1)
 def test_bad_metadata_marked_error_and_falls_back(self):
  self.c.summarize_one(lambda prompt:{'summary':42})
  row=self.file('audio.txt')
  self.assertEqual(row['metadata_status'],'error')
  self.assertIn('Raspberry Pi',row['summary'])
 def test_stale_summaries_not_used(self):
  self.c.summarize_one(lambda prompt:{'summary':'OLD SUMMARY','keywords':['old'],'project':'AudioLab','document_type':'guide'})
  (self.docs/'audio.txt').write_text('Updated file discusses a camera instead')
  self.c.index()
  row=self.file('audio.txt')
  self.assertEqual(row['metadata_status'],'pending')
  self.assertNotIn('OLD SUMMARY',row['summary'])
  self.assertEqual(row['keywords'],[])
 def test_view_events_are_not_created_by_index_or_read(self):
  self.c.index();self.c.read_file(self.file('audio.txt')['id'])
  self.assertEqual(self.c.timeline()['events'],[])
 def test_yesterday_uses_calendar_day_and_human_activity(self):
  zone=ZoneInfo('America/Chicago')
  now=dt.datetime(2026,10,3,15,0,tzinfo=zone).timestamp()
  yesterday=dt.datetime(2026,10,2,16,30,tzinfo=zone).timestamp()
  self.c.record_view(self.file('audio.txt')['id'],yesterday)
  self.c.record_view(self.file('budget.txt')['id'],now)
  result=self.c.find_documents('Raspberry Pi audio',activity='human_view',time_range='yesterday',now=now)
  self.assertEqual([f['path'] for f in result['matches']],['audio.txt'])
  self.assertEqual(result['time_window']['start'],'2026-10-02T00:00:00-05:00')
 def test_previous_view_matches_even_if_opened_again_today(self):
  zone=ZoneInfo('America/Chicago');now=dt.datetime(2026,10,3,15,0,tzinfo=zone).timestamp()
  fid=self.file('audio.txt')['id'];self.c.record_view(fid,now-86400);self.c.record_view(fid,now)
  self.assertEqual(len(self.c.find_documents('audio',time_range='yesterday',now=now)['matches']),1)
 def test_unsubstantiated_project_not_added_to_graph(self):
  self.c.summarize_one(lambda prompt:{'summary':'A document','keywords':[],
    'project':'Invented Project','document_type':'guide'})
  self.assertEqual(self.c.db.execute('SELECT count(*) FROM relations').fetchone()[0],0)
 def test_pin_tracks_file_and_unknown_id_rejected(self):
  fid=self.file('audio.txt')['id'];self.c.pin(fid)
  self.assertEqual(self.file('audio.txt')['pinned'],1)
  with self.assertRaises(ValueError):self.c.pin('not-a-file')
 def test_dispatch_agent_context_tool(self):
  result=self.c.dispatch('find_documents',{'query':'microphone'})
  self.assertTrue(result['matches'])

class PDFTests(unittest.TestCase):
 @unittest.skipUnless(shutil.which('pdftotext'),'Poppler required')
 def test_pdf_extraction_id_stability_and_topic_retrieval(self):
  with tempfile.TemporaryDirectory() as d:
   base=Path(d);docs=base/'docs';docs.mkdir()
   sample=Path(__file__).resolve().parents[1]/'demo/Raspberry_Pi_Audio.pdf'
   shutil.copy(sample,docs/'audio.pdf')
   c=Catalog(docs,base/'state')
   try:
    fid=c.files()['files'][0]['id'];c.index()
    self.assertEqual(c.files()['files'][0]['id'],fid)
    result=c.find_documents('Raspberry Pi audio',file_type='pdf')
    self.assertEqual(result['matches'][0]['file_id'],fid)
    self.assertIn('USB microphones',c.read_file(fid)['content'])
   finally:c.db.close()
