"""Whitespace-token backend English stability audit; emits aggregate numbers only."""
from collections import Counter,defaultdict
from hashlib import sha256
from itertools import takewhile
from pathlib import Path
import argparse,json,re,unicodedata

def erasure(a,b):
    return len(a)-sum(1 for _ in takewhile(lambda p:p[0]==p[1],zip(a,b)))

def lexical(text):
    text=unicodedata.normalize("NFKC",text).casefold().replace("’","'")
    return tuple(re.findall(r"\d+(?:[.,]\d+)+|[^\W_]+(?:'[^\W_]+)*",text))

def audit(raw, tokenize=str.split):
    assert raw['status']=='complete' and raw['factor']==1
    groups=defaultdict(list)
    for e in raw['events']:
        chain=groups[e['id']]
        assert not chain or e['wall_s']>=chain[-1][0]
        chain.append((e['wall_s'],tuple(tokenize(e.get('english') or ''))))
    total=Counter()
    segments={e['id']:tuple(tokenize(e.get('english') or '')) for e in raw['segments']}
    assert groups.keys()==segments.keys()
    for ident,chain in groups.items():
        assert chain[-1][1]==segments[ident]
        first=next((i for i,(_,t) in enumerate(chain) if t),None)
        total['all_captions']+=1
        if first is None: total['never_nonempty']+=1;continue
        total['nonempty_captions']+=1
        total['final_tokens']+=len(segments[ident])
        seen=set()
        for (_,a),(_,b) in zip(chain[first:],chain[first+1:]):
            if a==b: continue
            k='disappearance' if not b else 'reappearance' if not a else 'append' if not erasure(a,b) else 'nonappend'
            seen.add(k)
            total[k+'_events']+=1
            total['erased_tokens']+=erasure(a,b)
        total['captions_any_change']+=bool(seen)
        total['captions_nonappend']+=bool('nonappend' in seen)
        total['captions_disappeared']+=bool('disappearance' in seen)
        total['captions_only_append']+=seen=={'append'}
    result=dict(sorted(total.items()))
    for k in ['append_events','nonappend_events','disappearance_events','reappearance_events','never_nonempty']:result.setdefault(k,0)
    result['normalized_erasure']=total['erased_tokens']/total['final_tokens']
    return result
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('paths',nargs='+',type=Path);a=p.parse_args()
    assert erasure(('the','model'),('the','model','works'))==0
    assert erasure(('it','can','help'),('it',"can't",'help'))==2
    assert erasure(('text',),())==1
    out=[]
    for i,path in enumerate(a.paths,1):
        data=path.read_bytes()
        raw=json.loads(data)
        out.append(dict(trace=i,source_sha256=sha256(data).hexdigest(),whitespace=audit(raw),lexical_sensitivity=audit(raw,lexical)))
    print(json.dumps(out,indent=2))
