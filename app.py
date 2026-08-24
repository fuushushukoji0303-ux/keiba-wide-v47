# -*- coding: utf-8 -*-
"""
地方競馬ワイド投票管理 v47.1 - スマホ完全版

主な追加:
- NAR公式サイトから当日のワイドオッズ・単勝/複勝データを取得
- PC版v44のルールをベースに「堅め / バランス / 穴狙い」の3点候補を算出
- レース参考ランク（S+/S/S-/A/B/見送り）
- 3点候補をホーム画面へ自動入力
- 条件の良いレースを「今日の勝負レース」に保存
- 購入記録・的中/ハズレ・払戻・収支管理
- SPAT4は公式サイトを開き、最終投票は利用者自身が行う

注意:
- 3点候補・参考ランクは市場オッズを使ったルールベースの参考情報です。
  的中や利益を保証するものではありません。
- SPAT4のログイン情報は保存しません。
"""
from __future__ import annotations

import html
import os
import re
import sqlite3
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser
from pathlib import Path

from flask import Flask, request, redirect, url_for

JST = timezone(timedelta(hours=9))
APP_TITLE = "地方競馬 ワイド投票管理 v47.1"
DAILY_LIMIT = 3000
DEFAULT_BET = 300
SPAT4_URL = "https://www.spat4.jp/keiba/pc"
NAR_BASE_URL = "https://www.keiba.go.jp/KeibaWeb/TodayRaceInfo"

NAR_COURSE_CODES = {
    "門別": 36, "盛岡": 10, "水沢": 11, "浦和": 18, "船橋": 19,
    "大井": 20, "川崎": 21, "金沢": 22, "笠松": 23, "名古屋": 24,
    "園田": 27, "姫路": 28, "高知": 31, "佐賀": 32,
}

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", str(BASE_DIR / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "wide_v47.sqlite3"

app = Flask(__name__)


def now():
    return datetime.now(JST)


