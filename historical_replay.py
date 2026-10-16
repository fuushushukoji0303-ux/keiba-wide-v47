# -*- coding: utf-8 -*-
"""NAR official CSV chronological research replay. NOT the production ability index.
Only prior-dated finishes are available when predicting a later race.
"""
import csv
import io
from collections import defaultdict
from datetime import datetime

MAX_UPLOAD = 9_000_000
COURSES = {'門別','盛岡','水沢','浦和','船橋','大井','川崎','金沢','笠松','名古屋','園田','姫路','高知','佐賀'}


def load(blob, required):
    if not blob or len(blob)>MAX_UPLOAD:
        raise ValueError('各CSVは9MB以下にしてください')
    try:
        text=blob.decode('utf-8-sig')
    except UnicodeError:
        try: text=blob.decode('cp932')
        except UnicodeError: raise ValueError('CSVの文字コードはUTF-8またはShift-JISにしてください')
    reader=csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or not set(required).issubset(reader.fieldnames):
        raise ValueError('公式CSVの列名が合いません: '+', '.join(sorted(set(required)-set(reader.fieldnames))))
    return list(reader)


def num(v):
    try:return int(str(v).strip())
    except (TypeError, ValueError):return 0


def racekey(r):
    return (str(r['競走年月日']).strip(), str(r['競馬場']).strip(), num(r['レース番号']))


def replay(horse_blob, race_blob, pay_blob):
    horses=load(horse_blob, ['競馬場','競走年月日','レース番号','馬番','馬名','着順'])
    races=load(race_blob, ['競馬場','競走年月日','レース番号','頭数','距離'])
    pays=load(pay_blob, ['競馬場','競走年月日','レース番号','ワイド組番1馬番1','ワイド組番1馬番2','ワイド払戻金1（円）'])
    race_map={racekey(r):r for r in races if r['競馬場'] in COURSES}
    pay_map={racekey(r):r for r in pays if r['競馬場'] in COURSES}
    by_race=defaultdict(list)
    for h in horses:
        if h['競馬場'] in COURSES:
            by_race[racekey(h)].append(h)
    history=defaultdict(list)
    daily=defaultdict(lambda:{'races':0,'bets':0,'hits':0,'stake':0,'payout':0})
    rows=[]
    skipped=defaultdict(int)
    for key in sorted(by_race):
        day,course,race=key
        if key not in race_map or key not in pay_map:
            skipped['no_result']+=1
            continue
        rmeta=race_map[key]
        candidates=[]
        for h in by_race[key]:
            horse=h['馬名'].strip()
            horse_no=num(h['馬番'])
            past=history[horse]
            # Only results with STRICTLY EARLIER race dates are available.
            past=[(d,rank,field) for d,rank,field in past if d<day]
            if horse_no>0 and past:
                recent=past[-3:]
                # Finish percentile, 1 best, 0 worst. One-race history is provisional.
                score=sum((field-rank)/max(field-1,1) for _,rank,field in recent)/len(recent)
                candidates.append((score,len(recent),horse_no))
        candidates.sort(key=lambda x:(-x[0],-x[1],x[2]))
        if len(candidates)<3:
            skipped['insufficient_prior_runs']+=1
        else:
            picks=sorted(x[2] for x in candidates[:3])
            combos={(picks[0],picks[1]),(picks[0],picks[2]),(picks[1],picks[2])}
            payout={}
            p=pay_map[key]
            for i in (1,2,3):
                a=num(p.get(f'ワイド組番{i}馬番1'))
                b=num(p.get(f'ワイド組番{i}馬番2'))
                yen=num(p.get(f'ワイド払戻金{i}（円）'))
                if a>0 and b>0 and a!=b and yen>=100:
                    payout[tuple(sorted((a,b)))]=yen
            if not payout:
                skipped['no_wide_payout']+=1
            else:
                won=sum(payout.get(c,0) for c in combos)
                stat=daily[day]
                stat['races']+=1;stat['bets']+=3;stat['stake']+=300
                stat['payout']+=won;stat['hits']+=int(won>0)
                rows.append({'date':day,'course':course,'race':race,'picks':','.join(f'{a}-{b}' for a,b in sorted(combos)),'won':won})
        # Only after scoring this race, add its FINISHED results to the past-data pool.
        # Data from a different race on the same day is excluded by the strict date check.
        field=num(rmeta.get('頭数')) or len(by_race[key])
        for h in by_race[key]:
            rank=num(h.get('着順'))
            if 1<=rank<=field and field>=3:
                history[h['馬名'].strip()].append((day,rank,field))
    days=sorted(daily)
    split=int(len(days)*0.7)
    def agg(ds):
        s={k:sum(daily[d][k] for d in ds) for k in ('races','bets','hits','stake','payout')}
        s['profit']=s['payout']-s['stake']
        s['roi']=round(s['payout']*100/s['stake'],1) if s['stake'] else None
        return s
    return {'all':agg(days),'earlier':agg(days[:split]),'later':agg(days[split:]),
            'dates':len(days),'skipped':dict(skipped),'races_total':len(by_race),
            'horse_rows':len(horses),'rows':rows}
