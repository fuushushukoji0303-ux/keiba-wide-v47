# -*- coding: utf-8 -*-
"""v52.7 research-only strategy comparisons; prior-dated finishes only."""
from collections import defaultdict
from historical_replay import load, racekey, num, COURSES

STRATEGIES = ('3頭ボックス','4頭ボックス','軸流し2点','軸流し3点','強軸のみ2点')

def summarize(rows):
    races=len(rows)
    hits=sum(r['payout']>0 for r in rows)
    stake=sum(r['stake'] for r in rows)
    payout=sum(r['payout'] for r in rows)
    return dict(races=races,hits=hits,stake=stake,payout=payout,profit=payout-stake,
                roi=round(payout/stake*100,1) if stake else None)

def compare(horse_blob,race_blob,pay_blob):
    horses=load(horse_blob,['競馬場','競走年月日','レース番号','馬番','馬名','着順'])
    races=load(race_blob,['競馬場','競走年月日','レース番号','頭数','距離'])
    pays=load(pay_blob,['競馬場','競走年月日','レース番号','ワイド組番1馬番1','ワイド組番1馬番2','ワイド払戻金1（円）'])
    rm={racekey(r):r for r in races if r['競馬場'] in COURSES}
    pm={racekey(p):p for p in pays if p['競馬場'] in COURSES}
    by=defaultdict(list)
    for h in horses:
        if h['競馬場'] in COURSES: by[racekey(h)].append(h)
    history=defaultdict(list)
    output=[]
    skipped=defaultdict(int)
    for key in sorted(by):
        day,course,race=key
        if key not in rm or key not in pm:
            skipped['レース情報・払戻情報なし']+=1
            continue
        ranked=[]
        for h in by[key]:
            name=h['馬名'].strip(); no=num(h['馬番'])
            prior=[(d,rank,field) for d,rank,field in history[name] if d<day]
            if no>0 and prior:
                last=prior[-3:]
                score=sum((field-rank)/max(field-1,1) for _,rank,field in last)/len(last)
                ranked.append((score,len(last),no))
        ranked.sort(key=lambda x:(-x[0],-x[1],x[2]))
        p=pm[key]; payouts={}
        for i in (1,2,3):
            a=num(p.get(f'ワイド組番{i}馬番1'))
            b=num(p.get(f'ワイド組番{i}馬番2'))
            yen=num(p.get(f'ワイド払戻金{i}（円）'))
            if a>0 and b>0 and a!=b and yen>=100:
                payouts[tuple(sorted((a,b)))]=yen
        if not payouts:
            skipped['ワイド払戻なし']+=1
        elif len(ranked)<3:
            skipped['過去出走情報不足']+=1
        else:
            numbers=[x[2] for x in ranked]
            def pairs(nums):
                return [(min(a,b),max(a,b)) for i,a in enumerate(nums) for b in nums[i+1:]]
            plans={
                '3頭ボックス':pairs(numbers[:3]),
                '軸流し2点':[(min(numbers[0],n),max(numbers[0],n)) for n in numbers[1:3]],
            }
            if len(numbers)>=4:
                plans['4頭ボックス']=pairs(numbers[:4])
                plans['軸流し3点']=[(min(numbers[0],n),max(numbers[0],n)) for n in numbers[1:4]]
            # The cutoff is a predeclared research hypothesis, not tuned to these results.
            if ranked[0][0]-ranked[1][0]>=0.15:
                plans['強軸のみ2点']=plans['軸流し2点']
            for strategy,combos in plans.items():
                output.append(dict(date=day,course=course,race=race,strategy=strategy,
                                   stake=len(combos)*100,payout=sum(payouts.get(c,0) for c in combos)))
        field=num(rm[key].get('頭数')) or len(by[key])
        for h in by[key]:
            rank=num(h.get('着順'))
            if 1<=rank<=field and field>=3:
                history[h['馬名'].strip()].append((day,rank,field))
    dates=sorted({r['date'] for r in output})
    split=int(len(dates)*0.7)
    earlier=set(dates[:split]); later=set(dates[split:])
    strategy_summary={s:summarize([r for r in output if r['strategy']==s]) for s in STRATEGIES}
    course_summary={c:{s:summarize([r for r in output if r['course']==c and r['strategy']==s])
                       for s in STRATEGIES} for c in sorted(COURSES)}
    period_summary={period:{s:summarize([r for r in output if r['date'] in days and r['strategy']==s])
                             for s in STRATEGIES}
                    for period,days in [('前半70%',earlier),('後半30%',later)]}
    return dict(strategy=strategy_summary,course=course_summary,period=period_summary,
                skipped=dict(skipped),dates=len(dates),races_total=len(by),horse_rows=len(horses))
