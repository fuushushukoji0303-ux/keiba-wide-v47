# -*- coding: utf-8 -*-
"""
地方競馬 全馬能力指数エンジン v52.0

出典: 「地方競馬予想ソフト_平地14場_Ver1.0.1_3部門一致表示」
の各競馬場 predictor_v090.py の能力指数ロジックをWeb版向けに共通化。

重要:
- 総合・複勝・ワイド軸の配点は既存能力指数ソフトから変更していません。
- オッズ/人気は3指数に使用しません。
- 競馬場固有部分は course_name / baba code に置換しただけです。
"""
import re
import statistics
from datetime import datetime
from io import StringIO

import requests
import pandas as pd
from bs4 import BeautifulSoup

BASE = "https://www.keiba.go.jp"
DEBA = BASE + "/KeibaWeb/TodayRaceInfo/DebaTable"
SP_DEBA = BASE + "/KeibaWebSP/TodayRaceInfo/S_DebaTable"
RIDER_LEADING = BASE + "/KeibaWeb/DataRoom/RiderLeading"
TRAINER_LEADING = BASE + "/KeibaWeb/DataRoom/TrainerLeading"

COURSE_CODES = {
    "門別": 36, "盛岡": 10, "水沢": 11, "浦和": 18, "船橋": 19,
    "大井": 20, "川崎": 21, "金沢": 22, "笠松": 23, "名古屋": 24,
    "園田": 27, "姫路": 28, "高知": 31, "佐賀": 32,
}
PAST_LABELS = ["前走", "前々走", "3走前", "4走前", "5走前"]
PAST_WEIGHTS = [0.35, 0.25, 0.20, 0.12, 0.08]
_LEADING_CACHE = {}


def clamp(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, v))


def clean_text(text):
    return re.sub(r"\s+", " ", str(text or "").replace("\n", " ")).strip()


def norm_name(text):
    return re.sub(r"[\s　]+", "", str(text or ""))


def fetch_html(url, read_timeout=12):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        )
    }
    r = requests.get(url, headers=headers, timeout=(5, read_timeout))
    r.raise_for_status()
    r.encoding = r.apparent_encoding
    return r.text


def flatten_col(c):
    if isinstance(c, tuple):
        return " ".join(
            str(x).strip() for x in c
            if str(x).strip() and str(x).lower() != "nan" and not str(x).startswith("Unnamed")
        )
    return str(c).replace("\n", " ").strip()


def find_col(df, keywords):
    for c in df.columns:
        s = str(c)
        if any(k in s for k in keywords):
            return c
    return None


def find_past_col(df, label):
    for c in df.columns:
        s = str(c).replace(" ", "")
        if label in s:
            if label == "前走" and "前々走" in s:
                continue
            return c
    return None


def cell(row, c):
    if c is None:
        return ""
    v = row[c]
    if isinstance(v, pd.Series):
        return " ".join(str(x).strip() for x in v.tolist() if not pd.isna(x))
    return "" if pd.isna(v) else str(v).strip()


def int_only(text):
    m = re.fullmatch(r"\s*(\d{1,2})(?:\.0)?\s*", str(text))
    if not m:
        return None
    n = int(m.group(1))
    return n if 1 <= n <= 18 else None


def get_entry_table(html):
    tables = pd.read_html(StringIO(html))
    candidates = []
    for df in tables:
        df.columns = [flatten_col(c) for c in df.columns]
        joined = " ".join(df.columns)
        if "馬番" in joined and ("競走馬" in joined or "馬名" in joined):
            candidates.append(df)
    if not candidates:
        raise ValueError("PC版出馬表テーブルが見つかりません")
    return max(candidates, key=lambda x: (len(x), len(x.columns)))


def get_sp_entry_table(html):
    tables = pd.read_html(StringIO(html))
    candidates = []
    for df in tables:
        df.columns = [flatten_col(c) for c in df.columns]
        joined = " ".join(df.columns)
        if "馬番" in joined and "騎手" in joined and "馬名" in joined:
            candidates.append(df)
    if not candidates:
        raise ValueError("スマホ版出馬表テーブルが見つかりません")
    return max(candidates, key=lambda x: (len(x), len(x.columns)))


def parse_race_meta(html):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    dm = re.search(r"ダート\s*(\d{3,4})\s*[ｍm]", text)
    distance = int(dm.group(1)) if dm else None
    title = ""
    m = re.search(r"第\d+競走\s+\d{1,2}:\d{2}発走\s+(.+?)\s+ダート", text)
    if m:
        title = clean_text(m.group(1))
    return {"distance": distance, "title": title}


