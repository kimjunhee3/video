# Club_flask.py
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
import re
import os
import time
import json
from typing import Tuple

_SEARCH_CACHE = {}
SEARCH_CACHE_TTL = int(os.getenv("SEARCH_CACHE_TTL", "60"))  # seconds

app = Flask(__name__)
CORS(app)

# 팀 약어 -> 한글 팀명
TEAM_MAP = {
    "LG": "LG 트윈스",
    "두산": "두산 베어스",
    "SSG": "SSG 랜더스",
    "키움": "키움 히어로즈",
    "KT": "KT 위즈",
    "KIA": "KIA 타이거즈",
    "삼성": "삼성 라이온즈",
    "NC": "NC 다이노스",
    "롯데": "롯데 자이언츠",
    "한화": "한화 이글스",
}

# ---- 공식 채널 맵 (crawl과 동일하게 로드) ----
def _load_official_map():
    raw = os.getenv("OFFICIAL_CHANNELS_JSON")
    if raw:
        try:
            tmp = json.loads(raw)
            out = {}
            for k, v in tmp.items():
                out[k] = v if isinstance(v, list) else [v]
            return out
        except Exception:
            pass
    # 기본값(크롤러와 동일)
    return {
        "키움 히어로즈": ["UC_MA8-XEaVmvyayPzG66IKg"],
        "NC 다이노스":  ["UC8_FRgynMX8wlGsU6Jh3zKg"],
        "LG 트윈스":    ["UCL6QZZxb-HR4hCh_eFAnQWA"],
        "롯데 자이언츠":["UCAZQZdSY5_YrziMPqXi-Zfw"],
        "KT 위즈":     ["UCvScyjGkBUx2CJDMNAi9Twg"],
        "삼성 라이온즈":["UCMWAku3a3h65QpLm63Jf2pw"],
        "KIA 타이거즈":["UCKp8knO8a6tSI1oaLjfd9XA"],
        "두산 베어스": ["UCsebzRfMhwYfjeBIxNX1brg"],
        "SSG 랜더스": ["UCt8iRtgjVqm5rJHNl1TUojg"],
        "한화 이글스":["UCdq4Ji3772xudYRUatdzRrg"],
    }
OFFICIAL_MAP = _load_official_map()

# ---- 크롤러 로딩 ----
_search_func = None
_legacy_single_fetch = None
try:
    from crawl_club import search_videos_by_team as _search_func  # type: ignore
except Exception:
    _search_func = None

if _search_func is None:
    try:
        from crawl_club import get_youtube_videos as _legacy_single_fetch  # type: ignore
    except Exception:
        _legacy_single_fetch = None
        try:
            from crawl_youtube import search_youtube as _legacy_single_fetch  # type: ignore
        except Exception:
            _legacy_single_fetch = None

def _cached_safe_search(team_name: str, max_results: int = 60) -> Tuple[list, list]:
    key = f"{team_name}::{max_results}"
    rec = _SEARCH_CACHE.get(key)
    now = time.time()
    if rec and now - rec["ts"] < SEARCH_CACHE_TTL:
        return rec["value"]
    val = _safe_search(team_name, max_results=max_results)
    _SEARCH_CACHE[key] = {"ts": now, "value": val}
    return val

def _safe_search(team_name: str, max_results: int = 60):
    if _search_func:
        try:
            res = _search_func(team_name, max_results=max_results)
            if isinstance(res, (list, tuple)) and len(res) == 2:
                return res[0] or [], res[1] or []
        except Exception:
            pass
    if _legacy_single_fetch:
        try:
            longs = _legacy_single_fetch(team_name, max_results=max_results) or []
            return [], longs
        except Exception:
            pass
    return [], []

# ---- 제목 정리 ----
NEGATIVE_BY_TEAM = {
    "LG": ["전자","에너지솔루션","엔솔","디스플레이","u+","유플러스","생활건강","하우시스","이노텍","헬로비전","그룹","기업분석","그램","oled"],
}
BASEBALL_SIGNALS = ["KBO","프로야구","야구","하이라이트","경기","1군","2군","퓨처스","중계","리그",
                    "스포츠","타이거즈","트윈스","베어스","위즈","자이언츠","다이노스","라이온즈","히어로즈","랜더스"]

HASHTAG_RE = re.compile(r"(?:^|\s)#\S+")
SPACE_RE   = re.compile(r"\s{2,}")
BAR_TRIM   = re.compile(r"(^[\s\|\-·]+|[\s\|\-·]+$)")

def _clean_title(txt: str) -> str:
    if not txt: return ""
    t = HASHTAG_RE.sub(" ", txt)
    t = SPACE_RE.sub(" ", t)
    t = BAR_TRIM.sub("", t.strip())
    return t

def _title_ok(v: dict, team_key: str, team_full: str) -> bool:
    title = v.get("title") or ""
    ch_id = (v.get("channel_id") or "").strip()
    s = title.lower()

    # 0) 공식 채널이면 무조건 통과
    officials = set(OFFICIAL_MAP.get(team_full, [])) | set(OFFICIAL_MAP.get(team_key, []))
    if ch_id and ch_id in officials:
        return True

    # 1) 팀별 금칙어(LG 전자 등)
    if team_key in NEGATIVE_BY_TEAM:
        for bad in NEGATIVE_BY_TEAM[team_key]:
            if bad.lower() in s:
                return False

    # 2) 야구/팀 신호어 중 하나라도 포함되면 통과
    if team_full.lower() in s or team_key.lower() in s:
        return True
    return any(k.lower() in s for k in BASEBALL_SIGNALS)

def _postprocess(videos, team_key: str, team_full: str):
    out = []
    for v in videos or []:
        t = _clean_title(v.get("title"))
        item = {
            "title": t,
            "url": v.get("url") or v.get("watch_url"),
            "thumbnail": v.get("thumbnail") or v.get("thumbnail_url"),
            "channel": v.get("channel") or v.get("channelTitle") or v.get("uploader"),
            "channel_id": v.get("channel_id"),
            "published_at": v.get("published_at") or v.get("publish_date"),
            "duration": v.get("duration"),          # ISO 유지 (프런트가 변환)
            "seconds": v.get("seconds"),
        }
        if _title_ok(item, team_key, team_full):
            out.append(item)
    return out

@app.get("/healthz")
def health():
    return "ok", 200

@app.route("/")
@app.route("/club")
def index():
    team_param = (request.args.get("team") or "").strip() or "LG"
    team_name = TEAM_MAP.get(team_param, team_param)
    return render_template("Club.html", team_name=team_name, teams=TEAM_MAP)

@app.route("/search", methods=["GET", "POST"])
def search():
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        club = (data.get("club") or "").strip()
        force = bool(data.get("force"))
    else:
        club = (request.args.get("team") or request.args.get("club") or "").strip()
        force = bool(request.args.get("force"))

    if not club:
        return jsonify({"shorts": [], "short": [], "long": []})

    # 약어→풀네임
    team_key = None
    club_full = club
    for k, full in TEAM_MAP.items():
        if club == k or club == full:
            team_key = k
            club_full = full
            break
    if not team_key:
        team_key = club
        club_full = TEAM_MAP.get(club, club)

    # 캐시 무효화
    cache_key = f"{club_full}::60"
    if force:
        _SEARCH_CACHE.pop(cache_key, None)

    shorts, longs = _cached_safe_search(club_full, max_results=60)

    shorts = _postprocess(shorts, team_key, club_full)
    longs  = _postprocess(longs,  team_key, club_full)

    return jsonify({"shorts": shorts, "short": shorts, "long": longs})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
