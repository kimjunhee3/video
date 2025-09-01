from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
import re
import logging

# ---------------------------
# 1) 앱/기본 설정
# ---------------------------
app = Flask(__name__)
CORS(app)

# Flask 로거 레벨 (필요시 INFO/DEBUG로 조정)
app.logger.setLevel(logging.INFO)

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
# ---------------------------
_search_func = None
_legacy_single_fetch = None

try:
    # (shorts, longs) 튜플을 반환하는 최신 함수
    from crawl_club import search_videos_by_team as _search_func  # type: ignore
except Exception:
    _search_func = None

if _search_func is None:
    # 단일 리스트만 반환하는 과거 함수와의 호환
    try:
        from crawl_club import get_youtube_videos as _legacy_single_fetch  # type: ignore
    except Exception:
        _legacy_single_fetch = None
        try:
            from crawl_youtube import search_youtube as _legacy_single_fetch  # type: ignore
        except Exception:
            _legacy_single_fetch = None


def _safe_search(team_name: str, max_results: int = 60):
    """
    통합 래퍼:
    - 우선 최신 search_videos_by_team(team, max_results) 사용 (shorts, longs)
    - 없으면 단일 함수로 longs만 채움
    - 모두 실패하면 빈 리스트
    """
    if _search_func:
        try:
            res = _search_func(team_name, max_results=max_results)
            if isinstance(res, (list, tuple)) and len(res) == 2:
                return res[0] or [], res[1] or []
        except Exception as e:
            app.logger.warning("search_videos_by_team failed: %s", e)

    if _legacy_single_fetch:
        try:
            longs = _legacy_single_fetch(team_name, max_results=max_results) or []
            return [], longs
        except Exception as e:
            app.logger.warning("legacy fetch failed: %s", e)

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

# ✅ 전역 정규식 정의 (빠져있어 NameError가 났던 부분)
HASHTAG_CUT = re.compile(r"\s*[#＃].*$")
SPACE_RE    = re.compile(r"\s+")                               # 다중 공백 → 한 칸
BAR_TRIM    = re.compile(r"^[\s\-\|·~]+|[\s\-\|·~]+$")         # 양끝 구분자/공백 제거


def _clean_title(txt: str) -> str:
    """
    제목 정리:
    1) 해시태그(#, ＃) 이후 제거
    2) 다중 공백 정리
    3) 양끝 구분자/공백 제거
    """
    if not txt:
        return ""
    t = HASHTAG_CUT.sub("", txt)
    t = SPACE_RE.sub(" ", t)
    t = BAR_TRIM.sub("", t.strip())
    return t


def _title_ok(title: str, team_key: str, team_full: str) -> bool:
    if not title:
        return False
    s = title.lower()

    # 팀별 금칙어
    if team_key in NEGATIVE_BY_TEAM:
        for bad in NEGATIVE_BY_TEAM[team_key]:
            if bad.lower() in s:
                return False

    # 야구 신호어 or 팀명 신호어 중 하나는 포함
    if team_full.lower() in s or team_key.lower() in s:
        return True
    return any(k.lower() in s for k in BASEBALL_SIGNALS)


def _postprocess(videos, team_key: str, team_full: str):
    """
    - 제목 정리/필터
    - 실패한 아이템은 건너뛰어 전체 500 방지
    """
    out = []
    for v in videos or []:
        try:
            t = _clean_title(v.get("title") or "")
            if not _title_ok(t, team_key, team_full):
                continue
            out.append({
                "title": t,
                "url": v.get("url"),
                "thumbnail": v.get("thumbnail"),
                "channelTitle": v.get("channelTitle"),
                "seconds": v.get("seconds"),
            })
        except Exception as e:
            app.logger.warning("postprocess drop: %s / raw=%s", e, v)
            continue
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

    # 약어 → 한글 풀네임 매핑
    team_key = None
    club_full = None
    for k, full in TEAM_MAP.items():
        if club == k or club == full:
            team_key = k
            club_full = full
            break
    if not team_key:
        # 모르면 그대로 사용
        team_key = club
        club_full = TEAM_MAP.get(club, club)

    # 검색
    shorts, longs = _safe_search(club_full, max_results=60)

    # 정제/필터
    shorts = _postprocess(shorts, team_key, club_full)
    longs  = _postprocess(longs,  team_key, club_full)

    return jsonify({"short": shorts, "long": longs})


# ---------------------------
# 5) 로컬 실행
# ---------------------------
if __name__ == "__main__":
    # Render 환경에선 PORT 환경변수를 쓰지만, 로컬 디폴트는 5000
    import os
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