def class_score(text):
    t = clean_text(text).upper()
    if "JPN" in t or re.search(r"\bG[123]\b", t): return 100.0
    if "OP" in t or "オープン" in t: return 95.0
    if "3勝クラス" in t: return 92.0
    if "2勝クラス" in t: return 86.0
    if "1勝クラス" in t: return 80.0
    if "未勝利" in t: return 66.0
    if "Ａ１" in t or "A1" in t: return 94.0
    if "Ａ２" in t or "A2" in t: return 90.0
    if "Ｂ１" in t or "B1" in t: return 84.0
    if "Ｂ２" in t or "B2" in t: return 80.0
    if "Ｂ３" in t or "B3" in t: return 76.0
    if "Ｃ１" in t or "C1" in t: return 70.0
    if "Ｃ２" in t or "C2" in t: return 64.0
    if "Ｃ３" in t or "C3" in t: return 56.0
    return 58.0


def parse_run(text):
    t = clean_text(text)
    out = {"finish": None, "field": None, "venue": "", "distance": None,
           "margin": None, "final3f": None, "corners": [], "detail_score": None}
    if not t: return out
    fm = re.match(r"^\s*(\d{1,2})\b", t)
    hm = re.search(r"(\d{1,2})\s*頭", t)
    if fm: out["finish"] = int(fm.group(1))
    if hm: out["field"] = int(hm.group(1))
    venues = ["船橋","浦和","川崎","大井","門別","盛岡","水沢","金沢","笠松","名古屋","園田","姫路","高知","佐賀",
              "Ｊ東京","Ｊ中山","Ｊ新潟","Ｊ福島","Ｊ阪神","Ｊ京都","Ｊ札幌","Ｊ函館","Ｊ小倉","Ｊ中京"]
    for v in venues:
        if v in t:
            out["venue"] = v; break
    if out["venue"]:
        p = t.find(out["venue"]); part = t[p:p+55]
        dm = re.search(r"(?:左|右|直)?\s*(\d{3,4})\s*(?:m|ｍ)?", part)
        if dm:
            d = int(dm.group(1))
            if 800 <= d <= 3600: out["distance"] = d
    seqs = re.findall(r"\b(\d{1,2}(?:-\d{1,2}){1,3})\b", t)
    if seqs: out["corners"] = [int(x) for x in seqs[-1].split("-")]
    decimals = [float(x) for x in re.findall(r"(?<![:\d])(\d{1,3}\.\d)(?!\d)", t)]
    finals = [x for x in decimals if 30.0 <= x <= 55.0]
    if finals: out["final3f"] = finals[-1]
    margins = [x for x in decimals if 0.0 <= x <= 15.0]
    if margins: out["margin"] = margins[-1]
    f, n = out["finish"], out["field"]
    if f is not None and n is not None and n >= 2:
        pos_score = clamp(100.0 * (n-f) / (n-1))
        if f == 1: margin_score = 100.0
        elif out["margin"] is not None: margin_score = clamp(100.0 - out["margin"] * 22.0)
        else: margin_score = pos_score
        pace_score = 50.0
        if out["corners"]:
            gain = out["corners"][0] - out["corners"][-1]
            pace_score = 50.0 + gain * 7.0
            if f <= 3 and min(out["corners"]) <= 3: pace_score += 15.0
            if f <= 4 and gain >= 3: pace_score += 12.0
        pace_score = clamp(pace_score)
        final_score = 50.0 if out["final3f"] is None else clamp(100.0 - abs(out["final3f"] - 39.0) * 10.0)
        out["detail_score"] = clamp(pos_score*0.50 + margin_score*0.22 + class_score(t)*0.13 + pace_score*0.10 + final_score*0.05)
    return out


def weighted_recent_score(runs):
    total = used = 0.0; vals = []
    for run, w in zip(runs, PAST_WEIGHTS):
        s = run["detail_score"]
        if s is not None:
            total += s*w; used += w; vals.append(s)
    if used == 0: return 50.0, 0, 50.0
    recent = total / used
    stability = 50.0
    if len(vals) >= 2: stability = clamp(100.0 - statistics.pstdev(vals)*2.0)
    return round(recent,1), len(vals), round(stability,1)


def parse_record(blob, label):
    m = re.search(rf"{label}\s*(\d+)\s*-\s*(\d+)\s*-\s*(\d+)\s*-\s*(\d+)", blob)
    if not m: return None
    w,s,t,o = map(int,m.groups())
    return {"w":w,"s":s,"t":t,"o":o,"starts":w+s+t+o}


def record_score(rec):
    if not rec or rec["starts"] <= 0: return 50.0
    top3 = rec["w"] + rec["s"] + rec["t"]
    p = (top3 + 3.0) / (rec["starts"] + 10.0)
    return round(clamp(p / 0.60 * 100.0),1)


