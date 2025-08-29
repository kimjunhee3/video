# crawl_club.py
import os
import re
from typing import List, Dict, Any, Optional, Tuple

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SHORT_MAX_SEC = int(os.getenv("SHORT_MAX_SEC", "75"))  # 숏폼 기준(초)

# 구단별 공식 유튜브 채널 ID (네가 준 값)
club_youtube_channels = {
    "키움 히어로즈": "UC_MA8-XEaVmvyayPzG66IKg",
    "NC 다이노스":  "UC8_FRgynMX8wlGsU6Jh3zKg",
    "LG 트윈스":    "UCL6QZZxb-HR4hCh_eFAnQWA",
    "롯데 자이언츠":"UCAZQZdSY5_YrziMPqXi-Zfw",
    "KT 위즈":     "UCvScyjGkBUx2CJDMNAi9Twg",
    "삼성 라이온즈":"UCMWAku3a3h65QpLm63Jf2pw",
    "KIA 타이거즈":"UCKp8knO8a6tSI1oaLjfd9XA",
    "두산 베어스": "UCsebzRfMhwYfjeBIxNX1brg",
    "SSG 랜더스": "UCt8iRtgjVqm5rJHNl1TUojg",
    "한화 이글스":"UCdq4Ji3772xudYRUatdzRrg",
}

def _build_yt_client() -> Optional[Any]:
    api_key = os.getenv("YT_API_KEY") or os.getenv("YOUTUBE_API_KEY")
    if not api_key:
        return None
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
    return f"{team} 하이라이트"

def _collect_from_channel(yt, channel_id: str, max_results: int) -> List[Dict[str, Any]]:
    """공식 채널에서 최신 업로드 가져오기 (snippet + duration)"""
    if not channel_id:
        return []
    try:
        # 1) 채널 최신 업로드 videoId 수집
        search = yt.search().list(
            part="id",
            channelId=channel_id,
            type="video",
            order="date",
            maxResults=min(max_results, 50),
            safeSearch="none",
        ).execute()
    except Exception:
        return []

    ids: List[str] = [it["id"]["videoId"] for it in search.get("items", []) if it.get("id", {}).get("videoId")]
    if not ids:
        return []

    # 2) videos.list 로 snippet + contentDetails
    out: List[Dict[str, Any]] = []
    try:
        detail = yt.videos().list(part="snippet,contentDetails", id=",".join(ids)).execute()
        for v in detail.get("items", []):
            sn = v.get("snippet", {})
            cd = v.get("contentDetails", {})
            thumbs = sn.get("thumbnails") or {}
            thumb = (thumbs.get("high") or {}).get("url") or (thumbs.get("default") or {}).get("url")
            dur_iso = cd.get("duration")
            out.append({
                "title": sn.get("title"),
                "thumbnail": thumb,
                "channelTitle": sn.get("channelTitle"),
                "publishedAt": sn.get("publishedAt"),
                "url": f"https://www.youtube.com/watch?v={v.get('id')}",
                "duration": dur_iso,
                "seconds": _iso8601_to_seconds(dur_iso),
            })
    except Exception:
        # snippet만으로 최소한 채워주고, 길이 모르면 롱폼 취급은 Flask에서 처리
        pass
    return out

def _collect_by_search(yt, q: str, max_results: int) -> List[Dict[str, Any]]:
    """일반 검색으로 보강 (강화 전 방식)"""
    try:
        resp = yt.search().list(
            part="snippet",
            q=q,
            type="video",
            order="date",
            maxResults=min(max_results, 50),
            safeSearch="none",
        ).execute()
    except Exception:
        return []

    ids: List[str] = []
    base: Dict[str, Dict[str, Any]] = {}
    for it in resp.get("items", []):
        vid = (it.get("id") or {}).get("videoId")
        if not vid:
            continue
        sn = it.get("snippet", {})
        thumbs = sn.get("thumbnails") or {}
        thumb = (thumbs.get("high") or {}).get("url") or (thumbs.get("default") or {}).get("url")
        base[vid] = {
            "title": sn.get("title"),
            "thumbnail": thumb,
            "channelTitle": sn.get("channelTitle"),
            "publishedAt": sn.get("publishedAt"),
            "url": f"https://www.youtube.com/watch?v={vid}",
        }
        ids.append(vid)

    # 길이 붙이기
    try:
        detail = yt.videos().list(part="contentDetails", id=",".join(ids)).execute()
        out: List[Dict[str, Any]] = []
        for v in detail.get("items", []):
            vid = v.get("id")
            dur_iso = (v.get("contentDetails") or {}).get("duration")
            secs = _iso8601_to_seconds(dur_iso)
            out.append({**base.get(vid, {}), "duration": dur_iso, "seconds": secs})
        return out
    except Exception:
        return list(base.values())

def search_videos_by_team(team_name: str, max_results: int = 24) -> Tuple[List[Dict], List[Dict]]:
    """
    1) 구단 공식 채널에서 최근 업로드 우선 수집
    2) 부족하면 '팀명 하이라이트' 일반 검색으로 보강
    3) 길이로 숏/롱 분리
    """
    team_name = (team_name or "").strip()
    if not team_name:
        return [], []

    yt = _build_yt_client()
    if yt is None:
        return [], []

    videos: List[Dict[str, Any]] = []

    # 공식 채널 우선
    ch_id = club_youtube_channels.get(team_name)
    if ch_id:
        videos += _collect_from_channel(yt, ch_id, max_results=max_results)

    # 그래도 부족하면 일반 검색 폴백
    if len(videos) < max_results:
        q = _normalize_query(team_name)
        videos += _collect_by_search(yt, q, max_results=max_results - len(videos))

    # 길이 기준으로 분리
    shorts, longs = [], []
    for v in videos:
        secs = int(v.get("seconds") or 0)
        if secs and secs <= SHORT_MAX_SEC:
            shorts.append(v)
        else:
            longs.append(v)

    return shorts, longs


def yt_self_test():
    yt = _build_yt_client()
    if yt is None:
        return {"ok": False, "where": "build", "error": "NO_API_KEY"}
    try:
        r = yt.search().list(part="id", q="KBO", type="video", maxResults=1).execute()
        return {"ok": True, "items": len(r.get("items", []))}
    except Exception as e:
        import traceback
        traceback.print_exc()
        status = getattr(getattr(e, "resp", None), "status", None)
        msg = getattr(e, "content", b"")
        if isinstance(msg, (bytes, bytearray)):
            try:
                msg = msg.decode("utf-8", "ignore")
            except Exception:
                msg = str(msg)
        return {"ok": False, "where": "http", "status": status, "message": str(msg)[:300]}

# (구버전 호환) 롱폼만
def get_youtube_videos(team_name: str, max_results: int = 60) -> List[Dict]:
    _, longs = search_videos_by_team(team_name, max_results=max_results)
    return longs
