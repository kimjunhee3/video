import os
import re
from typing import List, Dict, Any, Optional, Tuple

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# 숏폼 기준(초) - 환경변수로 조정 가능
SHORT_MAX_SEC = int(os.getenv("SHORT_MAX_SEC", "75"))

# ISO8601 PT#M#S → 초
_DURATION_RE = re.compile(
    r"P(?:(?P<days>\d+)D)?"
    r"(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?"
)

# ---------------------------
# 공식 채널 검색을 위한 팀 정보 (Club_flask.py에서 복사하여 내부적으로 사용)
# ---------------------------
TEAM_MAP = {
    "LG": "LG 트윈스", "두산": "두산 베어스", "SSG": "SSG 랜더스", "키움": "키움 히어로즈",
    "KT": "KT 위즈", "KIA": "KIA 타이거즈", "삼성": "삼성 라이온즈", "NC": "NC 다이노스",
    "롯데": "롯데 자이언츠", "한화": "한화 이글스",
}

OFFICIAL_CHANNEL_IDS = {
    "KT":  ["UCvScyjGkBUx2CJDMNAi9Twg"], "한화": ["UCdq4Ji3772xudYRUatdzRrg"],
    "LG":  ["UCL6QZZxb-HR4hCh_eFAnQWA"], "두산": ["UCsebzRfMhwYfjeBIxNX1brg"],
    "KIA":  ["UCKp8knO8a6tSI1oaLjfd9XA"], "SSG":  ["UCt8iRtgjVqm5rJHNl1TUojg"],
    "삼성": ["UCMWAku3a3h65QpLm63Jf2pw"], "키움": ["UC_MA8-XEaVmvyayPzG66IKg"],
    "NC":  ["UC8_FRgynMX8wlGsU6Jh3zKg"], "롯데": ["UCAZQZdSY5_YrziMPqXi-Zfw"],
}

# ---------------------------
# 유틸리티 함수 (원본 유지)
# ---------------------------

def _iso8601_to_seconds(duration: str) -> int:
    if not duration:
        return 0
    m = _DURATION_RE.fullmatch(duration)
    if not m:
        return 0
    days = int(m.group("days") or 0)
    hours = int(m.group("hours") or 0)
    minutes = int(m.group("minutes") or 0)
    seconds = int(m.group("seconds") or 0)
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def _build_yt_client() -> Optional[Any]:
    api_key = os.getenv("YT_API_KEY") or os.getenv("YOUTUBE_API_KEY")
    if not api_key:
        return None
    # Render 같은 환경에선 cache_discovery=False 권장
    return build("youtube", "v3", developerKey=api_key, cache_discovery=False)


def _normalize_query(team: str) -> str:
    team = (team or "").strip()
    if not team:
        return ""
    # 기본적으로 하이라이트 중심으로
    return f"{team} 하이라이트"


def search_videos_by_team(team_name: str, max_results: int = 24) -> Tuple[List[Dict], List[Dict]]:
    """
    [수정] 팀명을 기반으로 팀 키(LG, KT 등)를 찾아 공식 채널 우선 검색을 수행합니다.
    """
    team_name = (team_name or "").strip()
    if not team_name:
        return [], []

    yt = _build_yt_client()
    if yt is None:
        return [], []

    all_base_map: Dict[str, Dict[str, Any]] = {}
    
    # team_name(풀네임)을 team_key(약어)로 매핑
    team_key = next((k for k, v in TEAM_MAP.items() if v == team_name), None)

    # 1. [우선] 공식 채널 검색
    channel_ids = OFFICIAL_CHANNEL_IDS.get(team_key, []) if team_key else []
    official_count_limit = max(1, min(max_results * 2 // 3, 30)) # 전체 2/3 정도 할당

    if channel_ids:
        for channel_id in channel_ids:
            try:
                # search API를 사용하여 특정 채널의 최신 영상 검색
                channel_search_resp = (
                    yt.search()
                    .list(
                        part="snippet",
                        channelId=channel_id,
                        type="video",
                        maxResults=official_count_limit, 
                        order="date", # 최신순
                    )
                    .execute()
                )
                
                for it in channel_search_resp.get("items", []):
                    vid = (it.get("id") or {}).get("videoId")
                    if not vid or vid in all_base_map:
                        continue
                    sn = it.get("snippet", {})
                    thumbs = sn.get("thumbnails") or {}
                    thumb = (thumbs.get("high") or {}).get("url") or (thumbs.get("default") or {}).get("url")
                    
                    all_base_map[vid] = {
                        "title": sn.get("title"),
                        "videoId": vid,
                        "thumbnail": thumb,
                        "channelTitle": sn.get("channelTitle"),
                        "channelId": sn.get("channelId"),
                        "publishedAt": sn.get("publishedAt"),
                        "url": f"https://www.youtube.com/watch?v={vid}",
                    }

            except HttpError as e:
                # 에러 발생 시 로그를 남기고 다음 채널로 이동
                print(f"Error fetching official channel {channel_id}: {e}") 
            except Exception as e:
                print(f"Unexpected error fetching official channel {channel_id}: {e}") 

    # 2. [보조] 일반 검색 (하이라이트 쿼리)
    remaining_results = max_results - len(all_base_map)
    # 여유분 15개 추가 (필터링에 대비)
    if remaining_results > 0:
        q = _normalize_query(team_name) 
        
        try:
            # 일반 검색으로 부족한 개수만큼 채우기 (최대 max_results 초과하지 않도록)
            general_search_resp = (
                yt.search()
                .list(
                    part="snippet",
                    q=q,
                    type="video",
                    maxResults=remaining_results,
                    order="date",
                    safeSearch="none",
                )
                .execute()
            )
            
            for it in general_search_resp.get("items", []):
                vid = (it.get("id") or {}).get("videoId")
                if not vid or vid in all_base_map: # 중복 제거
                    continue
                sn = it.get("snippet", {})
                thumbs = sn.get("thumbnails") or {}
                thumb = (thumbs.get("high") or {}).get("url") or (thumbs.get("default") or {}).get("url")
                
                all_base_map[vid] = {
                    "title": sn.get("title"),
                    "videoId": vid,
                    "thumbnail": thumb,
                    "channelTitle": sn.get("channelTitle"),
                    "channelId": sn.get("channelId"),
                    "publishedAt": sn.get("publishedAt"),
                    "url": f"https://www.youtube.com/watch?v={vid}",
                }
                
        except HttpError as e:
            print(f"Error fetching general search: {e}")
        except Exception as e:
            print(f"Unexpected error fetching general search: {e}")

    ids = list(all_base_map.keys())

    # 3. 길이 조회 및 분류 (원본 유지)
    shorts: List[Dict[str, Any]] = []
    longs: List[Dict[str, Any]] = []

    if not ids:
        return [], []

    try:
        detail_resp = yt.videos().list(part="contentDetails", id=",".join(ids)).execute()
        for v in detail_resp.get("items", []):
            vid = v.get("id")
            dur_iso = (v.get("contentDetails") or {}).get("duration")
            secs = _iso8601_to_seconds(dur_iso)
            data = {**all_base_map.get(vid, {}), "duration": dur_iso, "seconds": secs}
            
            # publishedAt이 없는 데이터는 제외 (필터링을 위해)
            if not data.get("publishedAt"): continue

            if secs <= SHORT_MAX_SEC:
                shorts.append(data)
            else:
                longs.append(data)
    except Exception:
        # 길이 조회 실패 시 전부 롱폼으로 처리
        longs = [
            {**data, "duration": None, "seconds": 0} 
            for vid, data in all_base_map.items() if data.get("publishedAt")
        ]

    return shorts, longs
