from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
import re
import os
import time
import json
from typing import Tuple

# 간단 in-memory TTL 캐시 (프로세스 내)
_SEARCH_CACHE = {}
SEARCH_CACHE_TTL = int(os.getenv("SEARCH_CACHE_TTL", "60"))  # seconds

# ---------------------------
# 1) 앱/기본 설정
# ---------------------------
app = Flask(__name__)
CORS(app)

# 팀 약어 -> 한글 팀명 매핑
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

try:
    OFFICIAL_CHANNELS = json.loads(os.getenv("OFFICIAL_CHANNELS_JSON", "{}"))
except Exception:
    OFFICIAL_CHANNELS = {}

def _official_ids(team_key: str, team_full: str):
    ids = set(OFFICIAL_CHANNELS.get(team_key, [])) | set(OFFICIAL_CHANNELS.get(team_full, []))
    # KBO 공식 채널(중립)도 가중치 약하게 주고 싶다면 OFFICIAL_CHANNELS에 "KBO" 키로 추가
    return ids

def _norm(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").lower())

def _teams_in(title: str):
    t = _norm(title)
    hits = []
    for k, full in TEAM_MAP.items():
        if _norm(full) in t or _norm(k) in t:
            hits.append(k)
    return hits

def _score(video: dict, team_key: str, team_full: str) -> int:
    title = video.get("title", "") or ""
    ch_id = (video.get("channel_id") or "").strip()
    ch_name = (video.get("channel") or video.get("channelTitle") or "").lower()
    score = 0
    # 1) 공식 채널 강한 가중치
    if ch_id in _official_ids(team_key, team_full):
        score += 100
    # 2) 제목에 팀명 포함/선행 가중치
    nt = _norm(title)
    if _norm(team_full) in nt or _norm(team_key) in nt:
        score += 30
        if nt.startswith(_norm(team_full)) or nt.startswith(_norm(team_key)):
            score += 15
    # 3) 'vs' 케이스: 양 팀 모두 언급되고, 공식/중립(KBO) 아닌 채널이면
    hits = _teams_in(title)
    if len(hits) >= 2 and ch_id not in _official_ids(team_key, team_full) and "kbo" not in ch_name:
        # 우리 팀이 제목에서 먼저 나오지 않으면 감점
        pos_self = min([p for p in [nt.find(_norm(team_full)), nt.find(_norm(team_key))] if p != -1] or [10**9])
        pos_others = min([nt.find(_norm(TEAM_MAP[h])) for h in hits if h != team_key] or [10**9])
        if pos_self >= pos_others:
            score -= 40
    return score

def _rank_and_filter(videos: list, team_key: str, team_full: str, drop_negative=True):
    vids = list(videos or [])
    vids.sort(key=lambda v: _score(v, team_key, team_full), reverse=True)
    if drop_negative:
        vids = [v for v in vids if _score(v, team_key, team_full) >= 0]
    return vids

# ---------------------------
# 2) 크롤러/검색 함수 폴백 로딩
#    - search_videos_by_team(team, max_results) -> (shorts, longs)
#    - 없으면 get_youtube_videos / crawl_youtube.search_youtube 로 폴백
# ---------------------------
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
    if rec and (now - rec["ts"] < SEARCH_CACHE_TTL):
        return rec["value"]
    val = _safe_search(team_name, max_results=max_results)
    _SEARCH_CACHE[key] = {"ts": now, "value": val}
    return val

def _safe_search(team_name: str, max_results: int = 60):
    """
    통합 래퍼:
    - 우선 search_videos_by_team 사용 (shorts, longs)
    - 없다면 단일 함수로 longs만 채움
    """
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

# ---------------------------
# 3) 제목 정제/필터 (LG 모호성 제거 + 해시태그 제거)
# ---------------------------
NEGATIVE_BY_TEAM = {
    "LG": [
        "전자", "에너지솔루션", "엔솔", "디스플레이", "u+", "유플러스",
        "생활건강", "하우시스", "이노텍", "헬로비전", "그룹", "기업분석", "그램", "oled",
    ],
}

BASEBALL_SIGNALS = [
    "KBO", "프로야구", "야구", "하이라이트", "경기", "1군", "2군", "퓨처스", "중계", "리그",
    "스포츠", "타이거즈", "트윈스", "베어스", "위즈", "자이언츠", "다이노스", "라이온즈", "히어로즈", "랜더스",
]

HASHTAG_RE = re.compile(r"(?:^|\s)#\S+")
SPACE_RE = re.compile(r"\s{2,}")
BAR_TRIM = re.compile(r"(^[\s\|\-·]+|[\s\|\-·]+$)")

def _clean_title(txt: str) -> str:
    if not txt:
        return ""
    t = HASHTAG_RE.sub(" ", txt)
    t = SPACE_RE.sub(" ", t)
    t = BAR_TRIM.sub("", t.strip())
    return t

def _title_ok(title: str, team_key: str, team_full: str) -> bool:
    if not title:
        return False
    s = title.lower()
    if team_key in NEGATIVE_BY_TEAM:
        for bad in NEGATIVE_BY_TEAM[team_key]:
            if bad.lower() in s:
                return False
    if team_full.lower() in s or team_key.lower() in s:
        return True
    return any(k.lower() in s for k in BASEBALL_SIGNALS)

def _fmt_duration(sec):
    try:
        s = int(sec or 0)
    except Exception:
        return None
    if s <= 0:
        return None
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"

def _postprocess(videos, team_key: str, team_full: str):
    out = []
    for v in videos or []:
        t = _clean_title(v.get("title"))
        if not _title_ok(t, team_key, team_full):
            continue
        out.append({
            "title": t,
            "url": v.get("url") or v.get("watch_url"),
            "thumbnail": v.get("thumbnail") or v.get("thumbnail_url"),
            "channel": v.get("channel") or v.get("channelTitle") or v.get("uploader"),
            "channel_id": v.get("channel_id"),
            "published_at": v.get("published_at") or v.get("publish_date"),
            "duration": v.get("duration") or _fmt_duration(v.get("seconds")),
        })
    return out

# ---------------------------
# 4) 라우트
# ---------------------------
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
    # 입력 파싱 (POST JSON 우선, 없으면 GET 쿼리)
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        club = (data.get("club") or "").strip()
        force = bool(data.get("force"))
    else:
        club = (request.args.get("team") or request.args.get("club") or "").strip()
        force = False

    if not club:
        return jsonify({"shorts": [], "short": [], "long": []})

    # 약어 -> 풀네임 매핑
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

    # 강제 갱신 처리
    cache_key = f"{club_full}::60"
    if force:
        _SEARCH_CACHE.pop(cache_key, None)

    # 검색(캐시 적용) 및 후처리
    shorts, longs = _cached_safe_search(club_full, max_results=60)
    shorts = _postprocess(shorts, team_key, club_full)
    longs  = _postprocess(longs,  team_key, club_full)
    shorts = _rank_and_filter(shorts, team_key, club_full)
    longs  = _rank_and_filter(longs,  team_key, club_full)

    return jsonify({"shorts": shorts, "short": shorts, "long": longs})
