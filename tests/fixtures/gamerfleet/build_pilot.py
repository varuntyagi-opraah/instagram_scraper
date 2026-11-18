import json, re, statistics, zipfile
from collections import Counter
from pathlib import Path
from datetime import datetime, timezone
root=Path('/tmp/opraah-gamerfleet')
out=root/'pilot'
for d in ('raw','wiki/brands','wiki/creators'):(out/d).mkdir(parents=True,exist_ok=True)
read=lambda p:json.loads((root/p).read_text())
def write(path,obj):path.write_text(json.dumps(obj,indent=2,ensure_ascii=False)+'\n')
all_posts={x['shortcode']:x for x in read('posts.json')}
grid_codes=read('instaloader-codes.json')
tab=(root/'reels.html').read_text()
plays={code:int(count) for count,code in re.findall(r'"play_count":(\d+),"clips_tab_pinned_user_ids":\[[^\]]*\],"code":"([\w-]{11})"',tab)}
selected=sorted((all_posts[c] for c in grid_codes),key=lambda r:r['date_utc'],reverse=True)[:30]
labels=['Gaming','Family and festivals','Gaming','Gaming','Family and festivals','Family and festivals','Brand integrations','Family and festivals','Brand integrations','Cars','Brand integrations','Brand integrations','Brand integrations','Cars','Gaming','Gaming','Gaming','Creator and culture','Cars','Brand integrations','Creator and culture','Travel and lifestyle','Travel and lifestyle','Creator and culture','Gaming','Travel and lifestyle','Travel and lifestyle','Gaming','Gaming','Unclear']
assert len(selected)==len(labels)==len({p['shortcode'] for p in selected})==30 and len(plays)==12
for p in all_posts.values():
 c=p['shortcode']
 if c in plays:
  p['plays']=plays[c]
  p['plays_source']='https://www.instagram.com/gamerfleetog/reels/'
 elif p.get('views') is not None:
  p['plays_source']=p['url']
for p, theme in zip(selected,labels):
 p['primary_theme']=theme
 p['is_profile_timeline_entry']=True
extras=sorted((all_posts[c] for c in plays if c not in grid_codes and all_posts[c]['date_utc']>=selected[-1]['date_utc']),key=lambda p:p['date_utc'],reverse=True)
for p in extras:p['is_profile_timeline_entry']=False
observed=datetime.fromtimestamp((root/'posts.json').stat().st_mtime,timezone.utc).isoformat()
metrics={'source_observed_at_utc':observed,'scope':'30 most recent by timestamp among 34 entries from the public main profile timeline','followers_embedded':2815790,'followers_display':'3M','post_count_display':540,'formats':dict(Counter(p['type'] for p in selected)),'primary_themes':dict(Counter(labels)),'total_likes':sum(p['likes'] for p in selected),'total_comments':sum(p['comments'] for p in selected),'median_likes':statistics.median(p['likes'] for p in selected),'median_comments':statistics.median(p['comments'] for p in selected),'grid_reels_with_plays':sum('plays' in p for p in selected),'grid_reels':sum(p['type']=='reel' for p in selected),'additional_reels_tab_examples':len(extras)}
metrics['median_follower_based_engagement_rate_pct']=round(statistics.median((p['likes']+p['comments'])/2815790*100 for p in selected),2)
write(out/'raw/gamerfleetog-2026-11-17.json',{'profile_url':'https://www.instagram.com/gamerfleetog/','observed_at_utc':observed,'collectors':['Instaloader 4.16','Obscura 0.2.7'],'main_timeline_candidate_count':len(grid_codes),'latest_30_main_timeline':selected,'additional_reels_tab_examples':extras,'summary':metrics})
brand=[
 ('samsung','Samsung India','Consumer electronics','disclosed_collaboration','DeopzBlJ9fO DeHQYPyp_T6 DdeFqrJpD6E','The captions use #Collaboration and promote Galaxy devices or Smart Switch.'),
 ('lenovo','Lenovo Legion India','Gaming hardware','disclosed_collaboration','DeZIkL6JjzH','Caption disclosure and an Instagram co-author field identify Lenovo Legion India.'),
 ('amazon','Amazon India','E-commerce','coauthored','DeO6poVJN85','Amazon News India co-authored a Great Indian Festival promotional Reel. Other brands listed in the offer are product mentions.'),
 ('amazon','Amazon India','E-commerce','retailer_mentioned','DeHQYPyp_T6','Amazon is named as the retailer in this Samsung offer.'),
 ('youtube','YouTube India','Creator platform','coauthored','Ddbe0z9FB_2','YouTube India published the carousel with GamerFleet as a co-author.'),
 ('joy','Joy Beautiful By Nature','Skincare','product_integration','Deg3TfWJ2O0','The caption tags Joy and describes its skincare in a sibling setup.'),
 ('pokemon','Pokémon','Gaming and events','event_association','Dd6YnFypfDK Dd1Hfwdpt1V Dc0Tk4uiRsw','The posts reference Pokémon and the event. The September 3 carousel thanks Pokémon.'),
 ('porsche','Porsche','Automotive','mentioned','DetxIOQJfmM DeCFRuBpee1 DdnbYJnJeLU','Captions tag Porsche in automotive or lifestyle content.'),
 ('mercedes','Mercedes','Automotive','mentioned','DfI8GIYJhes','Caption hashtag on a separate Reels-tab discovery.'),
 ('saregama','Saregama','Music','credited','Ddbe0z9FB_2','The carousel credits Saregama for music.'),
]
nodes=[{'id':'creator:gamerfleetog','type':'creator','display_name':'Anshu Bisht','handle':'gamerfleetog','platform':'instagram'}]
nodes.extend({'id':f'post:{p["shortcode"]}','type':'post','url':p['url'],'published_at':p['date_utc']} for p in selected+extras)
edges=[{'from':'creator:gamerfleetog','type':'published' if p['owner']=='gamerfleetog' else 'coauthored','to':f'post:{p["shortcode"]}','evidence':[p['url']]} for p in selected+extras]
for theme in dict.fromkeys(labels):
 nodes.append({'id':f'theme:{theme}','type':'theme','label':theme})
