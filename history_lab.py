# -*- coding: utf-8 -*-
"""v52.5: pre-race snapshot + historical wide payout CSV backtest.

No retroactive predictions are generated. Only timestamped pre-race snapshots
can enter the evaluated cohort. This module never fetches or scrapes websites.
"""
from __future__ import annotations

import csv
import io
import json
import math
import re
from datetime import datetime

RESULT_FIELDS = ['race_date','course','race','start_time','wide1','payout1','wide2','payout2','wide3','payout3','source','distance_m','runners','going']
PRED_FIELDS = ['race_date','course','race','mode','strategy','prediction_at'] + [f'combo{i}' for i in range(1,7)] + [f'stake{i}' for i in range(1,7)]
MAX_CSV_BYTES = 2_000_000
MAX_ROWS = 3000


def combo_norm(value):
    m = re.fullmatch(r'\s*(\d{1,2})\s*[-－ー―]\s*(\d{1,2})\s*', str(value or ''))
    if not m:
        raise ValueError('組合せは 3-8 のように入力してください')
    a, b = int(m.group(1)), int(m.group(2))
    if a < 1 or b < 1 or a == b:
        raise ValueError('馬番が不正です')
    return f'{min(a,b)}-{max(a,b)}'


def parse_day(v):
    try:
        day = datetime.strptime(str(v).strip(), '%Y-%m-%d')
        return day.strftime('%Y-%m-%d')
    except ValueError as exc:
        raise ValueError('日付は YYYY-MM-DD で入力してください') from exc


def parse_clock(v):
    try:
        return datetime.strptime(str(v).strip(), '%H:%M').strftime('%H:%M')
    except ValueError as exc:
        raise ValueError('発走時刻は 14:20 のように入力してください') from exc


def parse_datetime(v):
    try:
        return datetime.strptime(str(v).strip(), '%Y-%m-%d %H:%M').strftime('%Y-%m-%d %H:%M')
    except ValueError as exc:
        raise ValueError('予想記録時刻は YYYY-MM-DD HH:MM で入力してください') from exc


def read_csv(blob, required):
    if not blob or len(blob) > MAX_CSV_BYTES:
        raise ValueError('CSVは空でない2MB以下のファイルを選んでください')
    text = None
    for enc in ('utf-8-sig', 'cp932'):
        try:
            text = blob.decode(enc)
            break
        except UnicodeDecodeError:
            pass
    if text is None:
        raise ValueError('CSVはUTF-8またはShift-JISで保存してください')
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or not set(required).issubset(set(reader.fieldnames)):
        raise ValueError('CSVの列名がテンプレートと異なります')
    rows = []
    for n, row in enumerate(reader, 2):
        if n > MAX_ROWS + 1:
            raise ValueError('1回につき3000行までにしてください')
        if None in row:
            raise ValueError(f'{n}行目: 列数が合いません')
        if not any(str(v or '').strip() for v in row.values()):
            continue
        rows.append((n, row))
    return rows


def common(row, courses):
    day = parse_day(row.get('race_date',''))
    course = str(row.get('course','')).strip()
    if course not in courses:
        raise ValueError('対応していない競馬場です（ばんえいは対象外）')
    try:
        race = int(row.get('race',''))
    except (ValueError, TypeError) as exc:
        raise ValueError('レース番号は整数で入力してください') from exc
    if not 1 <= race <= 14:
        raise ValueError('レース番号は1～14で入力してください')
    return day, course, race


