# -*- coding: utf-8 -*-
"""Research-only course strategy selection. 70/30 per track by race date.
Never use holdout results to choose a strategy.
"""
from collections import defaultdict
from v53_batch_research import research, stat, STRATEGIES

MIN_TRAIN=15
MIN_TEST=7

def study(horse_blob,race_blob,pay_blob,limit=300):
    base=research(horse_blob,race_blob,pay_blob,limit=limit,include_rows=True)
    by_course=defaultdict(list)
    for row in base['eligible_rows']:
        by_course[row['course']].append(row)
    result=[]
    for course in sorted(by_course):
        rows=by_course[course]
        days=sorted({r['date'] for r in rows})
        cut=max(1,int(len(days)*.7)) if len(days)>1 else 1
        train_days=set(days[:cut]);test_days=set(days[cut:])
        train=[r for r in rows if r['date'] in train_days]
        test=[r for r in rows if r['date'] in test_days]
        scores={}
        for s in STRATEGIES[:4]:
            a=stat([r['plans'][s] for r in train if s in r['plans']])
            b=stat([r['plans'][s] for r in test if s in r['plans']])
            scores[s]={'train':a,'test':b}
        candidates=[(s,v['train']) for s,v in scores.items() if v['train']['races']>=MIN_TRAIN and (v['train']['roi'] or 0)>=100]
        chosen=max(candidates,key=lambda x:(x[1]['roi'],x[1]['races'],x[0]))[0] if candidates else None
        status='見送り（前半の件数・回収率不足）'
        if chosen:
            final=scores[chosen]['test']
            status='研究候補（後半基準通過）' if final['races']>=MIN_TEST and (final['roi'] or 0)>=100 else '見送り（後半未通過・データ不足）'
        if not test_days: status='見送り（別日付の後半検証なし）'
        result.append({'course':course,'dates':len(days),'all_races':len(rows),
                       'train_races':len(train),'test_races':len(test),
                       'chosen':chosen,'status':status,'strategies':scores})
    return {'base':base,'courses':result,'min_train':MIN_TRAIN,'min_test':MIN_TEST}
