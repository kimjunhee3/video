from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
import re

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

# ---------------------------
# 2) 크롤러/검색 함수 폴백 로딩
#    - search_videos_by_team(team, max_results) -> (shorts, longs)
#    - 없으면 get_youtube_videos / crawl_youtube.search_youtube 로 폴백
# ---------------------------
_search_func = None
_legacy_single_fetch = None

try:
    # 이상적: (shorts, longs) 튜플 반환
    from crawl_club import search_videos_by_team as _search_func  # type: ignore
except Exception:
    _search_func = None

if _search_func is None:
    # 단일 리스트만 반환하는 과거 함수들
    try:
        from crawl_club import get_youtube_videos as _legacy_single_fetch  # type: ignore
    except Exception:
        _legacy_single_fetch = None
        try:
            # 과거 모듈명
            from crawl_youtube import search_youtube as _legacy_single_fetch  # type: ignore
        except Exception:
            _legacy_single_fetch = None

def _safe_search(team_name: str, max_results: int = 60):
    """
    통합 래퍼:
    - 우선 최신 search_videos_by_team 사용 (shorts, longs)
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
    # LG 관련 비야구 금칙어
    "LG": [
        "전자", "에너지솔루션", "엔솔", "디스플레이", "u+", "유플러스",
        "생활건강", "하우시스", "이노텍", "헬로비전", "그룹", "기업분석", "그램", "oled",
    ],
}

BASEBALL_SIGNALS = [
    "KBO", "프로야구", "야구", "하이라이트", "경기", "1군", "2군", "퓨처스", "중계", "리그",
    "스포츠", "타이거즈", "트윈스", "베어스", "위즈", "자이언츠", "다이노스", "라이온즈", "히어로즈", "랜더스",
]

HASHTAG_CUT = re.compile(r"\s*[#＃].*$")

def _clean_title(txt: str) -> str:
    if not txt:
        return ""
    # 1) 첫 해시태그(# 또는 ＃) 이후를 통째로 제거
    t = HASHTAG_CUT.sub("", txt)

    # 2) 기존 정리 로직 유지
    t = SPACE_RE.sub(" ", t)          # 다중 공백 정리
    t = BAR_TRIM.sub("", t.strip())   # 양끝 구분자( | - · ) 정리
    return t

def _title_ok(title: str, team_key: str, team_full: str) -> bool:
    if not title:
        return False
    s = title.lower()

    # 금칙어(팀별)
    if team_key in NEGATIVE_BY_TEAM:
        for bad in NEGATIVE_BY_TEAM[team_key]:
            if bad.lower() in s:
                return False

    # 야구 신호어 or 팀명 신호어 포함해야 통과
    if team_full.lower() in s or team_key.lower() in s:
        return True
    return any(k.lower() in s for k in BASEBALL_SIGNALS)

def _postprocess(videos, team_key: str, team_full: str):
    out = []
    for v in videos or []:
        t = _clean_title(v.get("title"))
        if not _title_ok(t, team_key, team_full):
            continue
        out.append({
            "title": t,
            "url": v.get("url"),
            "thumbnail": v.get("thumbnail"),
            "channelTitle": v.get("channelTitle"),
            "seconds": v.get("seconds"),
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
    # 기본 팀
    team_param = (request.args.get("team") or "").strip() or "LG"
    team_name = TEAM_MAP.get(team_param, team_param)  # 약어면 한글로, 이미 한글이면 그대로
    return render_template("Club.html", team_name=team_name, teams=TEAM_MAP)

@app.post("/search")
def search():
    data = request.get_json(silent=True) or {}
    club = (data.get("club") or "").strip()
    if not club:
        return jsonify({"short": [], "long": []})

    # 약어 → 한글 풀네임
    team_key = None
    for k, full in TEAM_MAP.items():
        if club == k or club == full:
            team_key = k
            club_full = full
            break
    if not team_key:
        # 모르면 그대로 사용
        team_key = club
        club_full = TEAM_MAP.get(club, club)

    shorts, longs = _safe_search(club_full, max_results=60)

    # ✅ 정제: 해시태그 제거 + 야구 신호어 필터 + LG 금칙어 제외
    shorts = _postprocess(shorts, team_key, club_full)
    longs  = _postprocess(longs,  team_key, club_full)

    return jsonify({"short": shorts, "long": longs})


# ---------------------------
# 5) 로컬 실행
# ---------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