def recent_condition_score(runs, current_distance, mode, course_name):
    vals=[]
    for run in runs:
        if run["detail_score"] is None: continue
        if mode == "course":
            if run["venue"] == course_name: vals.append(run["detail_score"])
        elif mode == "distance":
            if current_distance and run["distance"] and abs(run["distance"]-current_distance) <= 100:
                vals.append(run["detail_score"])
    if not vals: return 50.0
    return round(sum(vals)/len(vals),1)


def blend_suitability(career, recent):
    return round(clamp(career*0.65 + recent*0.35),1)


def parse_sp_people(html):
    df = get_sp_entry_table(html)
    horse_no_col = find_col(df,["馬番"]); horse_info_col = find_col(df,["馬名"]); jockey_col = find_col(df,["騎手"])
    result={}
    for _,row in df.iterrows():
        horse_no=int_only(cell(row,horse_no_col))
        if horse_no is None: continue
        horse_info=clean_text(cell(row,horse_info_col)); jockey_text=clean_text(cell(row,jockey_col))
        horse_name=horse_info.split(" ")[0] if horse_info else ""
        trainer=""
        trainer_matches=re.findall(r"([一-龥々ぁ-んァ-ヶー・　\s]{2,20})（[^）]+）",horse_info)
        if trainer_matches: trainer=clean_text(trainer_matches[-1])
        jockey=re.sub(r"\s*（.*$","",jockey_text).strip()
        result[horse_no]={"horse_name":horse_name,"jockey":jockey,"trainer":trainer}
    return result


def find_leading_table(html):
    tables=pd.read_html(StringIO(html))
    for df in tables:
        df.columns=[flatten_col(c) for c in df.columns]; joined=" ".join(df.columns)
        if "氏名" in joined and "1着" in joined and "2着" in joined and "3着" in joined and "合計" in joined:
            return df
    raise ValueError("リーディングテーブルが見つかりません")


def to_num(v):
    try:
        s=re.sub(r"[^\d.\-]","",str(v)); return float(s) if s else 0.0
    except Exception: return 0.0


def percentile_map(raw):
    if not raw: return {}
    vals=sorted(raw.values()); n=len(vals); out={}
    for name,value in raw.items():
        if n<=1: out[name]=50.0; continue
        less=sum(1 for x in vals if x<value); equal=sum(1 for x in vals if x==value)
        position=less+(equal-1)/2.0
        out[name]=round(100.0*position/(n-1),1)
    return out


def build_leading_index(df):
    name_col=find_col(df,["氏名"]); first_col=find_col(df,["1着"]); second_col=find_col(df,["2着"]); third_col=find_col(df,["3着"]); total_col=find_col(df,["合計"])
    raw={}
    for _,row in df.iterrows():
        name=norm_name(cell(row,name_col))
        if not name: continue
        w=to_num(cell(row,first_col)); s=to_num(cell(row,second_col)); t=to_num(cell(row,third_col)); total=to_num(cell(row,total_col))
        if total<=0: continue
        win_rate=w/total; top3_rate=(w+s+t)/total
        raw[name]=win_rate*0.40+top3_rate*0.60
    return percentile_map(raw)


def load_rider_index(year):
    key=("rider",year)
    if key in _LEADING_CACHE: return _LEADING_CACHE[key]
    url=f"{RIDER_LEADING}?k_nenndo={year}&k_out_flag=1&selectedOption=80"
    value=build_leading_index(find_leading_table(fetch_html(url,10)))
    _LEADING_CACHE[key]=value
    return value


def load_trainer_index(year):
    key=("trainer",year)
    if key in _LEADING_CACHE: return _LEADING_CACHE[key]
    url=f"{TRAINER_LEADING}?k_nenndo1={year}&k_out_flag=1&selectedOption1=80"
    value=build_leading_index(find_leading_table(fetch_html(url,10)))
    _LEADING_CACHE[key]=value
    return value


def lookup_index(name,index_map,default=50.0):
    key=norm_name(name)
    if not key: return default
    if key in index_map: return index_map[key]
    for k,v in index_map.items():
        if len(key)>=2 and (key in k or k in key): return v
    return default


