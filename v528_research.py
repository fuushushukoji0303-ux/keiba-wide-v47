# -*- coding: utf-8 -*-
"""v52.8: held-out venue strategy selection. Research only; no real wagers."""
from collections import defaultdict
from historical_replay import load, racekey, num, COURSES
from strategy_compare import STRATEGIES, summarize

MIN_TRAIN = 30
MIN_TEST = 15


def study(horse_blob, race_blob, pay_blob):
    horses = load(horse_blob, ['競馬場','競走年月日','レース番号','馬番','馬名','着順'])
    races = load(race_blob, ['競馬場','競走年月日','レース番号','頭数','距離'])
    pays = load(pay_blob, ['競馬場','競走年月日','レース番号','ワイド組番1馬番1','ワイド組番1馬番2','ワイド払戻金1（円）'])
    rm = {racekey(r): r for r in races if r['競馬場'] in COURSES}
    pm = {racekey(r): r for r in pays if r['競馬場'] in COURSES}
    by = defaultdict(list)
    for h in horses:
        if h['競馬場'] in COURSES:
            by[racekey(h)].append(h)
    history = defaultdict(list)
    output = []
    skipped = defaultdict(int)
    for key in sorted(by):
        day, course, race = key
        if key not in rm or key not in pm:
            skipped['レース情報・払戻情報なし'] += 1
            continue
        ranked = []
        for h in by[key]:
            name, no = h['馬名'].strip(), num(h['馬番'])
            prior = [(d, rank, field) for d, rank, field in history[name] if d < day]
            if no > 0 and prior:
                recent = prior[-3:]
                score = sum((field-rank)/max(field-1, 1) for _, rank, field in recent)/len(recent)
                ranked.append((score, len(recent), no))
        ranked.sort(key=lambda x: (-x[0], -x[1], x[2]))
        payouts = {}
        for i in (1, 2, 3):
            p = pm[key]
            a, b = num(p.get(f'ワイド組番{i}馬番1')), num(p.get(f'ワイド組番{i}馬番2'))
            yen = num(p.get(f'ワイド払戻金{i}（円）'))
            if a > 0 and b > 0 and a != b and yen >= 100:
                payouts[tuple(sorted((a, b)))] = yen
        if not payouts:
            skipped['ワイド払戻なし'] += 1
        elif len(ranked) < 3:
            skipped['過去出走情報不足'] += 1
        else:
            nums = [x[2] for x in ranked]
            def pairs(seq):
                return [tuple(sorted((a,b))) for i,a in enumerate(seq) for b in seq[i+1:]]
            plans = {'3頭ボックス': pairs(nums[:3]),
                     '軸流し2点': [tuple(sorted((nums[0], n))) for n in nums[1:3]]}
            if len(nums) >= 4:
                plans['4頭ボックス'] = pairs(nums[:4])
                plans['軸流し3点'] = [tuple(sorted((nums[0], n))) for n in nums[1:4]]
            if ranked[0][0] - ranked[1][0] >= 0.15:
                plans['強軸のみ2点'] = plans['軸流し2点']
            distance = num(rm[key].get('距離'))
            field = num(rm[key].get('頭数')) or len(by[key])
            gap = ranked[0][0] - ranked[1][0]
            for strategy, combos in plans.items():
                output.append(dict(date=day, course=course, race=race, strategy=strategy,
                    stake=100*len(combos), payout=sum(payouts.get(c,0) for c in combos),
                    distance=distance, runners=field, gap=round(gap, 4)))
        field = num(rm[key].get('頭数')) or len(by[key])
        for h in by[key]:
            rank = num(h.get('着順'))
            if 1 <= rank <= field and field >= 3:
                history[h['馬名'].strip()].append((day, rank, field))
    # One global date cutoff, not a different cutoff for each venue.
    days = sorted({r['date'] for r in output})
    split = int(len(days)*0.7)
    early, late = set(days[:split]), set(days[split:])
    venue = {}
    for course in sorted(COURSES):
        train = {s: summarize([r for r in output if r['course']==course and r['date'] in early and r['strategy']==s]) for s in STRATEGIES}
        test = {s: summarize([r for r in output if r['course']==course and r['date'] in late and r['strategy']==s]) for s in STRATEGIES}
        eligible = [s for s in STRATEGIES if train[s]['races'] >= MIN_TRAIN and train[s]['roi'] is not None and train[s]['roi'] >= 100]
        # Selection uses training ONLY. Tie: fewer combinations then name.
        selected = sorted(eligible, key=lambda s: (-train[s]['roi'], train[s]['stake']/train[s]['races'], s))[0] if eligible else None
        verified = bool(selected and test[selected]['races'] >= MIN_TEST and test[selected]['roi'] is not None and test[selected]['roi'] >= 100)
        venue[course] = dict(selected=selected, verified=verified, train=train, test=test,
            decision=('検証通過（研究候補）' if verified else '見送り（未検証・基準未達）'))
    # Condition groups are descriptive ONLY, never used to optimize held-out selection.
    conditions = {}
    for label, pred in [('少頭数（8頭以下）',lambda r:r['runners']<=8),
                        ('多頭数（9頭以上）',lambda r:r['runners']>=9),
                        ('短距離（1400m以下）',lambda r:0<r['distance']<=1400),
                        ('中長距離（1401m以上）',lambda r:r['distance']>1400),
                        ('上位差大（0.15以上）',lambda r:r['gap']>=0.15),
                        ('上位差小（0.15未満）',lambda r:r['gap']<0.15)]:
        conditions[label] = {s:summarize([r for r in output if r['date'] in early and r['strategy']==s and pred(r)]) for s in STRATEGIES}
    return dict(venue=venue, conditions=conditions, days=len(days), train_days=len(early), test_days=len(late),
                skipped=dict(skipped), total=len(by), studied=len({(r['date'],r['course'],r['race']) for r in output}))
