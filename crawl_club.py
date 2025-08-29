# crawl_club.py
import os, re, json
from typing import List, Dict, Any, Optional, Tuple
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ==== 튜닝 파라미터 ====
SHORT_MAX_SEC = int(os.getenv("SHORT_MAX_SEC", "75"))           # 숏폼 기준(초)
RECENT_PER_CHANNEL = int(os.getenv("RECENT_PER_CHANNEL", "50")) # 공식/보조 채널당 최근 수집 개수
BACKFILL_PER_QUERY = int(os.getenv("BACKFILL_PER_QUERY", "30")) # 일반 검색 쿼리별 수집 개수

# ==== 기본 공식 채널 (네가 준 값) ====
DEFAULT_OFFICIAL = {
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

# 약어/별칭 → 정식명
ALIASES = {
    "LG": "LG 트윈스", "KT": "KT 위즈", "두산": "두산 베어스", "SSG": "SSG 랜더스",
    "키움": "키움 히어로즈", "KIA": "KIA 타이거즈", "삼성": "삼성 라이온즈",
    "NC": "NC 다이노스", "롯데": "롯데 자이언츠", "한화": "한화 이글스",
}

def _yt() -> Optional[Any]:
    key = os.getenv("YT_API_KEY") or os.getenv("YOUTUBE_API_KEY")
    return build("youtube", "v3", developerKey=key, cache_discovery=False) if key else None

def _load_official_map() -> Dict[str, List[str]]:
    # OFFICIAL_CHANNELS_JSON 있으면 그걸 우선 사용(override)
    raw = os.getenv("OFFICIAL_CHANNELS_JSON")
    if raw:
        try:
            tmp = json.loads(raw)
            out: Dict[str, List[str]] = {}
            for k, v in tmp.items():
                out[k] = v if isinstance(v, list) else [v]
            return out
        except Exception:
            pass
    # 기본값(정식명/약어 모두 키로 지원)
    out: Dict[str, List[str]] = {}
    for full, cid in DEFAULT_OFFICIAL.items():
        out.setdefault(full, []).append(cid)
    for alias, full in ALIASES.items():
        if full in DEFAULT_OFFICIAL:
            out.setdefault(alias, []).append(DEFAULT_OFFICIAL[full])
    return out

OFFICIAL_MAP = _load_official_map()

def _resolve_team(team: str) -> str:
    team = (team or "").strip()
    if not team: return ""
    if team in DEFAULT_OFFICIAL or team in OFFICIAL_MAP: return team
    if team in ALIASES: return ALIASES[team]
    return team

# 문자열 정규화 + 팀명 포함 여부
def _norm(s: str) -> str: return re.sub(r"\s+", "", (s or "").lower())
def _title_has_team(title: str, team: str) -> bool:
    if not title or not team: return False
    t = _norm(title)
    keys = {_norm(team)}
    for k, full in ALIASES.items():
        if full == team: keys.add(_norm(k))
    return any(k in t for k in keys)

# ISO8601 duration → 초
_dur_re = re.compile(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?")
def _sec(iso: str) -> int:
    if not iso: return 0
    m = _dur_re.fullmatch(iso)
    if not m: return 0
    d, h, m_, s = (int(x or 0) for x in m.groups())
    return d*86400 + h*3600 + m_*60 + s

def _videos_by_ids(yt, ids: List[str]) -> List[Dict]:
    out: List[Dict[str, Any]] = []
    if not ids: return out
    for i in range(0, len(ids), 50):
        resp = yt.videos().list(part="snippet,contentDetails", id=",".join(ids[i:i+50]), maxResults=50).execute()
        for it in resp.get("items", []):
            sn, cd = it.get("snippet", {}), it.get("contentDetails", {})
            sec = _sec(cd.get("duration"))
            thumbs = sn.get("thumbnails") or {}
            thumb = (thumbs.get("high") or {}).get("url") or (thumbs.get("medium") or {}).get("url") or (thumbs.get("default") or {}).get("url")
            out.append({
                "id": it.get("id"),
                "title": sn.get("title"),
                "url": f"https://www.youtube.com/watch?v={it.get('id')}",
                "thumbnail": thumb,
                "channelTitle": sn.get("channelTitle"),
                "channel_id": sn.get("channelId"),
                "publish_date": sn.get("publishedAt"),
                "duration": cd.get("duration"),
                "seconds": sec,
            })
    return out

def _recent_from_channel(yt, channel_id: str, limit: int, team_filter: str = "") -> List[Dict]:
    ids, token = [], None
    while len(ids) < limit:
        n = min(50, limit - len(ids))
        resp = yt.search().list(part="id", channelId=channel_id, type="video", order="date", maxResults=n, pageToken=token).execute()
        ids += [it["id"]["videoId"] for it in resp.get("items", []) if it.get("id", {}).get("videoId")]
        token = resp.get("nextPageToken")
        if not token: break
    vids = _videos_by_ids(yt, ids)
    return [v for v in vids if not team_filter or _title_has_team(v.get("title"), team_filter)]

def _search_multi(yt, queries: List[str], per_query: int, team_filter: str) -> List[Dict]:
    all_ids: List[str] = []
    for q in queries:
        token = None
        grabbed = 0
        while grabbed < per_query:
            n = min(50, per_query - grabbed)
            resp = yt.search().list(
                part="id",
                q=q, type="video", order="date",
                relevanceLanguage="ko", regionCode="KR",
                maxResults=n, pageToken=token, safeSearch="none"
            ).execute()
            ids = [it["id"]["videoId"] for it in resp.get("items", []) if it.get("id", {}).get("videoId")]
            if not ids: break
            all_ids += ids
            grabbed += len(ids)
            token = resp.get("nextPageToken")
            if not token: break
    vids = _videos_by_ids(yt, all_ids)
    return [v for v in vids if _title_has_team(v.get("title"), team_filter)]

def _dedup(items: List[Dict]) -> List[Dict]:
    seen, out = set(), []
    for v in items:
        vid = v.get("id") or v.get("videoId") or v.get("url")
        if not vid or vid in seen: continue
        seen.add(vid); out.append(v)
    return out

def search_videos_by_team(team_name: str, max_results: int = 60) -> Tuple[List[Dict], List[Dict]]:
    """
    리턴: (shorts, longs)
    1) 팀 '공식 채널' 최신 업로드 중심
    2) KBO 등 보조 채널(환경변수 OFFICIAL_CHANNELS_JSON에 "KBO" 등 등록 시)에서 '팀명 포함' 추가
    3) 일반 검색('팀명 하이라이트/KBO/경기 하이라이트')로 보강(제목에 팀명 필수)
    """
    team_name = (team_name or "").strip()
    if not team_name: return [], []
    yt = _yt()
    if yt is None:    return [], []

    resolved = _resolve_team(team_name)

    # 1) 공식 채널
    official_cids = OFFICIAL_MAP.get(resolved, []) + OFFICIAL_MAP.get(ALIASES.get(resolved, ""), [])
    official_cids = [c for c in official_cids if c]
    official: List[Dict] = []
    for cid in official_cids:
        try:
            # 공식 채널은 필터 없이
            official += _recent_from_channel(yt, cid, limit=RECENT_PER_CHANNEL, team_filter="")
        except Exception:
            pass

    # 2) 보조 채널 (예: "KBO")
    extras: List[Dict] = []
    for k in ("KBO", "KBO 리그", "KBO League"):
        for cid in OFFICIAL_MAP.get(k, []):
            try:
                extras += _recent_from_channel(yt, cid, limit=RECENT_PER_CHANNEL//2, team_filter=resolved)
            except Exception:
                pass

    # 3) 일반 검색
    queries = [
        f"{resolved} 하이라이트",
        f"{resolved} KBO",
        f"{resolved} 경기 하이라이트",
    ]
    backfill = []
    try:
        backfill = _search_multi(yt, queries, BACKFILL_PER_QUERY, team_filter=resolved)
    except HttpError:
        backfill = []
    except Exception:
        backfill = []

    merged = _dedup(official + extras + backfill)
    merged.sort(key=lambda v: (v.get("publish_date") or ""), reverse=True)

    shorts = [v for v in merged if (v.get("seconds") or 0) <= SHORT_MAX_SEC]
    longs  = [v for v in merged if (v.get("seconds") or 0) >  SHORT_MAX_SEC]

    if max_results and max_results > 0:
        shorts = shorts[:max_results]
        longs  = longs[:max_results]
    return shorts, longs

# 구버전 호환 (롱폼만)
def get_youtube_videos(team_name: str, max_results: int = 60) -> List[Dict]:
    _, longs = search_videos_by_team(team_name, max_results=max_results)
    return longs

