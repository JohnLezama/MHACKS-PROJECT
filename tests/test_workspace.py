import json
import os
import tempfile
import unittest
from pathlib import Path
from whiteboardos.workspace import Workspace,digest

class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.base=Path(self.temp.name)
        self.root=self.base/'documents'
        self.root.mkdir()
        (self.root/'old.txt').write_text('Atlas proposal version 1. Budget 500.')
        (self.root/'latest.txt').write_text('Atlas proposal version 2 supersedes version 1. Budget 750.')
        self.w=Workspace(self.root,self.base/'state')
    def tearDown(self):
        self.w.db.close()
        self.temp.cleanup()
    def file(self,name='latest.txt'):
        return next(x for x in self.w.list_files()['files'] if x['path']==name)
    def test_index_search_and_bounded_read(self):
        matches=self.w.search_files('750')['matches']
        self.assertEqual(matches[0]['path'],'latest.txt')
        r=self.w.read_file(matches[0]['id'],0,5)
        self.assertEqual(r['content'],'Atlas')
        self.assertEqual(r['next_offset'],5)
    def test_stable_id_after_move_and_undo(self):
        r=self.file()
        op=self.w.dispatch('move_file',{'file_id':r['id'],'destination':'Atlas/latest.txt','expected_hash':r['hash']},'move-1')
        self.w.index()
        self.assertEqual(self.file('Atlas/latest.txt')['id'],r['id'])
        self.w.dispatch('undo',{'operation_id':op['operation_id']},'undo-1')
        self.assertTrue((self.root/'latest.txt').exists())
        self.assertEqual(self.file()['id'],r['id'])
    def test_move_retry_is_idempotent(self):
        r=self.file()
        args={'file_id':r['id'],'destination':'new.txt','expected_hash':r['hash']}
        first=self.w.dispatch('move_file',args,'call-1')
        self.assertEqual(first,self.w.dispatch('move_file',args,'call-1'))
        with self.assertRaises(ValueError):
            self.w.dispatch('move_file',{**args,'destination':'different.txt'},'call-1')
    def test_no_overwrite_or_stale_write(self):
        r=self.file()
        with self.assertRaises(ValueError):
            self.w.move_file(r['id'],'old.txt',r['hash'],'x')
        (self.root/'latest.txt').write_text('Changed outside engine')
        with self.assertRaises(ValueError):
            self.w.write_file('latest.txt','replacement',r['hash'],'y')
    def test_symlink_and_traversal_blocked(self):
        (self.root/'escape').symlink_to(self.base,target_is_directory=True)
        for value in ('../outside.txt','/etc/passwd','escape/test.txt'):
            with self.assertRaises(ValueError):
                self.w.path(value)
    def test_write_and_undo_preserve_original(self):
        r=self.file()
        op=self.w.dispatch('write_file',{'path':'latest.txt','content':'new version','expected_hash':r['hash']},'write-1')
        self.w.dispatch('undo',{'operation_id':op['operation_id']},'undo-1')
        self.assertEqual(digest((self.root/'latest.txt').read_bytes()),r['hash'])
    def test_undo_refuses_intervening_edit(self):
        r=self.file()
        op=self.w.dispatch('move_file',{'file_id':r['id'],'destination':'new.txt','expected_hash':r['hash']},'move-2')
        (self.root/'new.txt').write_text('edited')
        with self.assertRaises(ValueError):
            self.w.dispatch('undo',{'operation_id':op['operation_id']},'undo-2')
    def test_crash_between_link_and_unlink_recovers(self):
        r=self.file()
        self.w._record('crash','move',{'file_id':r['id'],'source':r['path'],'destination':'new.txt','hash':r['hash']})
        os.link(self.root/'latest.txt',self.root/'new.txt')
        self.w.db.close()
        self.w=Workspace(self.root,self.base/'state')
        self.assertFalse((self.root/'latest.txt').exists())
        self.assertEqual(self.file('new.txt')['id'],r['id'])
        result=self.w.dispatch('move_file',{'file_id':r['id'],'destination':'new.txt','expected_hash':r['hash']},'crash')
        self.assertEqual(result['path'],'new.txt')
    def test_removed_file_disappears_and_special_file_not_read(self):
        (self.root/'old.txt').unlink()
        os.mkfifo(self.root/'pipe.txt')
        self.w.index()
        self.assertEqual(self.w.list_files()['total'],1)
    def test_changed_index_updates_search(self):
        (self.root/'latest.txt').write_text('new unique aardvark')
        self.w.index()
        self.assertEqual(len(self.w.search_files('aardvark')['matches']),1)
        self.assertEqual(len(self.w.search_files('750')['matches']),0)

if __name__=='__main__':
    unittest.main()