def today():
    return now().strftime("%Y-%m-%d")


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS drafts(
            id INTEGER PRIMARY KEY CHECK(id=1),
            saved_at TEXT, course TEXT, race TEXT,
            wide1 TEXT, odds1 REAL, amount1 INTEGER,
            wide2 TEXT, odds2 REAL, amount2 INTEGER,
            wide3 TEXT, odds3 REAL, amount3 INTEGER
        );

        CREATE TABLE IF NOT EXISTS purchases(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            race_date TEXT NOT NULL,
            course TEXT NOT NULL,
            race TEXT NOT NULL,
            bets TEXT NOT NULL,
            total_bet INTEGER NOT NULL,
            result TEXT NOT NULL DEFAULT '未確定',
            return_amount INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS picks(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            race_date TEXT NOT NULL,
            saved_at TEXT NOT NULL,
            course TEXT NOT NULL,
            race TEXT NOT NULL,
            mode TEXT NOT NULL,
            grade TEXT NOT NULL,
            score INTEGER NOT NULL,
            candidate1 TEXT DEFAULT '',
            candidate2 TEXT DEFAULT '',
            candidate3 TEXT DEFAULT '',
            UNIQUE(race_date, course, race, mode)
        );
        """)


init_db()


def clean_combo(v):
    return (v or "").strip().replace("－", "-").replace("ー", "-").replace("―", "-")


def to_int(v, default=0):
    try:
        return int(float(str(v).replace(",", "").strip()))
    except Exception:
        return default


def to_float(v, default=0.0):
    try:
        return float(str(v).replace(",", "").strip())
    except Exception:
        return default


def get_draft():
    with db() as con:
        row = con.execute("SELECT * FROM drafts WHERE id=1").fetchone()
    return dict(row) if row else {}


def write_draft(vals):
    with db() as con:
        con.execute("""
        INSERT INTO drafts
        (id,saved_at,course,race,wide1,odds1,amount1,wide2,odds2,amount2,wide3,odds3,amount3)
        VALUES(1,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
        saved_at=excluded.saved_at,course=excluded.course,race=excluded.race,
        wide1=excluded.wide1,odds1=excluded.odds1,amount1=excluded.amount1,
        wide2=excluded.wide2,odds2=excluded.odds2,amount2=excluded.amount2,
        wide3=excluded.wide3,odds3=excluded.odds3,amount3=excluded.amount3
        """, (
            now().strftime("%Y-%m-%d %H:%M:%S"),
            vals.get("course", ""), vals.get("race", ""),
            vals.get("wide1", ""), vals.get("odds1", 0), vals.get("amount1", 0),
            vals.get("wide2", ""), vals.get("odds2", 0), vals.get("amount2", 0),
            vals.get("wide3", ""), vals.get("odds3", 0), vals.get("amount3", 0),
        ))
    return get_draft()


def save_draft(form):
    vals = {
        "course": form.get("course", "").strip(),
        "race": form.get("race", "").strip(),
    }
    for i in range(1, 4):
        vals[f"wide{i}"] = clean_combo(form.get(f"wide{i}", ""))
        vals[f"odds{i}"] = to_float(form.get(f"odds{i}", ""), 0.0)
        amt = to_int(form.get(f"amount{i}", ""), 0)
        vals[f"amount{i}"] = max(100, amt) if vals[f"wide{i}"] else 0
    return write_draft(vals)


def draft_bets(d):
    bets = []
    for i in range(1, 4):
        combo = (d.get(f"wide{i}") or "").strip()
        if not combo:
            continue
        odds = float(d.get(f"odds{i}") or 0)
        amount = int(d.get(f"amount{i}") or DEFAULT_BET)
        bets.append({
            "combo": combo,
            "odds": odds,
            "amount": amount,
            "expected": round(amount * odds) if odds else 0,
        })
    return bets


def summary():
    with db() as con:
        rows = con.execute(
            "SELECT * FROM purchases WHERE race_date=?", (today(),)
        ).fetchall()
    used = sum(int(r["total_bet"]) for r in rows)
    ret = sum(int(r["return_amount"]) for r in rows if r["result"] == "的中")
    return {
        "used": used,
        "remaining": max(0, DAILY_LIMIT - used),
        "return": ret,
        "profit": ret - used,
    }


class SimpleTableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.current_row = None
        self.current_cell = None
        self.cell_parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.current_row = []
        elif tag in ("td", "th") and self.current_row is not None:
            self.current_cell = tag
            self.cell_parts = []

    def handle_data(self, data):
        if self.current_cell is not None:
            self.cell_parts.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.current_cell is not None:
            cell_text = " ".join(
                " ".join(self.cell_parts).replace("\xa0", " ").split()
            )
            self.current_row.append(cell_text)
            self.current_cell = None
            self.cell_parts = []
        elif tag == "tr" and self.current_row is not None:
            if self.current_row:
                self.rows.append(self.current_row)
            self.current_row = None


def nar_fetch(url, timeout=15):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
                "AppleWebKit/605.1.15 Version/18.0 Mobile Safari/604.1"
            )
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as res:
        raw = res.read()

    for enc in ("utf-8", "cp932", "shift_jis"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", errors="replace")


def nar_date_text():
    return now().strftime("%Y/%m/%d")


def nar_url(page_name, course_name, race_no):
    params = urllib.parse.urlencode({
        "k_babaCode": NAR_COURSE_CODES[course_name],
        "k_raceDate": nar_date_text(),
        "k_raceNo": int(race_no),
    })
    return f"{NAR_BASE_URL}/{page_name}?{params}"


def race_numbers(course):
    q = urllib.parse.urlencode({
        "k_babaCode": NAR_COURSE_CODES[course],
        "k_raceDate": nar_date_text(),
    })
    text = nar_fetch(f"{NAR_BASE_URL}/RaceList?{q}")
    nums = {
        int(x) for x in re.findall(r"k_raceNo=(\d+)", text)
        if 1 <= int(x) <= 12
    }
    if not nums:
        plain = re.sub(r"<[^>]+>", " ", text)
        nums = {
            int(x) for x in re.findall(r"(?<!\d)(1[0-2]|[1-9])R(?!\d)", plain)
        }
    return sorted(nums)


def nar_get_wide_odds(course_name, race_no):
    text = nar_fetch(nar_url("OddsWide", course_name, race_no))
    parser = SimpleTableParser()
    parser.feed(text)
    result = []

    for row in parser.rows:
        if len(row) < 2:
            continue
        combo = row[0].replace(" ", "")
        if not re.fullmatch(r"\d{1,2}-\d{1,2}", combo):
            continue

        nums = re.findall(r"\d+(?:\.\d+)?", row[1])
        if not nums:
            continue
        try:
            low = float(nums[0])
            high = float(nums[1]) if len(nums) >= 2 else low
        except ValueError:
            continue

        popularity = ""
        if len(row) >= 3:
            m = re.search(r"\d+", row[2])
            if m:
                popularity = m.group()

        result.append({
            "combo": combo,
            "low": low,
            "high": high,
            "display": f"{low:.1f}" if low == high else f"{low:.1f}～{high:.1f}",
            "popularity": popularity,
        })

    result.sort(key=lambda x: (
        int(x["popularity"]) if str(x["popularity"]).isdigit() else 9999,
        x["low"],
    ))
    return result


def nar_get_horse_market(course_name, race_no):
    text = nar_fetch(nar_url("OddsTanFuku", course_name, race_no))
    parser = SimpleTableParser()
    parser.feed(text)
    horses = []

    for row in parser.rows:
        if len(row) < 5:
            continue
        horse_no_text = row[1].replace(" ", "")
        if not re.fullmatch(r"\d{1,2}", horse_no_text):
            continue

        horse_name = row[2].strip()
        if not horse_name or "馬名" in horse_name:
            continue

        win_nums = re.findall(r"\d+(?:\.\d+)?", row[3])
        if not win_nums:
            continue
        try:
            win_odds = float(win_nums[0])
        except ValueError:
            continue
        if win_odds <= 0:
            continue

        place_nums = re.findall(r"\d+(?:\.\d+)?", row[4])
        try:
            place_low = float(place_nums[0]) if place_nums else 0.0
            place_high = float(place_nums[1]) if len(place_nums) >= 2 else place_low
        except ValueError:
            place_low = place_high = 0.0

        horses.append({
            "horse_no": int(horse_no_text),
            "horse_name": horse_name,
            "win_odds": win_odds,
            "place_low": place_low,
            "place_high": place_high,
        })

    horses.sort(key=lambda x: x["win_odds"])
    for rank, horse in enumerate(horses, start=1):
        horse["market_rank"] = rank
    return horses


def make_pair_key(a, b):
    first, second = sorted((int(a), int(b)))
    return f"{first}-{second}"


def select_three_by_mode(horses, wide_data, mode):
    """PC版v44の3点候補ルールをスマホ向けに移植。"""
    if len(horses) < 3 or not wide_data:
        return []

    wide_map = {item["combo"]: item for item in wide_data}
    ranked = sorted(horses, key=lambda h: h["market_rank"])
    candidates = []

    def add_candidate(axis, partner, wide, score, reason):
        confidence = max(1, min(99, int(round(100 - score * 5))))
        candidates.append((score, axis, partner, wide, confidence, reason))

    if mode == "堅め":
        axis = ranked[0]
        for partner in ranked[1:5]:
            combo = make_pair_key(axis["horse_no"], partner["horse_no"])
            wide = wide_map.get(combo)
            if not wide:
                continue
            spread = max(0.0, wide["high"] - wide["low"])
            score = (
                abs(wide["low"] - 2.5) * 0.8
                + (partner["market_rank"] - 2) * 0.55
                + spread * 0.10
            )
            add_candidate(axis, partner, wide, score, "1番人気軸＋上位人気を重視")

    elif mode == "穴狙い":
        for axis in ranked[:3]:
            for partner in ranked[2:8]:
                if axis["horse_no"] == partner["horse_no"]:
                    continue
                combo = make_pair_key(axis["horse_no"], partner["horse_no"])
                wide = wide_map.get(combo)
                if not wide or wide["low"] < 4.0:
                    continue

                low = wide["low"]
                spread = max(0.0, wide["high"] - wide["low"])
                odds_penalty = abs(low - 7.0) * 0.55
                if low > 12.0:
                    odds_penalty += (low - 12.0) * 0.9
                if low > 15.0:
                    odds_penalty += 5.0

                score = (
                    odds_penalty
                    + (axis["market_rank"] - 1) * 0.65
                    + abs(partner["market_rank"] - 5) * 0.35
                    + spread * 0.08
                )
                add_candidate(
                    axis, partner, wide, score,
                    "上位人気を軸に中穴を狙い、極端な大穴は抑制",
                )

    else:  # バランス
        for axis in ranked[:2]:
            for partner in ranked[1:6]:
                if axis["horse_no"] == partner["horse_no"]:
                    continue
                combo = make_pair_key(axis["horse_no"], partner["horse_no"])
                wide = wide_map.get(combo)
                if not wide:
                    continue
                spread = max(0.0, wide["high"] - wide["low"])
                score = (
                    abs(wide["low"] - 5.5) * 0.75
                    + (axis["market_rank"] - 1) * 0.45
                    + abs(partner["market_rank"] - 4) * 0.30
                    + spread * 0.08
                )
                add_candidate(
                    axis, partner, wide, score,
                    "上位人気と中位人気の組合せを重視",
                )

    candidates.sort(key=lambda x: x[0])
    selected, seen = [], set()

    for score, axis, partner, wide, confidence, reason in candidates:
        if wide["combo"] in seen:
            continue
        seen.add(wide["combo"])
        selected.append({
            "combo": wide["combo"],
            "low": wide["low"],
            "high": wide["high"],
            "display": wide["display"],
            "popularity": wide.get("popularity", ""),
            "axis": axis,
            "partner": partner,
            "score": round(score, 2),
            "confidence": confidence,
            "reason": reason,
        })
        if len(selected) >= 3:
            break
    return selected


def history_calibration():
    with db() as con:
        rows = con.execute(
            "SELECT result,total_bet,return_amount FROM purchases "
            "WHERE result IN ('的中','ハズレ')"
        ).fetchall()

    n = len(rows)
    if n == 0:
        return {"n": 0, "hit_rate": None, "roi": None, "score_adjust": 0}

    hits = sum(1 for r in rows if r["result"] == "的中")
    bet = sum(max(0, int(r["total_bet"])) for r in rows)
    ret = sum(max(0, int(r["return_amount"])) for r in rows)
    hit_rate = hits / n
    roi = ret / bet if bet > 0 else None

    if n < 20 or roi is None:
        adj = 0
    else:
        weight = min(1.0, n / 100.0)
        roi_part = max(-4.0, min(4.0, (roi - 1.0) * 8.0))
        hit_part = max(-2.0, min(2.0, (hit_rate - 0.35) * 5.0))
        adj = int(round((roi_part + hit_part) * weight))

    return {"n": n, "hit_rate": hit_rate, "roi": roi, "score_adjust": adj}


def add_expected_value_metrics(recommendations):
    calibration = history_calibration()

    for item in recommendations:
        low = max(float(item.get("low", 0.1)), 0.1)
        high = max(float(item.get("high", low)), low)
        mid = (low + high) / 2.0
        confidence = max(1.0, min(99.0, float(item.get("confidence", 50))))
        model_p = confidence / 100.0
        market_p = min(0.95, 1.0 / mid)

        n = calibration["n"]
        model_weight = 0.20 + min(0.25, n / 400.0)
        estimated_p = model_p * model_weight + market_p * (1.0 - model_weight)

        roi = calibration.get("roi")
        if n >= 50 and roi is not None:
            estimated_p *= max(0.92, min(1.08, 0.96 + 0.04 * roi))

        estimated_p = max(0.01, min(0.95, estimated_p))
        ev_index = estimated_p * mid

        item["estimated_hit_pct"] = round(estimated_p * 100, 1)
        item["ev_index"] = round(ev_index, 2)
        item["ev_label"] = (
            "妙味あり" if ev_index >= 1.08
            else "中立" if ev_index >= 0.95
            else "妙味薄め"
        )
    return calibration


def add_priority_scores(recommendations):
    for item in recommendations:
        confidence = float(item.get("confidence", 0))
        ev_index = float(item.get("ev_index", 0))
        low = max(float(item.get("low", 0.1)), 0.1)
        high = max(float(item.get("high", low)), low)
        spread_ratio = max(0.0, high - low) / low

        ev_score = max(0.0, min(100.0, (ev_index - 0.80) / 0.70 * 100.0))
        stability_score = max(0.0, min(100.0, 100.0 - spread_ratio * 100.0))
        item["priority_score"] = round(
            confidence * 0.50 + ev_score * 0.35 + stability_score * 0.15, 1
        )

    recommendations.sort(
        key=lambda x: (
            x.get("priority_score", 0),
            x.get("confidence", 0),
            x.get("ev_index", 0),
        ),
        reverse=True,
    )
    return recommendations


def evaluate_race_rank(horses, wide_data, mode, remaining_budget):
    recommendations = select_three_by_mode(horses, wide_data, mode)

    if len(recommendations) < 3:
        return {
            "grade": "見送り", "score": 0,
            "reasons": ["条件に合う3点候補を作れませんでした。"],
            "recommendations": recommendations,
        }

    if remaining_budget < 100:
        return {
            "grade": "見送り", "score": 0,
            "reasons": ["本日の残り予算が100円未満です。"],
            "recommendations": recommendations,
        }

    confidences = [x["confidence"] for x in recommendations]
    avg_conf = sum(confidences) / len(confidences)
    min_conf = min(confidences)

    spread_ratios = []
    for item in recommendations:
        low = max(item["low"], 0.1)
        spread_ratios.append(max(0.0, item["high"] - item["low"]) / low)
    avg_spread = sum(spread_ratios) / len(spread_ratios)

    ranked = sorted(horses, key=lambda h: h["market_rank"])
    favorite_odds = ranked[0]["win_odds"] if ranked else 99.9

    score = avg_conf
    if avg_spread <= 0.12:
        score += 3
    elif avg_spread <= 0.22:
        score += 1
    elif avg_spread >= 0.45:
        score -= 6
    elif avg_spread >= 0.30:
        score -= 3

    if 1.4 <= favorite_odds <= 3.5:
        score += 1
    elif favorite_odds >= 8.0:
        score -= 4

    if min_conf < 75:
        score -= 8
    elif min_conf < 82:
        score -= 4

    if mode == "穴狙い":
        score -= 4
    elif mode == "堅め":
        score += 1

    score = max(0, min(100, int(round(score))))

    if score >= 94 and min_conf >= 88 and mode != "穴狙い":
        base_grade = "S"
    elif score >= 88 and min_conf >= 82:
        base_grade = "A"
    elif score >= 79 and min_conf >= 72:
        base_grade = "B"
    else:
        base_grade = "見送り"

    grade = base_grade
    if base_grade == "S":
        if score >= 98 and min_conf >= 94 and avg_spread <= 0.35 and mode != "穴狙い":
            grade = "S+"
        elif score >= 95 and min_conf >= 90 and avg_spread <= 0.75 and mode != "穴狙い":
            grade = "S"
        else:
            grade = "S-"

    calibration = add_expected_value_metrics(recommendations)
    add_priority_scores(recommendations)

    # 履歴20件以上で期待値が明確に低ければ1段階下げる
    if calibration["n"] >= 20:
        values = [float(x.get("ev_index", 0)) for x in recommendations]
        avg_ev = sum(values) / len(values) if values else 0
        min_ev = min(values) if values else 0
        if avg_ev < 0.90 or min_ev < 0.80:
            grade = {
                "S+": "S", "S": "S-", "S-": "A", "A": "見送り"
            }.get(grade, grade)

    reasons = [
        f"3点候補の平均評価：{avg_conf:.1f}点",
        f"3点候補の最低評価：{min_conf}点",
        f"平均オッズ幅：{avg_spread * 100:.1f}%",
        f"1番人気の単勝オッズ：{favorite_odds:.1f}倍",
        f"モード：{mode}",
    ]

    return {
        "grade": grade,
        "score": score,
        "reasons": reasons,
        "recommendations": recommendations,
    }


def allocate_amounts(grade, recommendations, remaining_budget):
    if not recommendations or remaining_budget < 100:
        return [0] * len(recommendations)

    ratio = {"S+": 0.30, "S": 0.20, "S-": 0.10, "A": 0.10}.get(grade, 0.0)
    if ratio <= 0:
        # B以下は自動購入額を出さず、手動判断にする
        return [0] * len(recommendations)

    usable = int((remaining_budget * ratio) // 100 * 100)
    if len(recommendations) >= 3 and usable < 300:
        if remaining_budget >= 300:
            usable = 300
        else:
            return [0] * len(recommendations)

    weights = [max(1.0, float(x.get("priority_score", 1.0))) for x in recommendations]
    total_weight = sum(weights)
    amounts = [int((usable * w / total_weight) // 100 * 100) for w in weights]

    for i in range(min(3, len(amounts))):
        if usable >= 300 and amounts[i] < 100:
            amounts[i] = 100

    i = 0
    while sum(amounts) + 100 <= usable and amounts:
        amounts[i % len(amounts)] += 100
        i += 1

    while sum(amounts) > usable:
        changed = False
        for i in range(len(amounts) - 1, -1, -1):
            if amounts[i] >= 200:
                amounts[i] -= 100
                changed = True
                break
        if not changed:
            break

    return amounts


def save_pick(course, race, mode, result):
    if result["grade"] not in ("S+", "S", "S-", "A"):
        return
    recs = result["recommendations"][:3]
    texts = [f'{x["combo"]} ({x["display"]}倍)' for x in recs]
    texts += [""] * (3 - len(texts))

    with db() as con:
        con.execute("""
        INSERT INTO picks(race_date,saved_at,course,race,mode,grade,score,candidate1,candidate2,candidate3)
        VALUES(?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(race_date,course,race,mode) DO UPDATE SET
        saved_at=excluded.saved_at,grade=excluded.grade,score=excluded.score,
        candidate1=excluded.candidate1,candidate2=excluded.candidate2,candidate3=excluded.candidate3
        """, (
            today(), now().strftime("%Y-%m-%d %H:%M:%S"),
            course, f"{race}R", mode, result["grade"], int(result["score"]),
            texts[0], texts[1], texts[2],
        ))


CSS = """
:root{--bg:#f3f6fa;--card:#fff;--ink:#17202d;--muted:#68778c;--line:#dce4ee;--blue:#1677ff;--green:#16834f;--red:#b42318;--gold:#a56500}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Yu Gothic",sans-serif}
.wrap{max-width:980px;margin:auto;padding:12px}
.head{display:flex;justify-content:space-between;gap:8px;align-items:center;margin:4px 0 12px}
h1{font-size:20px;margin:0}.badge{background:#e7f0ff;color:#175aa6;border-radius:99px;padding:6px 9px;font-weight:700;font-size:12px}
.nav{display:flex;gap:7px;overflow:auto;margin-bottom:10px;padding-bottom:2px}
.card{background:white;border:1px solid var(--line);border-radius:15px;padding:14px;margin-bottom:10px;box-shadow:0 2px 8px #17202d0b}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}.metric small{color:var(--muted);display:block}.metric strong{font-size:21px}
.title{font-weight:800;font-size:17px;margin-bottom:10px}.row{display:grid;grid-template-columns:1.2fr .8fr .8fr;gap:7px;margin-bottom:8px}
.two{display:grid;grid-template-columns:1fr 1fr;gap:7px}
label{display:block;color:var(--muted);font-size:12px;margin-bottom:4px}
input,select{width:100%;font-size:16px;padding:11px;border:1px solid #cbd6e2;border-radius:10px;background:#fff}
button,.btn{border:0;border-radius:10px;background:var(--blue);color:white;padding:11px 13px;font-weight:700;text-decoration:none;display:inline-block;font-size:14px}
.secondary{background:#edf2f7;color:#26384d}.green{background:var(--green)}.red{background:var(--red)}.gold{background:var(--gold)}
.actions{display:flex;gap:7px;flex-wrap:wrap}.note{padding:11px;border-radius:11px;background:#fff7e5;border:1px solid #efd196;color:#704600;font-size:13px;margin-bottom:10px}
.ok{padding:11px;border-radius:11px;background:#eaf8ef;border:1px solid #a9d9b9;color:#155d31;margin-bottom:10px}
.bad{padding:11px;border-radius:11px;background:#fff0ef;border:1px solid #efbbb5;color:#7d2118;margin-bottom:10px}
.grade{font-size:34px;font-weight:900;line-height:1}.score{font-size:18px;font-weight:800;color:var(--muted)}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:8px 5px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{color:var(--muted)}.scroll{overflow:auto}
.rank1{font-weight:800;background:#f7fbff}.small{font-size:12px;color:var(--muted)}
@media(max-width:700px){.grid{grid-template-columns:1fr 1fr}.row,.two{grid-template-columns:1fr}.wrap{padding:9px}.metric strong{font-size:18px}}
"""


def page(body, title=APP_TITLE):
    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="地方競馬v47">
<title>{html.escape(title)}</title><style>{CSS}</style></head><body><div class="wrap">
<div class="head"><h1>{APP_TITLE}</h1><span class="badge">スマホ完全版</span></div>
<div class="nav">
<a class="btn secondary" href="/">ホーム</a>
<a class="btn secondary" href="/analyze">オッズ・3点予想</a>
<a class="btn secondary" href="/picks">勝負レース</a>
<a class="btn secondary" href="/history">成績履歴</a>
<a class="btn secondary" href="/courses">本日の開催</a>
</div>
{body}
<div class="note">3点候補・参考ランクは市場オッズを使ったルールベースの参考情報です。的中・利益を保証しません。SPAT4のログイン情報は保存せず、実際の投票・最終確認はSPAT4公式サイトでご自身で行ってください。</div>
</div></body></html>"""


@app.get("/")
def home():
    s = summary()
    d = get_draft()
    bets = draft_bets(d)
    msg = request.args.get("msg", "")
    message = f'<div class="ok">{html.escape(msg)}</div>' if msg else ""

    rows = ""
    for i in range(1, 4):
        rows += f"""<div class="row">
        <div><label>ワイド{i}</label><input name="wide{i}" value="{html.escape(str(d.get(f'wide{i}') or ''))}" placeholder="例 3-7"></div>
        <div><label>オッズ</label><input inputmode="decimal" name="odds{i}" value="{html.escape(str(d.get(f'odds{i}') or ''))}" placeholder="4.5"></div>
        <div><label>購入額</label><input inputmode="numeric" name="amount{i}" value="{html.escape(str(d.get(f'amount{i}') or ''))}" placeholder="300"></div></div>"""

    calc = ""
    if bets:
        trs = "".join(
            f"<tr><td>{html.escape(b['combo'])}</td><td>{b['odds'] or '-'}倍</td>"
            f"<td>{b['amount']:,}円</td><td>{b['expected']:,}円</td></tr>"
            for b in bets
        )
        total = sum(b["amount"] for b in bets)
        calc = f"""<div class="card"><div class="title">直近の計算</div>
        <div class="scroll"><table><tr><th>買い目</th><th>オッズ</th><th>購入額</th><th>参考払戻</th></tr>{trs}</table></div>
        <p><strong>合計 {total:,}円</strong></p>
        <div class="actions">
        <form method="post" action="/record"><button class="green">SPAT4で購入後、この内容を購入記録</button></form>
        <a class="btn" href="{SPAT4_URL}" target="_blank" rel="noopener">SPAT4公式サイトを開く</a>
        </div></div>"""

    opts = "".join(
        f'<option {"selected" if c == (d.get("course") or "") else ""}>{c}</option>'
        for c in NAR_COURSE_CODES
    )

    return page(f"""{message}
    <div class="grid">
      <div class="card metric"><small>本日の上限</small><strong>{DAILY_LIMIT:,}円</strong></div>
      <div class="card metric"><small>本日の使用額</small><strong>{s['used']:,}円</strong></div>
      <div class="card metric"><small>残り予算</small><strong>{s['remaining']:,}円</strong></div>
      <div class="card metric"><small>本日の収支</small><strong>{s['profit']:+,}円</strong></div>
    </div>
    <div class="card">
      <div class="title">PC版機能をスマホで</div>
      <div class="actions">
        <a class="btn green" href="/analyze">本日のレース・オッズを取得 → 3点予想</a>
        <a class="btn gold" href="/picks">今日の勝負レースを見る</a>
      </div>
    </div>
    <div class="card"><div class="title">買い目を確認・計算</div>
    <form method="post" action="/calculate"><div class="two">
    <div><label>競馬場</label><select name="course"><option value="">選択してください</option>{opts}</select></div>
    <div><label>レース番号</label><input name="race" value="{html.escape(str(d.get('race') or ''))}" placeholder="例 11R"></div></div><br>
    {rows}<button>購入内容を計算</button></form></div>{calc}""")


@app.post("/calculate")
def calculate():
    d = save_draft(request.form)
    total = sum(b["amount"] for b in draft_bets(d))
    if total > summary()["remaining"]:
        return redirect(url_for(
            "home",
            msg=f"計算しました。合計{total:,}円です。本日の残り予算を超えています。"
        ))
    return redirect(url_for("home", msg=f"計算しました。合計 {total:,}円です。"))


@app.route("/analyze", methods=["GET", "POST"])
def analyze():
    course = request.values.get("course", "")
    race = to_int(request.values.get("race", ""), 0)
    mode = request.values.get("mode", "バランス")
    opts = "".join(
        f'<option {"selected" if c == course else ""}>{c}</option>'
        for c in NAR_COURSE_CODES
    )
    race_opts = "".join(
        f'<option value="{n}" {"selected" if n == race else ""}>{n}R</option>'
        for n in range(1, 13)
    )
    mode_opts = "".join(
        f'<option {"selected" if m == mode else ""}>{m}</option>'
        for m in ("堅め", "バランス", "穴狙い")
    )

    form = f"""<div class="card"><div class="title">オッズ取得・3点予想</div>
    <form method="post">
    <div class="two">
      <div><label>競馬場</label><select name="course"><option value="">選択してください</option>{opts}</select></div>
      <div><label>レース</label><select name="race"><option value="">選択</option>{race_opts}</select></div>
    </div><br>
    <div><label>予想モード</label><select name="mode">{mode_opts}</select></div><br>
    <button class="green">ワイドオッズ取得 → 3点予想</button>
    </form></div>"""

    if request.method == "GET":
        return page(form, "オッズ・3点予想")

    if course not in NAR_COURSE_CODES or not (1 <= race <= 12):
        return page(form + '<div class="bad">競馬場とレース番号を選択してください。</div>', "オッズ・3点予想")

    try:
        wide_data = nar_get_wide_odds(course, race)
        horse_data = nar_get_horse_market(course, race)
    except Exception as exc:
        return page(
            form + f'<div class="bad">取得エラー：{html.escape(type(exc).__name__)} - {html.escape(str(exc))}</div>',
            "オッズ・3点予想"
        )

    if not wide_data:
        return page(
            form + '<div class="note">ワイドオッズを取得できませんでした。発売前・締切後・NAR側更新中の可能性があります。</div>',
            "オッズ・3点予想"
        )

    if not horse_data:
        return page(
            form + '<div class="note">単勝・複勝データを取得できませんでした。発売前などの可能性があります。</div>',
            "オッズ・3点予想"
        )

    result = evaluate_race_rank(horse_data, wide_data, mode, summary()["remaining"])
    save_pick(course, race, mode, result)
    recs = result["recommendations"][:3]
    amounts = allocate_amounts(result["grade"], recs, summary()["remaining"])

    rec_rows = ""
    hidden = ""
    for idx, item in enumerate(recs, start=1):
        amount = amounts[idx - 1] if idx - 1 < len(amounts) else 0
        rec_rows += (
            f'<tr class="{"rank1" if idx == 1 else ""}><td>{idx}位</td>'
            f'<td><strong>{html.escape(item["combo"])}</strong></td>'
            f'<td>{html.escape(item["display"])}倍</td>'
            f'<td>{item["confidence"]}点</td>'
            f'<td>{item.get("priority_score", 0):.1f}</td>'
            f'<td>{item.get("ev_index", 0):.2f}<br><span class="small">{html.escape(item.get("ev_label",""))}</span></td>'
            f'<td>{amount:,}円</td></tr>'
        )
        hidden += (
            f'<input type="hidden" name="wide{idx}" value="{html.escape(item["combo"])}">'
            f'<input type="hidden" name="odds{idx}" value="{item["low"]}">'
            f'<input type="hidden" name="amount{idx}" value="{amount or DEFAULT_BET}">'
        )

    reasons = "".join(f"<li>{html.escape(x)}</li>" for x in result["reasons"])
    all_rows = "".join(
        f"<tr><td>{html.escape(x['combo'])}</td><td>{html.escape(x['display'])}</td><td>{html.escape(str(x.get('popularity','')))}</td></tr>"
        for x in wide_data[:50]
    )

    result_html = f"""
    <div class="card">
      <div class="title">{html.escape(course)} {race}R　参考判定</div>
      <div class="grade">{html.escape(result["grade"])}</div>
      <div class="score">参考スコア {result["score"]} / 100</div>
      <ul>{reasons}</ul>
      <div class="small">※これは的中確率ではありません。市場オッズを使ったルールベース評価です。</div>
    </div>

    <div class="card">
      <div class="title">3点候補</div>
      <div class="scroll"><table>
      <tr><th>順位</th><th>ワイド</th><th>オッズ</th><th>候補評価</th><th>優先度</th><th>参考EV</th><th>推奨額</th></tr>
      {rec_rows or '<tr><td colspan="7">候補を3点作れませんでした。</td></tr>'}
      </table></div>
      {"<form method='post' action='/apply_recommendations'>" + hidden +
       f"<input type='hidden' name='course' value='{html.escape(course)}'>"
       f"<input type='hidden' name='race' value='{race}R'>"
       "<button class='green'>この3点をホームへ入力</button></form>" if recs else ""}
    </div>

    <div class="card">
      <div class="title">取得したワイドオッズ（最大50組）</div>
      <div class="scroll"><table><tr><th>組合せ</th><th>オッズ</th><th>人気</th></tr>{all_rows}</table></div>
    </div>
    """

    return page(form + result_html, "オッズ・3点予想")


@app.post("/apply_recommendations")
def apply_recommendations():
    vals = {
        "course": request.form.get("course", ""),
        "race": request.form.get("race", ""),
    }
    for i in range(1, 4):
        vals[f"wide{i}"] = clean_combo(request.form.get(f"wide{i}", ""))
        vals[f"odds{i}"] = to_float(request.form.get(f"odds{i}", ""), 0.0)
        vals[f"amount{i}"] = max(100, to_int(request.form.get(f"amount{i}", ""), DEFAULT_BET)) if vals[f"wide{i}"] else 0
    write_draft(vals)
    return redirect(url_for("home", msg="3点候補をホームへ入力しました。購入前に内容・最新オッズをご確認ください。"))


@app.post("/record")
def record():
    d = get_draft()
    bets = draft_bets(d)
    if not d or not bets:
        return redirect(url_for("home", msg="先に購入内容を計算してください。"))

    total = sum(b["amount"] for b in bets)
    if total > summary()["remaining"]:
        return redirect(url_for("home", msg="本日の残り予算を超えるため記録できません。"))

    bet_text = " / ".join(
        f"{b['combo']} {b['amount']}円 @{b['odds']}" for b in bets
    )

    with db() as con:
        con.execute("""
        INSERT INTO purchases(created_at,race_date,course,race,bets,total_bet,result,return_amount)
        VALUES(?,?,?,?,?,?,?,?)
        """, (
            now().strftime("%Y-%m-%d %H:%M:%S"), today(),
            d.get("course") or "", d.get("race") or "",
            bet_text, total, "未確定", 0,
        ))

    return redirect(url_for("history", msg="購入記録を追加しました。"))


@app.get("/picks")
def picks():
    with db() as con:
        rows = con.execute(
            "SELECT * FROM picks WHERE race_date=? ORDER BY "
            "CASE grade WHEN 'S+' THEN 1 WHEN 'S' THEN 2 WHEN 'S-' THEN 3 WHEN 'A' THEN 4 ELSE 9 END,"
            "score DESC, id DESC",
            (today(),),
        ).fetchall()

    trs = ""
    for r in rows:
        trs += (
            f"<tr><td><strong>{html.escape(r['course'])} {html.escape(r['race'])}</strong></td>"
            f"<td>{html.escape(r['mode'])}</td><td><strong>{html.escape(r['grade'])}</strong></td>"
            f"<td>{r['score']}</td><td>{html.escape(r['candidate1'])}<br>{html.escape(r['candidate2'])}<br>{html.escape(r['candidate3'])}</td></tr>"
        )

    if not trs:
        trs = '<tr><td colspan="5">まだ勝負レース候補はありません。「オッズ・3点予想」で分析したS/A系レースがここに保存されます。</td></tr>'

    return page(
        f"""<div class="card"><div class="title">今日の勝負レース</div>
        <div class="scroll"><table><tr><th>レース</th><th>モード</th><th>判定</th><th>スコア</th><th>3点候補</th></tr>{trs}</table></div></div>""",
        "今日の勝負レース"
    )


@app.get("/history")
def history():
    msg = request.args.get("msg", "")
    with db() as con:
        rows = con.execute(
            "SELECT * FROM purchases ORDER BY id DESC LIMIT 200"
        ).fetchall()

    trs = ""
    for r in rows:
        profit = int(r["return_amount"]) - int(r["total_bet"])
        trs += f"""<tr>
        <td>{html.escape(r['created_at'])}</td>
        <td>{html.escape(r['course'])} {html.escape(r['race'])}</td>
        <td>{html.escape(r['bets'])}</td>
        <td>{r['total_bet']:,}円</td>
        <td>{html.escape(r['result'])}</td>
        <td>{r['return_amount']:,}円</td>
        <td>{profit:+,}円</td>
        <td><form method="post" action="/result/{r['id']}" style="display:flex;gap:4px;min-width:270px">
          <input name="return_amount" inputmode="numeric" placeholder="払戻額">
          <button class="green" name="kind" value="hit">的中</button>
          <button class="red" name="kind" value="miss">ハズレ</button>
        </form></td></tr>"""

    if not trs:
        trs = '<tr><td colspan="8">履歴はまだありません。</td></tr>'

    return page(
        (f'<div class="ok">{html.escape(msg)}</div>' if msg else "")
        + f"""<div class="card"><div class="title">成績履歴</div>
        <div class="scroll"><table>
        <tr><th>日時</th><th>レース</th><th>買い目</th><th>購入</th><th>結果</th><th>払戻</th><th>収支</th><th>結果入力</th></tr>
        {trs}</table></div></div>""",
        "成績履歴"
    )


@app.post("/result/<int:pid>")
def result(pid):
    kind = request.form.get("kind")
    if kind == "hit":
        ret = max(0, to_int(request.form.get("return_amount"), 0))
        result_text = "的中"
        if ret <= 0:
            return redirect(url_for("history", msg="的中の場合は払戻額を入力してください。"))
    else:
        ret = 0
        result_text = "ハズレ"

    with db() as con:
        con.execute(
            "UPDATE purchases SET result=?,return_amount=? WHERE id=?",
            (result_text, ret, pid),
        )
    return redirect(url_for("history", msg=f"{result_text}として更新しました。"))


@app.get("/courses")
def courses():
    items = []
    for c in NAR_COURSE_CODES:
        try:
            r = race_numbers(c)
        except Exception:
            r = []
        if r:
            items.append((c, r))

    if not items:
        return page(
            '<div class="note">本日の開催を取得できませんでした。時間帯またはNAR側の表示をご確認ください。</div>',
            "本日の開催"
        )

    trs = "".join(
        f"<tr><td><strong>{html.escape(c)}</strong></td>"
        f"<td>{', '.join(str(x) + 'R' for x in r)}</td>"
        f"<td><a class='btn secondary' href='{html.escape(url_for('analyze', course=c, race=r[-1], mode='バランス'), quote=True)}'>分析へ</a></td></tr>"
        for c, r in items
    )

    return page(
        f'<div class="card"><div class="title">本日の開催</div>'
        f'<table><tr><th>競馬場</th><th>レース</th><th></th></tr>{trs}</table></div>',
        "本日の開催"
    )


@app.get("/health")
def health():
    return "ok", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
