import unittest, tempfile, json, io, zipfile
from pathlib import Path
from unittest.mock import patch
import core, app

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.old=(app.DATA,app.DB); app.DATA=Path(self.tmp.name);app.DB=app.DATA/'test.sqlite3'
    def tearDown(self):
        app.DATA,app.DB=self.old;self.tmp.cleanup()
    def test_docx_roundtrip(self):
        lines=['Глава 1','Анна сказала: «Да & нет».','***','Он шёл шёл домой.']
        self.assertEqual(core.extract('a.docx',core.docx(core.structure(lines))),lines)
    def test_structure(self):
        ps=core.structure(['Глава первая','Текст','***','Продолжение','Глава вторая','Конец'])
        self.assertEqual(ps[3]['scene'],2);self.assertEqual(ps[5]['scene'],1);self.assertEqual(ps[0]['id'],1)
    def test_import_persistence(self):
        b=app.import_book('роман.txt','Анна ждала.\nАнна молчала.\nОн шёл шёл.'.encode())
        self.assertEqual(app.get(b['id'])['title'],'роман');self.assertTrue(any(c['name']=='Анна' for c in b['characters']))
        self.assertEqual(b['issues'][0]['quote'],'шёл шёл')
    def test_edit_backup_stale(self):
        b=app.import_book('a.txt','Он шёл шёл.'.encode());i=b['issues'][0];i['replacement']='шёл';app.save(b)
        app.apply(b,i['id']);self.assertEqual(app.get(b['id'])['paragraphs'][0]['text'],'Он шёл.')
        self.assertEqual(b['history'][0]['before'],'Он шёл шёл.');self.assertTrue(list((app.DATA/'backups').glob('*.sqlite3')))
        with self.assertRaises(ValueError):app.apply(b,i['id'])
    def test_ambiguous_edit_rejected(self):
        b=app.import_book('a.txt','да да и да да'.encode());i=b['issues'][0];i['replacement']='да'
        with self.assertRaises(ValueError):app.apply(b,i['id'])
    def test_txt_cp1251(self):self.assertEqual(core.extract('a.txt','Привет'.encode('cp1251')),['Привет'])
    def test_invalid_document(self):
        with self.assertRaises(ValueError):core.extract('x.pdf',b'x')
        with self.assertRaises(ValueError):core.extract('x.txt',b'')
    def test_chunks(self):
        ps=core.structure(['a'*40]*10);chunks=list(core.batches(ps,100));self.assertEqual({p['id'] for b in chunks for p in b},set(range(1,11)))
    def test_llm_references(self):
        content={'summary':'Резюме','issues':[{'pid':1,'quote':'нет в тексте','message':'x'},{'pid':1,'quote':'Анна','message':'x','replacement':'Анна'}],'characters':[{'name':'Анна','evidence':[1,99],'description':'x'}]}
        response=io.BytesIO(json.dumps({'choices':[{'message':{'content':json.dumps(content)}}]}).encode())
        with patch('urllib.request.urlopen',return_value=response):
            issues,chars=core.llm_audit(core.structure(['Анна ждала.']),{'url':'http://localhost:1234/v1','model':'test','mode':'auditor'},'')
        self.assertEqual(len(issues),1);self.assertEqual(chars[0]['evidence'],[1])
    def test_epub_spine(self):
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w') as z:
            z.writestr('META-INF/container.xml','<container><rootfile full-path="OEBPS/book.opf"/></container>')
            z.writestr('OEBPS/book.opf','<package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="b" href="b.xhtml"/><item id="a" href="a.xhtml"/></manifest><spine><itemref idref="a"/><itemref idref="b"/></spine></package>')
            z.writestr('OEBPS/a.xhtml','<html><p>Первый</p></html>');z.writestr('OEBPS/b.xhtml','<p>Второй</p>')
        self.assertEqual(core.extract('a.epub',out.getvalue()),['Первый','Второй'])
if __name__=='__main__':unittest.main()
