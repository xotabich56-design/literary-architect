import base64, json, os, sqlite3, uuid, threading, webbrowser, argparse
from pathlib import Path
from datetime import datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import core
ROOT=Path(__file__).parent
DATA=Path(os.environ.get('LA_DATA',str(ROOT/'data'))); DATA.mkdir(parents=True,exist_ok=True)
DB=DATA/'library.sqlite3'
LOCK=threading.RLock(); TOKEN=uuid.uuid4().hex

def connect():
    c=sqlite3.connect(DB,timeout=30); c.execute('CREATE TABLE IF NOT EXISTS books (id TEXT PRIMARY KEY, body TEXT NOT NULL)'); return c

def books():
    with connect() as c: return [json.loads(r[0]) for r in c.execute('SELECT body FROM books')]

def get(bid):
    with connect() as c: row=c.execute('SELECT body FROM books WHERE id=?',(bid,)).fetchone()
    if not row: raise ValueError('Книга не найдена')
    return json.loads(row[0])

def save(b):
    with connect() as c: c.execute('INSERT OR REPLACE INTO books VALUES (?,?)',(b['id'],json.dumps(b,ensure_ascii=False)))

def backup():
    folder=DATA/'backups'; folder.mkdir(exist_ok=True)
    with connect() as src, sqlite3.connect(folder/(datetime.now().strftime('%Y%m%d-%H%M%S-')+uuid.uuid4().hex[:6]+'.sqlite3')) as dst: src.backup(dst)

def import_book(name,data):
    ps=core.structure(core.extract(name,data)); issues,chars,stats=core.local_audit(ps)
    b={'id':uuid.uuid4().hex,'title':Path(name).stem,'paragraphs':ps,'issues':issues,'characters':chars,'stats':stats,'canon':'','history':[],'revision':0}
    for issue in issues: issue['id']=uuid.uuid4().hex; issue['status']='open'
    original=DATA/'originals'; original.mkdir(exist_ok=True); (original/(b['id']+'.'+name.rsplit('.',1)[-1].lower())).write_bytes(data)
    save(b); return b

def apply(b,issue_id):
    issue=next(i for i in b['issues'] if i['id']==issue_id)
    if issue.get('status')!='open' or not isinstance(issue.get('replacement'),str): raise ValueError('Нет доступной правки')
    p=next(p for p in b['paragraphs'] if p['id']==issue['pid'])
    if p['text'].count(issue['quote'])!=1: raise ValueError('Цитата изменилась или неоднозначна; выполните аудит заново')
    backup(); before=p['text']; p['text']=before.replace(issue['quote'],issue['replacement'],1)
    b['history'].append({'pid':p['id'],'before':before,'after':p['text'],'date':datetime.now().isoformat()})
    b['revision']+=1; issue['status']='applied'; b['stats']=core.local_audit(b['paragraphs'])[2]; save(b)

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def send(self,data,status=200,ctype='application/json; charset=utf-8'):
        if not isinstance(data,bytes): data=json.dumps(data,ensure_ascii=False).encode()
        self.send_response(status); self.send_header('Content-Type',ctype); self.send_header('Content-Length',str(len(data))); self.send_header('X-Content-Type-Options','nosniff'); self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        try:
            if self.headers.get('Host') not in (f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'): return self.send({'error':'Неверный Host'},403)
            if self.path=='/': return self.send((ROOT/'index.html').read_text(encoding='utf-8').replace('__TOKEN__',TOKEN).encode(),ctype='text/html; charset=utf-8')
            if self.path=='/api/books': return self.send([{'id':b['id'],'title':b['title']} for b in books()])
            if self.path.startswith('/api/book/'): return self.send(get(self.path.split('/')[-1]))
            if self.path.startswith('/export/'):
                return self.send(core.docx(get(self.path.split('/')[-1])['paragraphs']),ctype='application/vnd.openxmlformats-officedocument.wordprocessingml.document')
            self.send({'error':'Не найдено'},404)
        except Exception as e: self.send({'error':str(e)},400)
    def do_POST(self):
        try:
            if self.headers.get('X-Token')!=TOKEN: return self.send({'error':'Обновите страницу'},403)
            size=int(self.headers.get('Content-Length','0'))
            if size>30_000_000: raise ValueError('Лимит загрузки — 20 МБ')
            d=json.loads(self.rfile.read(size))
            if self.path=='/api/import':
                with LOCK: b=import_book(d['name'],base64.b64decode(d['data'],validate=True))
                return self.send(b)
            if self.path=='/api/audit':
                b=get(d['id']); revision=b['revision']; issues,chars=core.llm_audit(b['paragraphs'],d,b['canon'])
                with LOCK:
                    b=get(d['id'])
                    if b['revision']!=revision: raise ValueError('Рукопись изменилась во время анализа; повторите аудит')
                    for it in issues: it.update(id=uuid.uuid4().hex,status='open')
                    b['issues']+=issues
                    merged={c['name']:c for c in b['characters']}
                    for c in chars: merged[c['name']]=c
                    b['characters']=list(merged.values()); save(b)
                return self.send(b)
            with LOCK:
                b=get(d['id'])
                if self.path=='/api/apply': apply(b,d['issue'])
                elif self.path=='/api/canon': backup(); b['canon']=str(d['canon']); b['revision']+=1; save(b)
                elif self.path=='/api/undo':
                    if not b['history']: raise ValueError('История пуста')
                    backup(); h=b['history'].pop(); p=next(p for p in b['paragraphs'] if p['id']==h['pid']); p['text']=h['before']; b['revision']+=1
                    for i in b['issues']:
                        if i.get('status')=='applied': i['status']='stale'
                    b['stats']=core.local_audit(b['paragraphs'])[2]; save(b)
                else: raise ValueError('Неизвестная команда')
            self.send(b)
        except Exception as e:
            # Provider errors can contain request information: keep them out of UI/logs.
            if self.path=='/api/audit' and not isinstance(e,ValueError): self.send({'error':'API не завершил анализ. Проверьте URL, модель, ключ, лимиты и доступность провайдера. Рукопись не изменена.'},400)
            else: self.send({'error':str(e)},400)

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--port',type=int,default=8765); parser.add_argument('--no-browser',action='store_true'); args=parser.parse_args()
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    print(f'Литературный архитектор: http://127.0.0.1:{args.port} (Ctrl+C — выход)',flush=True)
    if not args.no_browser: threading.Timer(0.8,lambda:webbrowser.open(f'http://127.0.0.1:{args.port}')).start()
    server.serve_forever()
