# crawl_club.py
import os
import re
import json
from typing import List, Dict, Any, Optional, Tuple
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ---- 설정값 ----
SHORT_MAX_SEC = int(os.getenv("SHORT_MAX_SEC", "75"))     # 숏폼 기준(초)
SEARCH_BACKFILL = int(os.getenv("SEARCH_BACKFILL", "60")) # 일반 검색으로 보강할 최대 개수
RECENT_PER_CHANNEL = int(os.getenv("RECENT_PER_CHANNEL", "40"))  # 공식 채널당 최근 수집

# ---- 기본(하드코딩) 공식 채널 맵: 네가 준 값 사용 ----
DEFAULT_OFFICIAL = {
    "키움 히어로즈": "UC_MA8-XEaVmvyayPzG66IKg",
    "NC 다이노스": "UC8_FRgynMX8wlGsU6Jh3zKg",
    "LG 트윈스": "UCL6QZZxb-HR4hCh_eFAnQWA",
    "롯데 자이언츠": "UCAZQZdSY5_YrziMPqXi-Zfw",
    "KT 위즈": "UCvScyjGkBUx2CJDMNAi9Twg",
    "삼성 라이온즈": "UCMWAku3a3h65QpLm63Jf2pw",
    "KIA 타이거즈": "UCKp8knO8a6tSI1oaLjfd9XA",
    "두산 베어스": "UCsebzRfMhwYfjeBIxNX1brg",
    "SSG 랜더스": "UCt8iRtgjVqm5rJHNl1TUojg",
    "한화 이글스": "UCdq4Ji3772xudYRUatdzRrg",
}

# 약어/별칭 → 정식 팀명 매핑 (키를 더 유연하게 받아들이기)
ALIASES = {
    "LG": "LG 트윈스",
    "KT": "KT 위즈",
    "두산": "두산 베어스",
    "SSG": "SSG 랜더스",
    "키움": "키움 히어로즈",
    "KIA": "KIA 타이거즈",
    "삼성": "삼성 라이온즈",
    "NC": "NC 다이노스",
    "롯데": "롯데 자이언츠",
    "한화": "한화 이글스",
}

# OFFICIAL_CHANNELS_JSON 환경변수(선택) 형식:
# {"LG":["UCL6..."], "LG 트윈스":["UCL6..."], "KBO":["UC..."]}
def _load_official_map() -> Dict[str, List[str]]:
    # 1) 환경변수 있으면 그걸 우선 사용
    raw = os.getenv("OFFICIAL_CHANNELS_JSON")
    if raw:
        try:
            data = json.loads(raw)
            # 문자열 하나만 줬을 수도 있으니 리스트로 정규화
            out: Dict[str, List[str]] = {}
            for k, v in data.items():
                if isinstance(v, list):
                    out[k] = v
                elif isinstance(v, str):
                    out[k] = [v]
            return out
        except Exception:
            pass

    # 2) 없으면 네가 준 기본값을 사용 (정식명 + 약어 키 모두 지원)
    out: Dict[str, List[str]] = {}
    for full_name, cid in DEFAULT_OFFICIAL.items():
        out.setdefault(full_name, []).append(cid)
    for alias, full in ALIASES.items():
        if full in DEFAULT_OFFICIAL:
            out.setdefault(alias, []).append(DEFAULT_OFFICIAL[full])
    return out

OFFICIAL_MAP = _load_official_map()

def _resolve_team_key(team_name: str) -> str:
    team_name = (team_name or "").strip()
    if not team_name:
        return ""
    # 이미 정식명이면 그대로 사용
    if team_name in DEFAULT_OFFICIAL or team_name in OFFICIAL_MAP:
        return team_name
    # 약어/별칭이면 정식명으로
    if team_name in ALIASES:
        return ALIASES[team_name]
    return team_name  # 모르면 그대로 사용

def _build_yt_client() -> Optional[Any]:
    # 키는 YT_API_KEY 또는 YOUTUBE_API_KEY 아무거나
    api_key = os.getenv("YT_API_KEY") or os.getenv("YOUTUBE_API_KEY")
    if not api_key:
        return None
    return build("youtube", "v3", developerKey=api_key, cache_discovery=False)

# ISO8601 → 초
_duration_re = re.compile(
    r"P(?:(?P<days>\d+)D)?"
    r"(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?"
)
def _iso8601_to_seconds(duration: str) -> int:
    if not duration:
        return 0
    m = _duration_re.fullmatch(duration)
    if not m:
        return 0
    days = int(m.group("days") or 0)
    hours = int(m.group("hours") or 0)
    minutes = int(m.group("minutes") or 0)
    seconds = int(m.group("seconds") or 0)
    return days * 86400 + hours * 3600 + minutes * 60 + seconds

