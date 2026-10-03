from html.parser import HTMLParser
from pathlib import Path
import re
import unittest

class Parser(HTMLParser):
 def __init__(self):super().__init__();self.ids=[];self.scripts=[]
 def handle_starttag(self,tag,attrs):
  a=dict(attrs)
  if 'id' in a:self.ids.append(a['id'])
  if tag=='script' and 'src' in a:self.scripts.append(a['src'])

class UIContractTests(unittest.TestCase):
 def test_controls_referenced_in_script_exist_and_ids_are_unique(self):
  root=Path(__file__).resolve().parents[1]
  parser=Parser();parser.feed((root/'ui/index.html').read_text())
  self.assertEqual(len(parser.ids),len(set(parser.ids)))
  script=(root/'ui/app.js').read_text()
  used=set(re.findall(r"\$\('([^']+)'\)",script))
  self.assertFalse(used-set(parser.ids),str(used-set(parser.ids)))
  self.assertNotIn('innerHTML',script)
  for src in parser.scripts:self.assertTrue((root/'ui'/src).exists())
 def test_preview_is_self_contained_and_explicitly_labeled(self):
  root=Path(__file__).resolve().parents[1]
  text=(root/'WhiteBoardOS-preview.html').read_text()
  self.assertIn('const preview=true;',text)
  self.assertIn('DESIGN PREVIEW',text)
  self.assertNotIn('src="app.js"',text)
  self.assertNotIn('href="style.css"',text)
