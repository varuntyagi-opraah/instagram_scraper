import re,json,subprocess,time
from pathlib import Path
from html.parser import HTMLParser
from datetime import datetime,timezone
root=Path('/tmp/opraah-gamerfleet')
exe='/tmp/obscura-reel.KOzFnJ/obscura'
class Scripts(HTMLParser):
 active=False
 def __init__(self,s):super().__init__();self.blobs=[];self.feed(s)
 def handle_starttag(self,t,a):
  if t=='script':self.active=dict(a).get('type')=='application/json'
 def handle_endtag(self,t):
  if t=='script':self.active=False
 def handle_data(self,s):
  if self.active:
   try:self.blobs.append(json.loads(s))
   except ValueError:pass
def walk(o):
 if isinstance(o,dict):
  yield o
  for x in o.values():yield from walk(x)
 elif isinstance(o,list):
  for x in o:yield from walk(x)
def nodes(s):return (d for b in Scripts(s).blobs for d in walk(b))
tabs={}
for tab in ('profile','reels'):
 tabnodes=list(nodes((root/(tab+'.html')).read_text()))
 tabs[tab]=list(dict.fromkeys(d['code'] for d in tabnodes if re.fullmatch('[A-Za-z0-9_-]{11}',d.get('code') or '') ))
 print(tab,tabs[tab],flush=True)
 for d in tabnodes:
  if d.get('username')=='gamerfleetog' and 'follower_count' in d:print('public embedded followers',d['follower_count'],flush=True)
existing=root/'instaloader-codes.json'
rows=json.loads((root/"posts.json").read_text()) if (root/"posts.json").exists() else []
already={r["shortcode"] for r in rows}
for c in dict.fromkeys(tabs['profile']+tabs['reels']+(json.loads(existing.read_text()) if existing.exists() else [])):
 if c in already: continue
 url='https://www.instagram.com/p/'+c+'/'
 try:r=subprocess.run([exe,'fetch',url,'--dump','original','--timeout','10'],capture_output=True,text=True,errors='replace',timeout=13)
 except subprocess.TimeoutExpired:print('timeout',c,flush=True);continue
 if re.search(r'HTTP (?:429|403)',r.stderr,re.I) or '/accounts/login/?next=' in r.stderr:
  print('stopped on restriction',c,flush=True);break
 m=next((d for d in nodes(r.stdout) if d.get('code',d.get('shortcode'))==c and isinstance(d.get('taken_at'),int) and (isinstance(d.get('user'),dict) or isinstance(d.get('owner'),dict))),None)
 if not m:print('no matching metadata',c,flush=True);continue
 cap=m.get('caption') or {}
 loc=m.get('location') or {}
 ns=lambda v:[(x.get('sponsor') or x.get('user') or x).get('username') for x in (v or []) if isinstance(x,dict)]
 row={'shortcode':c,'url':'https://www.instagram.com/'+('reel' if m.get('product_type')=='clips' else 'p')+'/'+c+'/','owner':(m.get('user') or m.get('owner')).get('username'),'date_utc':datetime.fromtimestamp(m['taken_at'],timezone.utc).isoformat(),'type':'reel' if m.get('product_type')=='clips' else {1:'image',2:'video',8:'carousel'}.get(m.get('media_type')),'caption':cap.get('text','') if isinstance(cap,dict) else cap,'likes':m.get('like_count'),'comments':m.get('comment_count'),'views':next((m[k] for k in ('play_count','ig_play_count','view_count','video_view_count') if type(m.get(k))==int),None),'paid_partnership':m.get('is_paid_partnership'),'sponsors':ns(m.get('sponsor_tags')),'coauthors':ns(m.get('coauthor_producers')),'location':loc.get('name') if isinstance(loc,dict) else None,'sources':[t for t,cs in tabs.items() if c in cs]}
 rows.append(row)
 print(json.dumps({**row,'caption':row['caption'][:400]},ensure_ascii=False),flush=True)
 (root/'posts.json').write_text(json.dumps(rows,indent=2,ensure_ascii=False))
 time.sleep(.7)