for p in selected:
 edges.append({'from':f'post:{p["shortcode"]}','type':'has_theme','to':f'theme:{p["primary_theme"]}','evidence':[p['url']],'evidence_kind':'caption_classification'})
for key,name,category,rel,codes,note in brand:
 id=f'brand:{key}'
 if not any(n['id']==id for n in nodes):nodes.append({'id':id,'type':'brand','name':name,'category':category})
 for c in codes.split():
  edges.append({'from':f'post:{c}','type':rel,'to':id,'evidence':[all_posts[c]['url']],'observed_at_utc':observed,'note':note})
nodeids={n['id'] for n in nodes}
assert len(nodeids)==len(nodes) and all(e['from'] in nodeids and e['to'] in nodeids for e in edges)
write(out/'graph.json',{'nodes':nodes,'edges':edges})
(out/'index.md').write_text('# Opraah creator wiki\n\n- [Anshu Bisht / GamerFleet](wiki/creators/gamerfleetog.md)\n- [Public Instagram evidence snapshot](raw/gamerfleetog-2026-11-17.json)\n- [Graph data](graph.json)\n- [Evidence rules](SCHEMA.md)\n')
(out/'SCHEMA.md').write_text('''# Evidence rules

Keep each dated collection in raw/. Link each derived claim to the source post. Use the roster vendor code as the stable creator identifier when a match is confirmed. Otherwise use a provisional platform handle and resolve it later.

Track the creator, post, brand and content theme as separate nodes. Keep disclosures, co-authors, product integrations, event associations, hashtags and credits as separate relationships. Record payment only when evidence establishes it. A hashtag or co-author field alone does not establish payment.

Store capture time and source with every metric. Missing metrics remain null. Report sample coverage with play totals and averages. Review ambiguous caption labels using frames or transcripts before adding more specific claims. When a new collection arrives, retain the old raw snapshot and update the linked wiki pages.
''')
lines=['# Anshu Bisht / GamerFleet','','[Instagram](https://www.instagram.com/gamerfleetog/) · [Raw evidence](../../raw/gamerfleetog-2026-11-17.json) · [Graph](../../graph.json)','','Main profile timeline: 30 posts, 25 August through 16 November 2026 UTC. The Reels tab returned four additional recent examples, stored separately.','']
for key,name,category,rel,codes,note in brand:
 lines.append(f'- [{name}](../brands/{key}.md) · {rel.replace("_"," ")}. {note}')
 text=f'\n- {rel.replace("_"," ")}. {note} '+ ' · '.join(f'[{c}]({all_posts[c]["url"]})' for c in codes.split())+'\n'
 page=out/'wiki/brands'/f'{key}.md'
 if len([b for b in brand[:brand.index((key,name,category,rel,codes,note))] if b[0]==key])==0:
  page.write_text(f'# {name}\n\n{category}. [GamerFleet](../creators/gamerfleetog.md)\n')
 with page.open('a') as f:f.write(text)
lines+=['','## Main timeline posts','','| Date (UTC) | Format | Theme | Likes | Comments | Plays |','|---|---|---|---:|---:|---:|']
for p in selected:
 lines.append(f'| [{p["date_utc"][:10]}]({p["url"]}) | {p["type"]} | {p["primary_theme"]} | {p["likes"]:,} | {p["comments"]:,} | {p.get("plays","")} |')
(out/'wiki/creators/gamerfleetog.md').write_text('\n'.join(lines)+'\n')
with zipfile.ZipFile(root/'gamerfleet-wiki-pilot.zip','w',zipfile.ZIP_DEFLATED) as z:
 for file in sorted(out.rglob('*')):
  if file.is_file():z.write(file,file.relative_to(out))
print(json.dumps(metrics,ensure_ascii=False));print('extras',[(p['shortcode'],p['plays']) for p in extras]);print(len(nodes),len(edges))
