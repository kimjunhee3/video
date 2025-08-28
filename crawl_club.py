# crawl_club.py
import os
import re
from typing import List, Dict, Any, Optional, Tuple

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SHORT_MAX_SEC = int(os.getenv("SHORT_MAX_SEC", "75"))  # 숏폼 기준(초)

def _build_yt_client() -> Optional[Any]:
    api_key = os.getenv("YT_API_KEY") or os.getenv("YOUTUBE_API_KEY")
    if not api_key:
        return None
    # Render 같은 환경에선 cache_discovery=False 권장
    return build("youtube", "v3", developerKey=api_key, cache_discovery=False)

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

def _normalize_query(team: str) -> str:
    team = (team or "").strip()
    if not team:
        return ""
    # 기본적으로 하이라이트 중심으로
    return f"{team} 하이라이트"

def search_videos_by_team(team_name: str, max_results: int = 24) -> Tuple[List[Dict], List[Dict]]:
    """
    팀 이름으로 영상을 검색하고, 길이를 조회해서 (shorts, longs) 두 리스트로 나눠 반환.
    각 아이템: {title, videoId, thumbnail, channelTitle, publishedAt, url, duration, seconds}
    """
    team_name = (team_name or "").strip()
    if not team_name:
        return [], []

    yt = _build_yt_client()
    if yt is None:
        return [], []

    q = _normalize_query(team_name)

    try:
        # 1) search로 비디오 id 모으기
        search_resp = (
            yt.search()
            .list(
                part="snippet",
                q=q,
                type="video",
                maxResults=max(1, min(max_results, 50)),
                order="date",
                safeSearch="none",
            )
            .execute()
        )
    except HttpError:
        return [], []
    except Exception:
        return [], []

    items = search_resp.get("items", [])
    if not items:
        return [], []

    # id 목록
    ids = []
    base_map: Dict[str, Dict[str, Any]] = {}
    for it in items:
        vid = (it.get("id") or {}).get("videoId")
        if not vid:
            continue
        sn = it.get("snippet", {})
        thumbs = sn.get("thumbnails") or {}
        thumb = (thumbs.get("high") or {}).get("url") or (thumbs.get("default") or {}).get("url")
        base_map[vid] = {
            "title": sn.get("title"),
            "videoId": vid,
            "thumbnail": thumb,
            "channelTitle": sn.get("channelTitle"),
            "publishedAt": sn.get("publishedAt"),
            "url": f"https://www.youtube.com/watch?v={vid}",
        }
        ids.append(vid)

    # 2) videos.list로 길이 가져오기
    shorts, longs = [], []
    try:
        detail_resp = (
            yt.videos()
            .list(part="contentDetails", id=",".join(ids))
            .execute()
        )
        for v in detail_resp.get("items", []):
            vid = v.get("id")
            dur_iso = (v.get("contentDetails") or {}).get("duration")
            secs = _iso8601_to_seconds(dur_iso)
            data = {**base_map.get(vid, {}), "duration": dur_iso, "seconds": secs}
            if secs <= SHORT_MAX_SEC:
                shorts.append(data)
            else:
                longs.append(data)
    except Exception:
        # 길이 조회 실패 시 전부 롱폼으로 처리
        longs = list(base_map.values())

    return shorts, longs

