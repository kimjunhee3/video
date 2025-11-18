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
# 3) 제목/채널 정제/필터
# ---------------------------

# 팀별 금칙어 (비야구 컨텍스트 제거용)
NEGATIVE_BY_TEAM = {
    # LG 관련 비야구/타 종목 금칙어
    "LG": [
        "전자", "에너지솔루션", "엔솔", "디스플레이", "u+", "유플러스",
        "생활건강", "하우시스", "이노텍", "헬로비전", "그룹", "기업분석", "그램", "oled",
        "세이커스", "sakers", "농구",
    ],
    # KT 관련 비야구/통신/기타 금칙어
    "KT": [
        "요금제", "통신", "5g", "5G", "기가인터넷", "기가 인터넷", "internet",
        "와이파이", "wifi", "와이파이6", "휴대폰", "핸드폰", "스마트폰",
        "데이터", "무제한", "약정", "ipTV", "olleh", "올레", "인터넷설치",
        "상품권", "현금지원", "지원금", "광고", "cf", "광고영상",
        "kt wiz esports", "e스포츠", "롤", "리그오브레전드",
    ],
    # 두산 그룹/산업
    "두산": [
        "두산중공업", "두산에너빌리티", "건설", "산업", "플랜트", "연봉", "면접", "채용",
        "기업분석", "재무제표", "주가",
    ],
    # SSG / 유통
    "SSG": [
        "ssg.com", "쓱닷컴", "이마트", "백화점", "마트", "창고형", "유통", "푸드마켓",
        "신세계", "스타필드",
    ],
    # 키움 / 증권
    "키움": [
        "키움증권", "영웅문", "mts", "hts", "주식", "선물옵션", "선물", "옵션",
        "투자", "운용", "펀드", "ELS", "DLS",
    ],
    # KIA / 자동차
    "KIA": [
        "기아", "기아자동차", "자동차", "ev", "전기차", "하이브리드",
        "k3", "k5", "k7", "k8", "스포티지", "쏘렌토", "셀토스", "모닝", "카니발",
    ],
    # 삼성 / 전자, 금융 등
    "삼성": [
        "삼성전자", "전자", "반도체", "디스플레이", "갤럭시", "휴대폰", "핸드폰",
        "노트북", "태블릿", "버즈", "워치", "tv", "티비", "생명", "화재",
        "연봉", "면접", "채용", "기업분석",
    ],
    # NC / 게임사
    "NC": [
        "nc소프트", "엔씨소프트", "게임", "mmorpg", "리니지", "아이온", "블소",
        "길드워", "트릭스터m", "리니지m", "리니지w",
    ],
    # 롯데 / 유통, 놀이공원
    "롯데": [
        "롯데월드", "놀이공원", "롯데마트", "롯데백화점", "롯데리아",
        "롯데시네마", "면세점", "월드타워", "아쿠아리움",
    ],
    # 한화 / 방산, 에너지
    "한화": [
        "한화솔루션", "한화에어로스페이스", "방산", "방위산업", "태양광",
        "연봉", "면접", "채용", "기업분석", "주가",
    ],
}

# 야구 관련 키워드 (제목에 등장하면 야구일 확률↑)
BASEBALL_SIGNALS = [
    "KBO", "프로야구", "야구", "하이라이트", "경기", "1군", "2군", "퓨처스", "중계", "리그",
    "스포츠", "타이거즈", "트윈스", "베어스", "위즈", "자이언츠", "다이노스", "라이온즈", "히어로즈", "랜더스",
    "선발", "불펜", "마무리", "홈런", "타석", "타자", "투수", "안타", "득점", "실점",
]

# 애매한 팀에 강하게 적용할 '야구 핵심 신호어'
BASEBALL_CORE_SIGNALS = [
    "kbo", "프로야구", "야구",
    "홈런", "타석", "타자", "투수", "안타", "득점", "실점",
    "선발", "불펜", "마무리", "타격",
]

# 팀별 공식/준공식 채널 키워드 (채널명이 이걸 포함하면 무조건 통과 + 상단 정렬)
OFFICIAL_CHANNEL_KEYWORDS = {
    "LG": [
        "lg트윈스", "lg twins", "lg_twins", "lg twins tv", "lg트윈스tv",
    ],
    "KT": [
        "kt wiz", "kt위즈", "ktwiz", "kt wiz tv", "kt wiz baseball",
    ],
    "두산": [
        "두산 베어스", "doosan bears", "두산베어스", "doosanbears",
    ],
    "SSG": [
        "ssg 랜더스", "ssg landers", "ssglanders",
    ],
    "키움": [
        "키움 히어로즈", "kiwoom heroes", "heroes tv", "kiwoomheroes",
    ],
    "KIA": [
        "kia 타이거즈", "kia tigers", "kiatigers",
    ],
    "삼성": [
        "삼성 라이온즈", "samsung lions", "samsunglions",
    ],
    "NC": [
        "nc 다이노스", "nc dinos", "ncdinos",
    ],
    "롯데": [
        "롯데 자이언츠", "lotte giants", "lottegiants",
    ],
    "한화": [
        "한화 이글스", "hanwha eagles", "hanwhaeagles",
    ],
}

