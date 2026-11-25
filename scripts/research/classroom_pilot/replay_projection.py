"""Audit archived 1× event clocks and fixed-window projection; do not score WER."""
from bisect import bisect_left
from collections import defaultdict
from hashlib import file_digest
from math import floor, ceil
import argparse, json, re
from pathlib import Path

def q(v,p=.95):
    a=sorted(v);i=(len(a)-1)*p;j=floor(i);k=ceil(i)
    return a[j]+(a[k]-a[j])*(i-j)

def nd(v):
    return {'n':len(v),'p50_s':q(v,.5),'p95_s':q(v),'max_s':max(v)} if v else {'n':0}

def audit(d,unit=300):
    assert unit>0 and d['status']=='complete' and d['factor']==1
    assert d['input_frames']==d['consumed_frames'] and d['dropped']==0
    arrivals=d['arrivals']
    end=[x['end'] for x in arrivals]
    due=[x['wall_s'] for x in arrivals]
    assert end==sorted(set(end)) and due==sorted(due) and end[-1]==d['duration_s']
    def arrived(t):
        if t==0:return 0.
        return due[bisect_left(end,t)]
    events=defaultdict(list)
    def english(e): return ' '.join((e.get('english') or '').split())
    assert [e['wall_s'] for e in d['events']]==sorted(e['wall_s'] for e in d['events'])
    for e in d['events']:
        assert 0<=e['start']<=e['end']<=d['duration_s']
        assert e['end']<=e['audio_available_s']+.0001,e['id']
        events[e['id']].append(e)
    groups=sorted(d['segments'],key=lambda s:(s['start'],s['end']))
    traces=[];crossings=[];textchanges=[];cut_count=0
    boundaries=[i*unit for i in range(1,ceil(d['duration_s']/unit))]
    assert set(events)=={s['id'] for s in groups}
    for rank,s in enumerate(groups,1):
        es=events[s['id']]
        assert all(e['start']==s['start'] for e in es)
        assert english(es[-1])==english(s) and es[-1]['status']==s['status']
        values=[e for e in es if english(e)]
        if not values:continue
        first=values[0];last=first;prev=english(first);nrev=0
        for e in values[1:]:
            t=english(e)
            if t!=prev:last=e;nrev+=1
            prev=t
        lock=next((e for e in reversed(es) if e['status']=='locked'),None)
        a,b=s['start'],s['end']
        crossings_for_s=[x for x in boundaries if a<x<b]
        cut_count+=len(crossings_for_s)
        if crossings_for_s:
            crossings.append(dict(caption=rank,start_s=a,end_s=b,boundaries_s=crossings_for_s,caption_word_count=len(re.findall(r"\S+",english(s))),start_bin=int(a//unit),midpoint_bin=int(((a+b)/2)//unit)))
        textchanges.append(nrev)
        traces.append(dict(index=rank,first_from_start_audio=first['wall_s']-a,first_from_start_arrival=first['wall_s']-arrived(a),
            terminal_english_from_end_audio=last['wall_s']-b,terminal_english_from_end_arrival=last['wall_s']-arrived(b),
            lock_from_end_arrival=None if lock is None else lock['wall_s']-arrived(b),lock_after_last_english=None if lock is None else lock['wall_s']-last['wall_s']))
    return dict(duration_s=d['duration_s'],source_range_s=d['source_range'],input_complete=True,dropped=0,
                segments=len(groups),nonempty_english=len(traces),fixed_bin_seconds=unit,boundaries=len(boundaries),boundaries_crossed=cut_count,
                crossed_caption_tokens=sum(x['caption_word_count'] for x in crossings),start_vs_midpoint_reassigned=sum(x['start_bin']!=x['midpoint_bin'] for x in crossings),
                crossing_rows=crossings,timing={k:nd([x[k] for x in traces if x[k] is not None]) for k in traces[0] if k!='index'},
                captions_with_english_revisions=sum(n>0 for n in textchanges),caption_timing=traces)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('input',nargs='+',type=Path);p.add_argument('--unit',type=int,default=300);a=p.parse_args()
    rs=[]
    for i,f in enumerate(a.input,1):
        with f.open('rb') as r: digest=file_digest(r,'sha256').hexdigest()
        rs.append(dict(fixture=f'trace-{i}',archived_result_sha256=digest,**audit(json.loads(f.read_text()),a.unit)))
    print(json.dumps(dict(kind='archived public classroom replay; no new inference or WER; research backend timestamps, not screen render',fixtures=rs),indent=2))
