# -*- coding: utf-8 -*-
"""v53.2: Larger-sample independent selection, research only."""
from collections import defaultdict
from v53_batch_research import research, stat, STRATEGIES

MIN_TRAIN=50
MIN_TEST=20
MIN_DATES=8

def study(horse_blob,race_blob,pay_blob,limit=1000):
    base=research(horse_blob,race_blob,pay_blob,limit=limit,include_rows=True)
    grouped=defaultdict(list)
    for row in base['eligible_rows']:
        grouped[row['course']].append(row)
    results=[]
    for course in sorted(grouped):
        rows=grouped[course]
        days=sorted({r['date'] for r in rows})
        cut=max(1,int(len(days)*.7)) if len(days)>1 else len(days)
        train_days=set(days[:cut]); test_days=set(days[cut:])
        train=[r for r in rows if r['date'] in train_days]
        test=[r for r in rows if r['date'] in test_days]
        candidates={}
        for name in STRATEGIES[:4]:
            candidates[name]={
                'train':stat([r['plans'][name] for r in train if name in r['plans']]),
                'test':stat([r['plans'][name] for r in test if name in r['plans']])}
        possible=[(name,st['train']) for name,st in candidates.items()
                  if st['train']['races']>=MIN_TRAIN and (st['train']['roi'] or 0)>=100]
        chosen=max(possible,key=lambda x:(x[1]['roi'],x[1]['races'],x[0]))[0] if possible else None
        if len(days)<MIN_DATES or len(test_days)==0:
            chosen=None; status='見送り（日付が不足）'
        elif not chosen:
            status='見送り（前半の件数・回収率不足）'
        else:
            st=candidates[chosen]['test']
            status=('研究候補（後半基準通過）' if st['races']>=MIN_TEST and (st['roi'] or 0)>=100
                    else '見送り（後半未通過・件数不足）')
        results.append({'course':course,'dates':len(days),'train_days':len(train_days),
                        'test_days':len(test_days),'races':len(rows),'train_races':len(train),
                        'test_races':len(test),'chosen':chosen,'status':status,'strategies':candidates})
    return {'base':base,'courses':results,'min_train':MIN_TRAIN,'min_test':MIN_TEST,'min_dates':MIN_DATES}