def parse_results(blob, courses):
    result = []
    for lineno, row in read_csv(blob, RESULT_FIELDS[:10]):
        try:
            day, course, race = common(row, courses)
            start = parse_clock(row.get('start_time',''))
            payouts = {}
            for i in (1,2,3):
                c = str(row.get(f'wide{i}','') or '').strip()
                p = str(row.get(f'payout{i}','') or '').replace(',','').strip()
                if not c and not p:
                    continue
                pair = combo_norm(c)
                if pair in payouts:
                    raise ValueError('同じワイド組合せが重複しています')
                if not p.isdecimal() or not 100 <= int(p) <= 10_000_000:
                    raise ValueError('払戻金は100円あたりの正の整数（100円以上）です')
                payouts[pair] = int(p)
            if not payouts:
                raise ValueError('少なくとも1組のワイド払戻が必要です')
            source = str(row.get('source','') or '利用者CSV').strip()[:100]
            dist = str(row.get('distance_m','') or '').strip()
            runners = str(row.get('runners','') or '').strip()
            going = str(row.get('going','') or '').strip()[:12]
            if dist and (not dist.isdecimal() or not 100 <= int(dist) <= 5000):
                raise ValueError('距離は100～5000mの整数です')
            if runners and (not runners.isdecimal() or not 2 <= int(runners) <= 20):
                raise ValueError('頭数は2～20頭です')
            result.append((day, course, race, start, json.dumps(payouts, ensure_ascii=False),
                           source, int(dist) if dist else 0, int(runners) if runners else 0, going))
        except ValueError as exc:
            raise ValueError(f'{lineno}行目: {exc}') from exc
    return result


def parse_predictions(blob, courses):
    result = []
    for lineno, row in read_csv(blob, PRED_FIELDS):
        try:
            day, course, race = common(row, courses)
            at = parse_datetime(row.get('prediction_at',''))
            if at[:10] > day:
                raise ValueError('予想記録日がレース日より後です')
            mode = str(row.get('mode','') or '').strip()[:30] or 'バランス'
            strategy = str(row.get('strategy','') or '').strip()[:40]
            if not strategy:
                raise ValueError('strategy（予想方式）を入力してください')
            bets = []
            seen = set()
            for i in range(1,7):
                c = str(row.get(f'combo{i}','') or '').strip()
                s = str(row.get(f'stake{i}','') or '').strip()
                if not c and not s:
                    continue
                pair = combo_norm(c)
                if pair in seen:
                    raise ValueError('同じ組合せが重複しています')
                if not s.isdecimal() or not 100 <= int(s) <= 10000 or int(s) % 100:
                    raise ValueError('購入額は100円単位で100～10000円です')
                bets.append({'combo':pair, 'amount':int(s)})
                seen.add(pair)
            if not bets:
                raise ValueError('少なくとも1組の買い目が必要です')
            result.append((day, course, race, mode, strategy, at, json.dumps(bets, ensure_ascii=False)))
        except ValueError as exc:
            raise ValueError(f'{lineno}行目: {exc}') from exc
    return result


def snapshot_outcome(recorded_at, day, start_time, bets, payouts):
    """Only snapshots strictly before actual race start are evaluated."""
    try:
        ts = datetime.strptime(recorded_at[:16], '%Y-%m-%d %H:%M')
        start = datetime.strptime(f'{day} {start_time}', '%Y-%m-%d %H:%M')
        if ts >= start:
            return None
        if not isinstance(bets, list) or not bets:
            return None
        total_bet = total_return = 0
        hit = False
        seen = set()
        for item in bets:
            pair = combo_norm(item.get('combo',''))
            amount = int(item.get('amount',0))
            if pair in seen or amount < 100 or amount % 100 or amount > 10000:
                return None
            seen.add(pair)
            total_bet += amount
            payout = int(payouts.get(pair,0))
            total_return += payout * amount // 100
            hit = hit or payout > 0
        return {'bet':total_bet,'return':total_return,'hit':hit}
    except (ValueError, TypeError, KeyError):
        return None


def group_stats(rows):
    staked = sum(r['bet'] for r in rows)
    returned = sum(r['return'] for r in rows)
    return {'races':len(rows),'hits':sum(bool(r['hit']) for r in rows),
            'bet':staked,'return':returned,'profit':returned-staked,
            'roi':round(100*returned/staked,1) if staked else None,
            'hit_rate':round(100*sum(bool(r['hit']) for r in rows)/len(rows),1) if rows else None}


def time_split(rows):
    """Split by unique race dates to avoid same-day leakage across sets."""
    days = sorted({r['date'] for r in rows})
    if len(days) < 5:
        return {'early': [], 'later': [], 'enough': False}
    cutoff = max(1, min(len(days)-1, math.floor(len(days)*0.7)))
    train_days = set(days[:cutoff])
    return {'early':[r for r in rows if r['date'] in train_days],
            'later':[r for r in rows if r['date'] not in train_days], 'enough': True}
