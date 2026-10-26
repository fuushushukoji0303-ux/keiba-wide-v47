# -*- coding: utf-8 -*-
"""v53.0: NAR CSV-based historical strategy research; not production indexes.
All horse scores use strictly older race dates. Test portion never chooses strategies.
"""
from collections import defaultdict
from historical_replay import racekey, num, COURSES
import csv, io

def load(blob,required):
    if not blob or len(blob)>25_000_000:
        raise ValueError("各CSVは25MB以下にしてください")
    content=None
    for codec in ("utf-8-sig","cp932"):
        try: content=blob.decode(codec);break
        except UnicodeDecodeError: pass
    if content is None: raise ValueError("CSVの文字コードが不明です")
    reader=csv.DictReader(io.StringIO(content))
    if not reader.fieldnames or not set(required).issubset(reader.fieldnames):
        raise ValueError("CSV列が不足しています: "+", ".join(set(required)-set(reader.fieldnames or [])))
    return list(reader)

STRATEGIES=('軸流し2点','軸流し3点','3頭ボックス','4頭ボックス','強軸2点（指数差0.15以上）')

def stat(rows):
    n=len(rows); stake=sum(r['stake'] for r in rows); payout=sum(r['payout'] for r in rows)
    return {'races':n,'hits':sum(r['payout']>0 for r in rows),'stake':stake,'payout':payout,
            'profit':payout-stake,'roi':round(100*payout/stake,1) if stake else None}

def research(horse_blob,race_blob,pay_blob,limit=100,include_rows=False):
    if limit not in (100,300): raise ValueError('検証数は100か300を選んでください')
    horses=load(horse_blob,['競馬場','競走年月日','レース番号','馬番','馬名','着順'])
    races=load(race_blob,['競馬場','競走年月日','レース番号','頭数','距離'])
    pays=load(pay_blob,['競馬場','競走年月日','レース番号','ワイド組番1馬番1','ワイド組番1馬番2','ワイド払戻金1（円）'])
    rm={racekey(r):r for r in races if r['競馬場'] in COURSES}
    pm={racekey(r):r for r in pays if r['競馬場'] in COURSES}
    by=defaultdict(list)
    for h in horses:
        if h['競馬場'] in COURSES: by[racekey(h)].append(h)
    history=defaultdict(list); eligible=[]; skip=defaultdict(int)
    for key in sorted(by):
        day,course,race=key
        race_horses=by[key]
        if key not in rm or key not in pm:
            skip['レース情報・払戻なし']+=1
        else:
            ranked=[]
            for h in race_horses:
                name=h['馬名'].strip(); no=num(h['馬番'])
                prior=[(rank,field) for d,rank,field in history[name] if d<day]
                if no>0 and prior:
                    recent=prior[-3:]
                    score=sum((field-rank)/max(field-1,1) for rank,field in recent)/len(recent)
                    ranked.append((score,len(recent),no))
            ranked.sort(key=lambda x:(-x[0],-x[1],x[2]))
            payout={}
            pay=pm[key]
            for i in (1,2,3):
                a=num(pay.get(f'ワイド組番{i}馬番1')); b=num(pay.get(f'ワイド組番{i}馬番2'))
                yen=num(pay.get(f'ワイド払戻金{i}（円）'))
                if a>0 and b>0 and a!=b and yen>=100:
                    payout[tuple(sorted((a,b)))]=yen
            if len(ranked)<3: skip['過去戦績がある馬不足']+=1
            elif not payout: skip['ワイド確定払戻なし']+=1
            else:
                nums=[x[2] for x in ranked]
                def combo(a,b):return tuple(sorted((a,b)))
                def box(n):return [combo(a,b) for j,a in enumerate(nums[:n]) for b in nums[j+1:n]]
                plans={'軸流し2点':[combo(nums[0],x) for x in nums[1:3]],'3頭ボックス':box(3)}
                if len(nums)>=4:
                    plans['軸流し3点']=[combo(nums[0],x) for x in nums[1:4]]
                    plans['4頭ボックス']=box(4)
                if ranked[0][0]-ranked[1][0]>=0.15:
                    plans['強軸2点（指数差0.15以上）']=plans['軸流し2点']
                eligible.append({'date':day,'course':course,'race':race,'plans':{
                    k: {'date':day,'course':course,'race':race,'stake':len(v)*100,'payout':sum(payout.get(p,0) for p in v)}
                    for k,v in plans.items()}})
        # Update AFTER scoring; only dates < target day can influence score.
        field=num(rm.get(key,{}).get('頭数')) or len(race_horses)
        for h in race_horses:
            rank=num(h.get('着順')); name=h['馬名'].strip()
            if 1<=rank<=field and field>=3 and name:
                history[name].append((day,rank,field))
    selected=eligible[-limit:]
    # One date must belong entirely to train or test: no leakage across time-split.
    days=sorted({r['date'] for r in selected})
    split=max(1,int(len(days)*0.7)) if len(days)>1 else len(days)
    training=set(days[:split]); testing=set(days[split:]); groups={}
    for portion,valid_days in [('全体',set(days)),('前半70%（比較用）',training),('後半30%（未使用検証）',testing)]:
        groups[portion]={s:stat([r['plans'][s] for r in selected if r['date'] in valid_days and s in r['plans']]) for s in STRATEGIES}
    # The candidate is nominated only using training. The test is reported, NEVER used to revise.
    choices=[(s,groups['前半70%（比較用）'][s]) for s in STRATEGIES if s!='強軸2点（指数差0.15以上）' and groups['前半70%（比較用）'][s]['races']>=20]
    chosen=max(choices,key=lambda p:(p[1]['roi'] or 0),default=(None,None))[0]
    courses={c:{s:stat([r['plans'][s] for r in selected if r['course']==c and s in r['plans']]) for s in STRATEGIES[:4]} for c in sorted({r['course'] for r in selected})}
    return {'limit':limit,'available':len(eligible),'selected':len(selected),'dates':len(days),
            'train_days':len(training),'test_days':len(testing),'groups':groups,'courses':courses,
            'candidate':chosen,'skipped':dict(skip),'source_rows':len(horses),
            'eligible_rows':selected if include_rows else []}