def _videos_by_ids(yt, ids: List[str]) -> List[Dict]:
    out: List[Dict[str, Any]] = []
    if not ids:
        return out
    for i in range(0, len(ids), 50):
        resp = yt.videos().list(
            part="snippet,contentDetails",
            id=",".join(ids[i:i+50]),
            maxResults=50
        ).execute()
        for it in resp.get("items", []):
            sn = it.get("snippet", {})
            cd = it.get("contentDetails", {})
            sec = _iso8601_to_seconds(cd.get("duration"))
            thumbs = sn.get("thumbnails") or {}
            thumb = (
                (thumbs.get("high") or {}).get("url")
                or (thumbs.get("medium") or {}).get("url")
                or (thumbs.get("default") or {}).get("url")
            )
            out.append({
                "id": it.get("id"),
                "title": sn.get("title"),
                "url": f"https://www.youtube.com/watch?v={it.get('id')}",
                "thumbnail": thumb,
                "channelTitle": sn.get("channelTitle"),
                "channel_id": sn.get("channelId"),   # ✅ 필수
                "publish_date": sn.get("publishedAt"),
                "duration": cd.get("duration"),
                "seconds": sec,
            })
    return out

def _recent_from_channel(yt, channel_id: str, limit: int) -> List[Dict]:
    ids: List[str] = []
    token = None
    while len(ids) < limit:
        n = min(50, limit - len(ids))
        resp = yt.search().list(
            part="id",
            channelId=channel_id,
            type="video",
            order="date",
            maxResults=n,
            pageToken=token
        ).execute()
        ids += [
            it["id"]["videoId"]
            for it in resp.get("items", [])
            if it.get("id", {}).get("videoId")
        ]
        token = resp.get("nextPageToken")
        if not token:
            break
    return _videos_by_ids(yt, ids)

def _search_generic(yt, query: str, limit: int) -> List[Dict]:
    ids: List[str] = []
    token = None
    while len(ids) < limit:
        n = min(50, limit - len(ids))
        resp = yt.search().list(
            part="id",
            q=query,
            type="video",
            order="date",
            safeSearch="none",
            relevanceLanguage="ko",
            regionCode="KR",
            maxResults=n,
            pageToken=token
        ).execute()
        ids += [
            it["id"]["videoId"]
            for it in resp.get("items", [])
            if it.get("id", {}).get("videoId")
        ]
        token = resp.get("nextPageToken")
        if not token:
            break
    return _videos_by_ids(yt, ids)

def _dedup_by_id(items: List[Dict]) -> List[Dict]:
    seen = set()
    out = []
    for v in items:
        vid = v.get("id") or v.get("videoId") or v.get("url")
        if not vid or vid in seen:
            continue
        seen.add(vid)
        out.append(v)
    return out

def search_videos_by_team(team_name: str, max_results: int = 60) -> Tuple[List[Dict], List[Dict]]:
    """
    returns (shorts, longs)
    각 item: title, url, thumbnail, channelTitle, channel_id, publish_date, duration, seconds
    """
    team_name = (team_name or "").strip()
    if not team_name:
        return [], []

    yt = _build_yt_client()
    if yt is None:
        # API 키 없으면 빈 리스트
        return [], []

    # 1) 공식 채널에서 우선 대량 수집
    resolved = _resolve_team_key(team_name)
    official_ids = OFFICIAL_MAP.get(resolved, []) + OFFICIAL_MAP.get(ALIASES.get(resolved, ""), [])
    official_ids = [cid for cid in official_ids if cid]  # 정리

    official_vids: List[Dict] = []
    for cid in official_ids:
        try:
            official_vids += _recent_from_channel(yt, cid, limit=RECENT_PER_CHANNEL)
        except HttpError:
            continue
        except Exception:
            continue

    # 2) 일반 검색으로 보강 (팀명 + 하이라이트 / KBO)
    q = f"{resolved} 하이라이트"
    generic_vids: List[Dict] = []
    try:
        generic_vids = _search_generic(yt, q, limit=SEARCH_BACKFILL)
    except Exception:
        generic_vids = []

    # 3) 합치고 중복 제거, 최신순 정렬
    merged = _dedup_by_id(official_vids + generic_vids)
    merged.sort(key=lambda v: (v.get("publish_date") or ""), reverse=True)

    # 4) 숏폼/롱폼 분리
    shorts = [v for v in merged if (v.get("seconds") or 0) <= SHORT_MAX_SEC]
    longs  = [v for v in merged if (v.get("seconds") or 0) >  SHORT_MAX_SEC]

    # (선택) 개수 제한이 필요하면 여기서 슬라이스
    if max_results and max_results > 0:
        shorts = shorts[:max_results]
        longs  = longs[:max_results]

    return shorts, longs

# 구버전 호환 (롱폼만)
def get_youtube_videos(team_name: str, max_results: int = 60) -> List[Dict]:
    _, longs = search_videos_by_team(team_name, max_results=max_results)
    return longs