def parse_entries(df,race_no,current_distance,people_map,rider_index,trainer_index,course_name):
    frame_col=find_col(df,["枠番"]); horse_no_col=find_col(df,["馬番"]); horse_col=find_col(df,["競走馬","馬名"])
    past_cols=[find_past_col(df,label) for label in PAST_LABELS]
    rows=[]; seen=set()
    for _,row in df.iterrows():
        horse_no=int_only(cell(row,horse_no_col))
        if horse_no is None or horse_no in seen: continue
        horse_text=clean_text(cell(row,horse_col))
        if not horse_text: continue
        fallback_name=horse_text.split(" ")[0].strip(); person=people_map.get(horse_no,{})
        horse_name=person.get("horse_name") or fallback_name; jockey=person.get("jockey") or "-"; trainer=person.get("trainer") or "-"
        frame=int_only(cell(row,frame_col)); runs=[parse_run(cell(row,pc)) for pc in past_cols]
        recent,parsed_count,stability=weighted_recent_score(runs)
        row_blob=" ".join(clean_text(cell(row,c)) for c in df.columns)
        course_index=blend_suitability(record_score(parse_record(row_blob,"場")), recent_condition_score(runs,current_distance,"course",course_name))
        distance_index=blend_suitability(record_score(parse_record(row_blob,"距")), recent_condition_score(runs,current_distance,"distance",course_name))
        jockey_index=lookup_index(jockey,rider_index); trainer_index_value=lookup_index(trainer,trainer_index)
        # 既存能力指数ソフトの配点をそのまま使用
        total=(recent*0.38 + course_index*0.14 + distance_index*0.14 + jockey_index*0.12 + trainer_index_value*0.08 + stability*0.14)
        place=(recent*0.32 + stability*0.27 + course_index*0.14 + distance_index*0.14 + jockey_index*0.09 + trainer_index_value*0.04)
        wide=(place*0.43 + total*0.24 + stability*0.17 + jockey_index*0.08 + course_index*0.04 + distance_index*0.04)
        rows.append({"race":race_no,"frame":"" if frame is None else frame,"horse_no":horse_no,"horse_name":horse_name,"jockey":jockey,"trainer":trainer,
                     "recent":round(recent,1),"course":round(course_index,1),"distance":round(distance_index,1),"jockey_idx":round(jockey_index,1),
                     "trainer_idx":round(trainer_index_value,1),"stability":round(stability,1),"total":round(total,1),"place":round(place,1),"wide":round(wide,1),
                     "parsed":parsed_count,"rank_total":None,"rank_place":None,"rank_wide":None})
        seen.add(horse_no)
    for key,rank_key in [("total","rank_total"),("place","rank_place"),("wide","rank_wide")]:
        ranked=sorted(rows,key=lambda x:x[key],reverse=True)
        for rank,item in enumerate(ranked,start=1): item[rank_key]=rank
    return sorted(rows,key=lambda x:x["horse_no"])


def calculate_ability_indices(course_name, race_no, date_text):
    if course_name not in COURSE_CODES: raise ValueError("対象外の競馬場です")
    baba=COURSE_CODES[course_name]
    pc_url=f"{DEBA}?k_babaCode={baba}&k_raceDate={date_text}&k_raceNo={int(race_no)}"
    sp_url=f"{SP_DEBA}?k_babaCode={baba}&k_raceDate={date_text}&k_raceNo={int(race_no)}"
    pc_html=fetch_html(pc_url,15); sp_html=fetch_html(sp_url,15)
    df=get_entry_table(pc_html); people=parse_sp_people(sp_html); meta=parse_race_meta(pc_html)
    year=int(str(date_text).replace("/","-")[:4]) if str(date_text)[:4].isdigit() else datetime.now().year
    statuses=[]
    try:
        rider_idx=load_rider_index(year); statuses.append("騎手リーディング取得済み")
    except Exception as exc:
        rider_idx={}; statuses.append(f"騎手取得失敗→50点: {type(exc).__name__}")
    try:
        trainer_idx=load_trainer_index(year); statuses.append("調教師リーディング取得済み")
    except Exception as exc:
        trainer_idx={}; statuses.append(f"調教師取得失敗→50点: {type(exc).__name__}")
    leading_status=" / ".join(statuses)
    rows=parse_entries(df,int(race_no),meta.get("distance"),people,rider_idx,trainer_idx,course_name)
    top_total=next((x for x in rows if x["rank_total"]==1),None)
    top_place=next((x for x in rows if x["rank_place"]==1),None)
    top_wide=next((x for x in rows if x["rank_wide"]==1),None)
    triple_axis=0
    if top_total and top_place and top_wide:
        nos={top_total["horse_no"],top_place["horse_no"],top_wide["horse_no"]}
        if len(nos)==1: triple_axis=top_total["horse_no"]
    return {"rows":rows,"triple_axis":triple_axis,"meta":meta,"leading_status":leading_status,
            "top_total":top_total,"top_place":top_place,"top_wide":top_wide}