# 제목 안에서 팀을 가리키는 패턴 (팀 전체/마스코트 명)
TEAM_TITLE_PATTERNS = {
    "LG": [
        "lg 트윈스", "lg트윈스", "엘지 트윈스", "엘지트윈스", "lg twins", "트윈스",
    ],
    "KT": [
        "kt wiz", "kt위즈", "케이티 위즈", "위즈", "wiz park", "위즈파크",
    ],
    "두산": [
        "두산 베어스", "두산베어스", "doosan bears", "베어스",
    ],
    "SSG": [
        "ssg 랜더스", "ssg랜더스", "ssg landers", "랜더스",
    ],
    "키움": [
        "키움 히어로즈", "키움히어로즈", "kiwoom heroes", "히어로즈",
    ],
    "KIA": [
        "kia 타이거즈", "kia타이거즈", "기아 타이거즈", "기아타이거즈", "kia tigers", "타이거즈",
    ],
    "삼성": [
        "삼성 라이온즈", "삼성라이온즈", "samsung lions", "라이온즈",
    ],
    "NC": [
        "nc 다이노스", "nc다이노스", "nc dinos", "다이노스",
    ],
    "롯데": [
        "롯데 자이언츠", "롯데자이언츠", "lotte giants", "자이언츠",
    ],
    "한화": [
        "한화 이글스", "한화이글스", "hanwha eagles", "이글스",
    ],
}

# 모든 팀 이름이 다 기업/브랜드라 사실상 전부 애매하다고 보고 처리
AMBIGUOUS_KEYS = set(TEAM_MAP.keys())

# ✅ 전역 정규식 정의
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


def _is_official_channel(channel_title: str | None, team_key: str) -> bool:
    """
    채널명이 구단 공식/준공식 채널 키워드를 포함하면 True.
    """
    if not channel_title:
        return False
    s = channel_title.lower()
    for kw in OFFICIAL_CHANNEL_KEYWORDS.get(team_key, []):
        if kw.lower() in s:
            return True
    return False


def _title_ok(title: str, team_key: str, team_full: str) -> bool:
    if not title:
        return False
    s = title.lower()

    # 1) 팀별 금칙어
    if team_key in NEGATIVE_BY_TEAM:
        for bad in NEGATIVE_BY_TEAM[team_key]:
            if bad.lower() in s:
                return False

    # 2) 농구/KBL 명시적으로 포함되면 모든 팀에서 컷 (야구 페이지니까)
    if re.search(r"(농구|kbl|프로농구)", s):
        return False

    # 3) 기업/브랜드와 겹치는 애매한 팀들에 대한 추가 규칙
    if team_key in AMBIGUOUS_KEYS:
        has_core = any(k in s for k in BASEBALL_CORE_SIGNALS)
        team_patterns = TEAM_TITLE_PATTERNS.get(team_key, [])
        has_team_word = any(p in s for p in team_patterns)

        # 야구 핵심 신호어도 없고, 팀명/마스코트 패턴도 없으면 컷
        if not (has_core or has_team_word):
            return False

    # 4) 여기까지 통과했다면, 최소한 팀/야구 관련성이 있다고 보고,
    # 팀 풀네임 또는 약어, 혹은 일반 야구 신호어가 들어가 있으면 살린다.
    full_lower = team_full.lower()
    key_lower = team_key.lower()

    if full_lower in s or key_lower in s:
        return True
    return any(k.lower() in s for k in BASEBALL_SIGNALS)


def _postprocess(videos, team_key: str, team_full: str):
    """
    - 제목 정리/필터
    - 구단 공식 채널 영상은 무조건 통과 + 리스트 상단에 배치
    - 실패한 아이템은 건너뛰어 예외 방지
    """
    official_list = []
    normal_list = []

    for v in videos or []:
        try:
            raw_title = v.get("title") or ""
            t = _clean_title(raw_title)
            channel_title = v.get("channelTitle") or ""

            is_official = _is_official_channel(channel_title, team_key)

            # 공식 채널이면 제목이 조금 애매해도 우선 통과,
            # 그 외에는 _title_ok 필터 적용
            if not is_official and not _title_ok(t, team_key, team_full):
                # 디버깅용 로그 (필요 없으면 주석 처리)
                # app.logger.info("[DROP] team=%s title=%s", team_key, t)
                continue

            item = {
                "title": t,
                "url": v.get("url"),
                "thumbnail": v.get("thumbnail"),
                "channelTitle": channel_title,
                "seconds": v.get("seconds"),
            }

            if is_official:
                official_list.append(item)
            else:
                normal_list.append(item)

        except Exception as e:
            app.logger.warning("postprocess drop: %s / raw=%s", e, v)
            continue

    # 공식 채널 영상이 항상 앞에 오도록 합치기
    return official_list + normal_list


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
