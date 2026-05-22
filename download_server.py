#!/usr/bin/env python3
# Hookpilot 로컬 백엔드 — 정적 파일 서빙 + yt-dlp 다운로드 API
# Downie 4 처럼 yt-dlp 래퍼로 YouTube·TikTok·Instagram 원클릭 다운로드
# 사용: python3 download_server.py (포트 8889)

import http.server
import socketserver
import urllib.parse
import urllib.request
import subprocess
import os
import sys
import re
import tempfile
import threading
import json
import glob
import time
import uuid

PORT = int(os.environ.get("PORT", "9000"))  # Cloud (Render·Fly.io) 가 $PORT 박음. 로컬은 9000.
ROOT = os.path.dirname(os.path.abspath(__file__))
YTDLP = "/opt/homebrew/bin/yt-dlp"
DOWNLOAD_TIMEOUT_SEC = 300  # 5분
DL_DIR = "/tmp/hookpilot_downloads"
os.makedirs(DL_DIR, exist_ok=True)
FILE_TTL_SEC = 3600  # 1시간 후 자동 정리

# 메모리 캐시 — 메타·자막 결과 5분 보관 (반복 클릭 즉시 응답)
META_MEM_CACHE = {}  # url -> (timestamp, data)
META_MEM_TTL = 300  # 5분


def _mem_cache_get(key):
    entry = META_MEM_CACHE.get(key)
    if not entry: return None
    ts, data = entry
    if time.time() - ts > META_MEM_TTL:
        META_MEM_CACHE.pop(key, None)
        return None
    return data


def _mem_cache_set(key, data):
    META_MEM_CACHE[key] = (time.time(), data)
    # 최대 500개 (LRU 흉내 — 가장 오래된 것 제거)
    if len(META_MEM_CACHE) > 500:
        oldest = min(META_MEM_CACHE.items(), key=lambda x: x[1][0])
        META_MEM_CACHE.pop(oldest[0], None)


def fetch_youtube_oembed(url):
    """YouTube oEmbed API — 봇 차단 시 fallback (title/channel/thumbnail만)"""
    try:
        req = urllib.request.Request(
            f"https://www.youtube.com/oembed?url={urllib.parse.quote(url)}&format=json",
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=4) as r:
            d = json.loads(r.read())
        return {
            "channel": d.get("author_name"),
            "caption": d.get("title"),
            "source": "YouTube oEmbed (제한적)",
        }
    except Exception:
        return None


# Invidious public instances — yt-dlp 봇 차단 시 fallback
# 동시에 3개 호출, 첫 응답 채택. 죽은 instance는 자동 패스.
# 2026-05 기준: Piped 생태계 거의 다 다운. Invidious도 대부분 죽음. f5.si 살아있음.
INVIDIOUS_INSTANCES = [
    "https://invidious.f5.si",
    "https://inv.in.projectsegfau.lt",
    "https://invidious.protokolla.fi",
]


def _extract_youtube_id(url):
    """YouTube URL에서 11자 video ID 추출"""
    m = re.search(r"(?:shorts/|watch\?v=|youtu\.be/|embed/)([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else None


def fetch_youtube_html(url):
    """YouTube watch 페이지 HTML 직접 fetch — ytInitialData에서 views·likes·channel·title·uploadDate 추출
    yt-dlp 봇 차단 우회 — 직접 HTML 호출은 일반 사용자 IP에서 통과됨"""
    vid = _extract_youtube_id(url)
    if not vid: return None
    watch_url = f"https://www.youtube.com/watch?v={vid}"
    try:
        req = urllib.request.Request(watch_url, headers={
            "User-Agent": ua(),  # 매 호출 랜덤 UA
            "Accept-Language": "ko,en;q=0.9",
        })
        with urllib.request.urlopen(req, timeout=3) as r:
            html = r.read().decode("utf-8", errors="ignore")
        if "Sign in to confirm" in html or "not a bot" in html: return None
    except Exception:
        return None
    m = re.search(r'var ytInitialData\s*=\s*(\{.+?\})\s*;\s*</script>', html, re.DOTALL)
    if not m: return None
    try:
        d = json.loads(m.group(1))
    except Exception:
        return None

    def find_key(obj, key, depth=0):
        if depth > 20: return
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == key: yield v
                yield from find_key(v, key, depth + 1)
        elif isinstance(obj, list):
            for it in obj: yield from find_key(it, key, depth + 1)

    out = {}
    # 조회수 — "조회수 1,674회" → 1674
    for vc in find_key(d, 'viewCount'):
        if isinstance(vc, dict):
            st = vc.get('viewCount', {}).get('simpleText') if 'videoViewCountRenderer' not in vc else \
                 vc.get('videoViewCountRenderer', {}).get('viewCount', {}).get('simpleText')
            if not st and 'simpleText' in vc: st = vc['simpleText']
            if st:
                num = re.sub(r'[^\d]', '', st)
                if num: out["views"] = int(num); break
    # 좋아요 — toggleButtonViewModel.defaultButtonViewModel.buttonViewModel.title (숫자 또는 '좋아요')
    for tb in find_key(d, 'toggleButtonViewModel'):
        try:
            title = tb.get('toggleButtonViewModel', {}).get('defaultButtonViewModel', {}) \
                      .get('buttonViewModel', {}).get('title', '')
            icon = tb.get('toggleButtonViewModel', {}).get('defaultButtonViewModel', {}) \
                     .get('buttonViewModel', {}).get('iconName', '')
            if icon in ('LIKE', 'SHORTS_LIKE') and title:
                num = title.replace(',', '').replace('만', '0000').replace('천', '000').replace('K', '000').replace('M', '000000')
                num = re.sub(r'[^\d]', '', num)
                if num: out["likes"] = int(num); break
        except Exception: continue
    # 채널 — videoOwnerRenderer.title.runs[0].text
    for vor in find_key(d, 'videoOwnerRenderer'):
        try:
            ch = vor.get('title', {}).get('runs', [{}])[0].get('text')
            if ch: out["channel"] = ch; break
        except Exception: continue
    # 게시일 — dateText.simpleText "2026. 4. 26."
    for dt in find_key(d, 'dateText'):
        try:
            txt = dt.get('simpleText') if isinstance(dt, dict) else None
            if txt:
                dm = re.search(r'(\d{4})\.?\s*(\d{1,2})\.?\s*(\d{1,2})', txt)
                if dm:
                    out["uploadDate"] = f"{dm.group(1)}-{int(dm.group(2)):02d}-{int(dm.group(3)):02d}"
                    break
        except Exception: continue
    # 제목 — title runs
    for tl in find_key(d, 'title'):
        try:
            if isinstance(tl, dict) and 'runs' in tl:
                parts = [r.get('text', '') for r in tl['runs']]
                joined = ''.join(parts).strip()
                if joined and len(joined) > 3:
                    out["caption"] = joined[:500]; break
        except Exception: continue
    if out:
        out["source"] = "youtube.com HTML"
        return out
    return None


def fetch_youtube_rydapi(url):
    """ReturnYouTubeDislike API — views·likes·dislikes (캐시되어 있어 약간 옛 값일 수 있지만 봇 차단 없음)"""
    vid = _extract_youtube_id(url)
    if not vid: return None
    try:
        req = urllib.request.Request(
            f"https://returnyoutubedislikeapi.com/votes?videoId={vid}",
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=2) as r:
            d = json.loads(r.read())
        if d.get("deleted"): return None
        out = {}
        if d.get("viewCount") is not None and d["viewCount"] > 0:
            out["views"] = int(d["viewCount"])
        if d.get("likes") is not None and d["likes"] > 0:
            out["likes"] = int(d["likes"])
        if d.get("dislikes") is not None:
            out["dislikes"] = int(d["dislikes"])
        return out if out else None
    except Exception:
        return None


def fetch_youtube_invidious(url):
    """Invidious public instance fallback — 3개 instance 동시 호출, 첫 유효 응답 채택
    반환: 메타 dict 또는 None (모든 instance 차단·다운)"""
    import concurrent.futures, datetime
    vid = _extract_youtube_id(url)
    if not vid: return None
    fields = "viewCount,likeCount,lengthSeconds,author,title,published,description"
    api_path = f"/api/v1/videos/{vid}?fields={fields}"

    def try_inst(inst):
        try:
            req = urllib.request.Request(
                inst + api_path,
                headers={"User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req, timeout=2.5) as r:
                raw = r.read()
            try:
                d = json.loads(raw)
            except Exception:
                return None
            if not isinstance(d, dict) or d.get("error"): return None
            out = {}
            if d.get("viewCount") is not None: out["views"] = int(d["viewCount"])
            if d.get("likeCount") is not None and d["likeCount"] > 0:
                out["likes"] = int(d["likeCount"])
            if d.get("lengthSeconds"): out["duration"] = int(d["lengthSeconds"])
            if d.get("published"):
                try:
                    out["uploadDate"] = datetime.datetime.fromtimestamp(
                        int(d["published"])
                    ).strftime("%Y-%m-%d")
                except Exception: pass
            if d.get("author"): out["channel"] = d["author"]
            if d.get("title"): out["caption"] = d["title"]
            if d.get("description"):
                out["description"] = d["description"][:2000]
            if out.get("views") is not None or out.get("duration") is not None:
                out["source"] = f"Invidious ({inst.split('//')[1]})"
                return out
        except Exception:
            return None
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        futures = [ex.submit(try_inst, inst) for inst in INVIDIOUS_INSTANCES]
        try:
            for fut in concurrent.futures.as_completed(futures, timeout=3.2):
                try:
                    result = fut.result()
                    if result:
                        for f in futures:
                            if f is not fut: f.cancel()
                        return result
                except Exception:
                    continue
        except concurrent.futures.TimeoutError:
            pass
    return None


MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 "
    "Mobile/15E148 Safari/604.1"
)

# UA pool — 봇 차단 회피 (같은 UA로 1000개 요청 = 차단). 매 요청마다 랜덤 선택
import random as _random
USER_AGENTS = [
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; SM-S918N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.2) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (Linux; Android 13; SAMSUNG SM-A536U) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/23.0 Chrome/115.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/121.0.6167.66 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
]

def ua():
    """랜덤 User-Agent — 매 요청 다른 UA로 봇 패턴 회피"""
    return _random.choice(USER_AGENTS)

ALLOWED_HOSTS = re.compile(
    r"^https?://(www\.)?"
    r"(youtube\.com|youtu\.be|tiktok\.com|vm\.tiktok\.com|instagram\.com)/",
    re.IGNORECASE,
)
INSTAGRAM_RE = re.compile(
    r"^https?://(www\.)?instagram\.com/(reel|p|tv)/([A-Za-z0-9_-]+)",
    re.IGNORECASE,
)


def _ig_embed_extract_field(html, start_marker):
    """state machine으로 escape된 JSON 값 추출"""
    pos = html.find(start_marker)
    if pos < 0: return None
    pos += len(start_marker)
    i = pos; end = pos
    while i < len(html) - 1:
        c = html[i]
        if c == "\\":
            nxt = html[i + 1]
            if nxt == '"': end = i; break
            i += 2; continue
        if c == '"': end = i; break
        i += 1
    raw = html[pos:end]
    try:
        v = json.loads('"' + raw + '"')
        if "\\/" in v or "\\\\" in v or "\\u" in v:
            v = json.loads('"' + v + '"')
        return v
    except Exception:
        return None


def extract_instagram_video_url(url):
    """Instagram embed/captioned 페이지에서 로그인 없이 video_url 추출.
    실패 시 yt-dlp fallback (사장님 IG 재생 안 됨 진짜 fix)"""
    info = extract_instagram_meta(url)
    vu = info.get("video_url") if info else None
    if vu: return vu
    # yt-dlp fallback — IG embed extractor 실패 시 (4/5 실패 케이스 대응)
    try:
        proc = subprocess.run(
            [YTDLP, "--get-url", "-f", "best[ext=mp4]/best", "--no-warnings", "--quiet", url],
            capture_output=True, timeout=20
        )
        if proc.returncode == 0:
            line = proc.stdout.decode("utf-8", errors="ignore").strip().split("\n")[0].strip()
            if line.startswith("http"): return line
    except Exception:
        pass
    return None


def extract_instagram_meta(url):
    """Instagram embed/captioned 페이지에서 메타데이터 통합 추출
    Returns {views, caption, video_url, taken_at_timestamp} or None"""
    m = INSTAGRAM_RE.match(url)
    if not m: return None
    shortcode = m.group(3)
    embed_url = f"https://www.instagram.com/reel/{shortcode}/embed/captioned/"
    req = urllib.request.Request(embed_url, headers={
        "User-Agent": ua(),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    })
    try:
        # 3 → 10초 (IG embed 페이지가 3초 안에 응답 못 하는 경우 자주)
        html = urllib.request.urlopen(req, timeout=10).read().decode("utf-8", errors="ignore")
    except Exception:
        return None
    out = {}
    # video_url
    vu = _ig_embed_extract_field(html, 'video_url\\":\\"')
    if vu: out["video_url"] = vu
    # video_view_count
    mv = re.search(r'video_view_count\\":\s*(\d+)', html)
    if mv: out["views"] = int(mv.group(1))
    # 좋아요 — edge_liked_by 또는 edge_media_preview_like
    ml = re.search(r'(?:edge_liked_by|edge_media_preview_like)\\":\s*\{\\"count\\":\s*(\d+)', html)
    if ml: out["likes"] = int(ml.group(1))
    # 댓글 — edge_media_to_comment 또는 edge_media_to_parent_comment
    mc = re.search(r'edge_media_to_(?:parent_comment|comment)\\":\s*\{\\"count\\":\s*(\d+)', html)
    if mc: out["comments"] = int(mc.group(1))
    # caption
    cap = _ig_embed_extract_field(html, 'edge_media_to_caption\\":{\\"edges\\":[{\\"node\\":{\\"text\\":\\"')
    if cap: out["caption"] = cap[:2000]
    # 채널 (owner.username)
    own = _ig_embed_extract_field(html, '"owner\\":{\\"id\\":\\"')  # try alt
    if not own:
        own_m = re.search(r'owner\\":\s*{[^}]*\\"username\\":\s*\\"([^\\"]+)\\"', html)
        if own_m: out["channel"] = own_m.group(1)
    # taken_at_timestamp
    mt = re.search(r'taken_at_timestamp\\":\s*(\d+)', html)
    if mt:
        try:
            import datetime
            out["uploadDate"] = datetime.datetime.fromtimestamp(int(mt.group(1))).strftime("%Y-%m-%d")
        except Exception: pass
    return out if out else None


def _probe_mp4_duration(video_url):
    """ffprobe로 mp4 URL의 duration 추출 (네트워크 stream 직접 분석, 다운로드 불필요)
    최대 5초 timeout"""
    try:
        proc = subprocess.run([
            "/opt/homebrew/bin/ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            "-user_agent", ua(),
            "-referer", "https://www.instagram.com/",
            "-rw_timeout", "2000000",
            video_url,
        ], capture_output=True, timeout=2)
        if proc.returncode != 0: return None
        s = proc.stdout.decode().strip()
        if not s: return None
        return int(float(s))
    except Exception:
        return None


def ytdlp_print_meta(url):
    """yt-dlp --skip-download --print으로 풍부한 메타데이터 추출
    봇 차단 해제 시 (/tmp/hookpilot_yt_unblocked.flag 존재) PoToken + cookies 자동 활용"""
    try:
        cmd = [
            YTDLP, "--skip-download", "--no-warnings", "--quiet", "--no-playlist",
            "--socket-timeout", "1",
            "--user-agent", ua(),
        ]
        # 봇 차단 풀린 경우 자동으로 PoToken + cookies 사용 (yt_unblock_monitor가 flag 만들 때)
        if os.path.exists("/tmp/hookpilot_yt_unblocked.flag"):
            cmd += ["--cookies", "/tmp/yt_headless_cookies.txt",
                    "--extractor-args", "youtube:player_client=mweb",
                    "--extractor-args", "youtubepot-bgutilhttp:base_url=http://127.0.0.1:4416"]
        else:
            cmd += ["--extractor-args", "youtube:player_client=web"]
        cmd += [
            "--print",
            "%(view_count|0)s|%(duration|0)s|%(upload_date)s|%(like_count|0)s|%(comment_count|0)s|"
            "%(uploader|NA)s|%(channel|NA)s|%(channel_follower_count|0)s|%(categories|NA)j|"
            "%(tags|NA)j|%(description|NA)s|%(title)s",
            url,
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=2)
        if proc.returncode != 0: return None
        line = proc.stdout.decode("utf-8", errors="ignore").strip().split("\n")[0]
        parts = line.split("|", 11)
        if len(parts) < 12: return None
        v, d, ud, lc, cc, up, ch, fc, cats, tags, desc, title = parts
        out = {}
        try: out["views"] = int(v) if v and v.isdigit() and v != "0" else None
        except Exception: out["views"] = None
        try: out["duration"] = int(float(d)) if d and d not in ("0", "NA") else None
        except Exception: out["duration"] = None
        try: out["likes"] = int(lc) if lc and lc.isdigit() and lc != "0" else None
        except Exception: out["likes"] = None
        try: out["comments"] = int(cc) if cc and cc.isdigit() and cc != "0" else None
        except Exception: out["comments"] = None
        try: out["channelFollowers"] = int(fc) if fc and fc.isdigit() and fc != "0" else None
        except Exception: out["channelFollowers"] = None
        if ud and len(ud) == 8 and ud.isdigit():
            out["uploadDate"] = f"{ud[:4]}-{ud[4:6]}-{ud[6:8]}"
        if ch and ch != "NA": out["channel"] = ch
        elif up and up != "NA": out["channel"] = up
        # 카테고리·태그 (JSON 형식)
        try:
            if cats and cats not in ("NA", "null"):
                cs = json.loads(cats)
                if isinstance(cs, list) and cs: out["categories"] = cs[:3]
        except Exception: pass
        try:
            if tags and tags not in ("NA", "null"):
                ts = json.loads(tags)
                if isinstance(ts, list) and ts: out["tags"] = ts[:10]
        except Exception: pass
        if desc and desc != "NA": out["description"] = desc[:1500]
        if title and title != "NA": out["caption"] = title[:2000]
        return out
    except Exception: return None


def stream_url_to_client(handler, video_url, fname):
    """원격 mp4 URL에서 받아 클라이언트로 스트림"""
    req = urllib.request.Request(
        video_url,
        headers={
            "User-Agent": ua(),
            "Referer": "https://www.instagram.com/",
        },
    )
    upstream = urllib.request.urlopen(req, timeout=30)
    # Content-Length 안전 처리 — upstream이 중복/콤마 값 보낼 수 있어 정수 검증
    raw_size = upstream.headers.get("Content-Length")
    size_int = None
    if raw_size:
        try:
            # 콤마 분리된 경우 첫 값만
            size_int = int(str(raw_size).split(",")[0].strip())
        except (TypeError, ValueError):
            size_int = None
    handler.send_response(200)
    handler.send_header("Content-Type", "video/mp4")
    if size_int and size_int > 0:
        handler.send_header("Content-Length", str(size_int))
    handler.send_header(
        "Content-Disposition", f'attachment; filename="{fname}"'
    )
    handler.end_headers()
    while True:
        chunk = upstream.read(64 * 1024)
        if not chunk:
            break
        handler.wfile.write(chunk)


def safe_filename(s, fallback="video"):
    if not s:
        return fallback
    # 한글·영문·숫자만 허용
    return re.sub(r"[^\w\-가-힣]", "_", s)[:50] or fallback


# === 무료 보안 5종: rate limit · IP 차단 · 보안 헤더 · 다운로드 cap · 로그 ===
import threading
_RL_LOCK = threading.Lock()
_RL_BUCKET = {}    # ip → list[timestamps] (1분 슬라이딩)
_RL_BLOCK = {}     # ip → block_until_ts (24시간 차단)
_RL_DL_COUNT = {}  # ip → list[ts] (일 다운로드 cap용)

RL_PER_MIN = 60       # 분당 60건 초과 시 의심 → 5분 cool-down
RL_BURST_BLOCK = 120  # 분당 120건 초과 시 24시간 차단
RL_DL_PER_DAY = 5     # IP당 일 5건 다운로드 cap (외부 abuse + 사장님 Mac IP SNS 위험 최소화)
RL_DL_PER_MIN = 1     # IP당 분당 1건 다운로드 (yt-dlp 무한 호출 차단)


def _ip_rate_limit(ip, is_download=False):
    """초과 시 (False, 사유) · 통과 시 (True, None).
    localhost(127.0.0.1·::1) 영구 면제 — 사장님 같은 머신 사용 + 자동 검증 도구 차단 방지."""
    # localhost·local IP 면제
    if ip in ("127.0.0.1", "::1", "localhost") or ip.startswith("192.168.") or ip.startswith("10."):
        return True, None
    import time as _t
    now = _t.time()
    with _RL_LOCK:
        # 1. 차단 IP 검사
        until = _RL_BLOCK.get(ip)
        if until and until > now:
            return False, f"24h-blocked (남은 {int((until-now)/3600)}h)"
        if until:
            _RL_BLOCK.pop(ip, None)
        # 2. 분당 슬라이딩 검사
        bucket = _RL_BUCKET.setdefault(ip, [])
        bucket[:] = [t for t in bucket if now - t < 60]
        bucket.append(now)
        if len(bucket) > RL_BURST_BLOCK:
            _RL_BLOCK[ip] = now + 24 * 3600
            return False, f"burst-blocked ({len(bucket)}/min)"
        if len(bucket) > RL_PER_MIN:
            return False, f"rate-limited ({len(bucket)}/min)"
        # 3. 일 다운로드 cap + 분당 다운로드 cap (외부 abuse 방어)
        if is_download:
            dl = _RL_DL_COUNT.setdefault(ip, [])
            dl[:] = [t for t in dl if now - t < 86400]
            dl.append(now)
            if len(dl) > RL_DL_PER_DAY:
                return False, f"daily-cap ({len(dl)}/{RL_DL_PER_DAY})"
            # 분당 다운로드 — yt-dlp 무한 호출 차단 (외부 봇 방어)
            dl_min = [t for t in dl if now - t < 60]
            if len(dl_min) > RL_DL_PER_MIN:
                return False, f"download-burst ({len(dl_min)}/{RL_DL_PER_MIN}/min)"
    return True, None


class HookpilotHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def end_headers(self):
        # CORS 허용 (simple.html이 fetch로 호출)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        # /api/thumb 같은 이미지·정적 자원은 브라우저 캐시 허용 — 사장님 카드 스크롤 시 빠르게 표시
        # 자주 변하는 자원 (trending_videos.js·live.html 등)만 no-store로 (do_GET wrap 안에서 별도 적용)
        try:
            path = getattr(self, 'path', '') or ''
            if not path.startswith('/api/thumb'):
                self.send_header("Cache-Control", "no-store")
            # /api/thumb은 강제 캐시 안 박음 — handle_thumb 자체가 public max-age=86400 설정
        except Exception:
            self.send_header("Cache-Control", "no-store")
        # === 무료 보안 헤더 (브라우저 측 공격 방어) ===
        # XSS·clickjacking·MIME sniffing 차단
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        # HSTS — HTTPS 강제 (외부 노출 시 효과). localhost는 무시됨.
        self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        # CSP — script·style·img·media 제어. inline은 허용 (현재 코드 호환), CDN 허용.
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https:; "
            "media-src 'self' blob: https:; "
            # connect-src — simple.html 이 클라이언트 사이드에서 직접 fetch 하는 외부 fallback API 모두 허용:
            # · TikTok·IG CDN, · Piped (YouTube 메타 8개 fallback), · Invidious (YT 메타 4개),
            # · noembed (oEmbed), · tikwm (TT 메타).
            # 이게 빠지면 시각적 무한 "분석 중" + 조회수 "?" → 사장님이 "다 안 됨" 으로 보심 (며칠 동안 잡힌 진짜 회귀).
            "connect-src 'self' "
            "https://*.tikwm.com https://*.tiktok.com https://*.instagram.com "
            "https://*.fbcdn.net https://*.cdninstagram.com https://*.ytimg.com "
            "https://pipedapi.kavin.rocks https://pipedapi.tokhmi.xyz https://pipedapi.adminforge.de "
            "https://pipedapi.r4fo.com https://api-piped.mha.fi https://pipedapi.privacydev.net "
            "https://piped-api.lunar.icu https://pipedapi.us.projectsegfau.lt "
            "https://yewtu.be https://invidious.fdn.fr https://invidious.materialio.us https://inv.nadeko.net "
            "https://noembed.com; "
            "frame-src https://www.tiktok.com https://www.instagram.com https://www.youtube.com https://www.youtube-nocookie.com; "
            "object-src 'none'; "
            "base-uri 'self';"
        )
        super().end_headers()



    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def do_GET(self):
        # 브라우저가 응답 중간에 끊는 케이스 (탭 닫기·새로고침·모달 빠른 닫기) — BrokenPipeError·ConnectionResetError
        # 정상 시나리오. stderr traceback 도배 방지하려면 wrap.
        try:
            self._do_GET_inner()
        except (BrokenPipeError, ConnectionResetError):
            pass  # 클라이언트가 응답 받기 전 끊음 — 정상

    def _do_GET_inner(self):
        parsed = urllib.parse.urlparse(self.path)
        # === 외부 사용자 rate limit 매우 strict (사장님 Mac yt-dlp 부하·SNS 위험 최소화) ===
        # 다운로드·메타·thumb 모두 사장님 Mac yt-dlp 호출 = 사장님 Mac IP 사용.
        # 외부 사용자 = 분당 1건, 일 5건 cap. 사장님 본인 (localhost) = 무한.
        if parsed.path.startswith("/api/"):
            try:
                cf_ip = self.headers.get("Cf-Connecting-Ip")
                xff = self.headers.get("X-Forwarded-For", "").split(",")[0].strip()
                client_ip = cf_ip or xff or self.client_address[0]
                is_download = parsed.path in ("/api/prepare", "/api/download")
                ok, reason = _ip_rate_limit(client_ip, is_download=is_download)
                if not ok:
                    sys.stderr.write(f"[security] {client_ip} {reason} {parsed.path}\n")
                    self.send_response(429)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Retry-After", "60")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": reason}).encode())
                    return
            except Exception as e:
                sys.stderr.write(f"[security] rate-limit error: {e}\n")
        if parsed.path == "/api/download":
            self.handle_download(parsed)
        elif parsed.path == "/api/prepare":
            self.handle_prepare(parsed)
        elif parsed.path.startswith("/api/file/"):
            self.handle_file(parsed)
        elif parsed.path == "/api/probe":
            self.handle_probe(parsed)
        elif parsed.path == "/api/transcript":
            self.handle_transcript(parsed)
        elif parsed.path == "/api/meta":
            self.handle_meta(parsed)
        elif parsed.path == "/api/ig-stream":
            self.handle_ig_stream(parsed)
        elif parsed.path == "/api/refresh-trending":
            self.handle_refresh_trending(parsed)
        elif parsed.path == "/api/thumb":
            self.handle_thumb(parsed)
        else:
            # === gzip 압축 — 큰 정적 자산 (1MB+ trending_videos.js) 5배 작게 ===
            # Accept-Encoding 가 gzip 지원하면 압축해서 전송. 사이트 첫 로드 속도 critical.
            accept_enc = (self.headers.get("Accept-Encoding") or "")
            compressible = (".js", ".html", ".json", ".css")
            if "gzip" in accept_enc and parsed.path.endswith(compressible):
                try:
                    file_path = os.path.normpath(os.path.join(ROOT, parsed.path.lstrip("/")))
                    if os.path.isfile(file_path) and file_path.startswith(ROOT):
                        import gzip as _gz, mimetypes
                        with open(file_path, "rb") as _f: raw = _f.read()
                        compressed = _gz.compress(raw, compresslevel=6)
                        ctype = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
                        self.send_response(200)
                        self.send_header("Content-Type", ctype)
                        self.send_header("Content-Encoding", "gzip")
                        self.send_header("Content-Length", str(len(compressed)))
                        self.send_header("Vary", "Accept-Encoding")
                        # trending_videos.js 같은 자주 변하는 자산은 no-store
                        if parsed.path.rstrip("/").endswith(("trending_videos.js", "data.js", "simple.html", "live.html", "hookpilot.html")):
                            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate, max-age=0")
                        self.end_headers()
                        self.wfile.write(compressed)
                        return
                except Exception as _gz_e:
                    pass  # gzip 실패 시 일반 응답으로 fallback
            # 자주 갱신되는 자산: 캐시 100% 무력화 + If-Modified-Since 무시 + ETag 비움.
            # 사용자 브라우저가 304 못 받게 → 매번 200 + 새 본문.
            no_cache_paths = ("trending_videos.js", "data.js", "simple.html", "live.html", "hookpilot.html")
            if any(parsed.path.rstrip("/").endswith(p) or parsed.path == "/" for p in no_cache_paths):
                # If-Modified-Since/If-None-Match 무력화 — SimpleHTTPRequestHandler가 304 안 보내게.
                if "If-Modified-Since" in self.headers:
                    del self.headers["If-Modified-Since"]
                if "If-None-Match" in self.headers:
                    del self.headers["If-None-Match"]
                _orig_end = self.end_headers
                def _wrapped():
                    self.send_header("Cache-Control", "no-cache, no-store, must-revalidate, max-age=0")
                    self.send_header("Pragma", "no-cache")
                    self.send_header("Expires", "0")
                    # ETag·Last-Modified는 SimpleHTTPRequestHandler가 박지만 우리는 매번 다른 값으로
                    import time as _t
                    self.send_header("X-Served-At", str(int(_t.time() * 1000)))
                    _orig_end()
                self.end_headers = _wrapped
            super().do_GET()

    # TikTok thumbnail 메모리 캐시 — oEmbed 호출 비용 절약 (5분 TTL)
    _TT_THUMB_CACHE = {}

    def handle_thumb(self, parsed):
        """썸네일 프록시 — TikWM/Instagram/TikTok CDN hot-link 차단 우회.
        쿼리:
          ?url=<직접 url> → 그 url을 fetch (TikWM, IG 등)
          ?tt=<tiktokId>&user=<tiktokUser> → TikTok oEmbed로 thumbnail_url 가져와 proxy
        """
        import time as _t
        qs = urllib.parse.parse_qs(parsed.query)
        tt_id = (qs.get("tt") or [""])[0]
        tt_user = (qs.get("user") or [""])[0]
        url = (qs.get("url") or [""])[0]

        # TikTok 모드: oEmbed로 thumbnail_url 동적 추출
        if tt_id and tt_user:
            cache_key = f"{tt_user}:{tt_id}"
            cached = self._TT_THUMB_CACHE.get(cache_key)
            if cached and _t.time() - cached[0] < 300:
                url = cached[1]
            else:
                try:
                    oembed_url = f"https://www.tiktok.com/oembed?url=https://www.tiktok.com/@{tt_user}/video/{tt_id}"
                    req = urllib.request.Request(oembed_url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=6) as r:
                        d = json.loads(r.read())
                    url = d.get("thumbnail_url", "")
                    if url:
                        self._TT_THUMB_CACHE[cache_key] = (_t.time(), url)
                        if len(self._TT_THUMB_CACHE) > 1000:
                            oldest = min(self._TT_THUMB_CACHE.items(), key=lambda x: x[1][0])
                            self._TT_THUMB_CACHE.pop(oldest[0], None)
                    else:
                        self._send_transparent_thumb("no thumbnail in oembed")
                        return
                except Exception as e:
                    self._send_transparent_thumb(f"oembed failed: {e}")
                    return

        if not url:
            self.send_error(400, "missing url or tt+user param")
            return

        # 도메인 화이트리스트 (open redirect / SSRF 방지)
        allowed = (
            "tikwm.com", "instagram.com", "cdninstagram.com", "ytimg.com",
            "tiktokcdn.com", "tiktokcdn-us.com", "tiktokcdn-eu.com", "byteoversea.com",
        )
        try:
            host = urllib.parse.urlparse(url).hostname or ""
            if not any(host.endswith(d) for d in allowed):
                self.send_error(400, f"domain {host} not allowed")
                return
            # 디스크 영구 캐시 — 두 번째 호출부터 네트워크 호출 없이 즉시 응답
            import hashlib
            cache_dir = os.path.join(ROOT, ".tmp", "thumb_cache")
            os.makedirs(cache_dir, exist_ok=True)
            cache_key = hashlib.sha1(url.encode()).hexdigest()[:16]
            cache_path = os.path.join(cache_dir, f"{cache_key}.bin")
            cache_meta = os.path.join(cache_dir, f"{cache_key}.ct")
            # 디스크 캐시 hit — 24h TTL
            if os.path.isfile(cache_path) and (time.time() - os.path.getmtime(cache_path)) < 86400:
                ctype = open(cache_meta).read().strip() if os.path.isfile(cache_meta) else "image/webp"
                data = open(cache_path, "rb").read()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("X-Cache", "DISK-HIT")
                self.end_headers()
                self.wfile.write(data)
                return
            # 디스크 캐시 miss — 네트워크 fetch
            ref = "https://www.tiktok.com/" if "tiktokcdn" in host else (
                "https://www.tikwm.com/" if "tikwm" in host else "https://www.instagram.com/")
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": ref,
                "Accept": "image/webp,image/png,image/jpeg,*/*",
            })
            with urllib.request.urlopen(req, timeout=8) as r:
                ctype = r.headers.get("Content-Type", "image/webp")
                data = r.read()
            # 사장님 사이트 속도 fix — 500KB 초과 큰 썸네일은 ffmpeg로 resize (5MB→100KB)
            # 카드 표시는 600px 폭 이하 충분. 5MB 이미지가 사장님 페이지 무겁게 만든 진짜 원인.
            if len(data) > 500 * 1024:
                try:
                    proc = subprocess.run([
                        "/opt/homebrew/bin/ffmpeg",
                        "-loglevel", "error",
                        "-i", "pipe:0",
                        "-vf", "scale='min(600,iw)':'-2'",
                        "-q:v", "5",
                        "-f", "image2pipe", "-vcodec", "mjpeg",
                        "pipe:1",
                    ], input=data, capture_output=True, timeout=8)
                    if proc.returncode == 0 and len(proc.stdout) > 1024:
                        data = proc.stdout
                        ctype = "image/jpeg"
                except Exception as _resize_e:
                    sys.stderr.write(f"[thumb-resize] {_resize_e}\n")
            # 디스크에 영구 저장 (resize 후 작아진 사이즈)
            try:
                if len(data) <= 500 * 1024:
                    with open(cache_path, "wb") as f: f.write(data)
                    with open(cache_meta, "w") as f: f.write(ctype)
            except Exception: pass
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("X-Cache", "MISS")
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            self._send_transparent_thumb(str(e))

    def _send_transparent_thumb(self, reason=""):
        """1x1 투명 PNG 응답 — 카드 fallback gradient가 onerror 없이 자연스럽게 채워짐.
        upstream 404/502/timeout 시 브라우저 콘솔에 빨간 에러 X."""
        transparent_png = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff"
            b"\xff?\x00\x05\xfe\x02\xfe\xa1\xc1V\x80\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(transparent_png)))
        self.send_header("Cache-Control", "public, max-age=3600")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("X-Upstream-Note", reason[:120] if reason else "no-upstream")
        self.end_headers()
        self.wfile.write(transparent_png)

    def handle_refresh_trending(self, parsed):
        """crawler + merge 백그라운드 실행. lock·timeout·진행 상태 응답.

        쿼리: ?status=1 → 진행 상태만 응답 (실행 X)
              ?force=1 → 24h fresh 체크 우회
        응답: {status: started|already_running|fresh|done, age_sec, ts, log_tail, video_count}
        """
        import time as _t
        qs = urllib.parse.parse_qs(parsed.query)
        is_status = (qs.get("status") or [""])[0] == "1"
        force = (qs.get("force") or [""])[0] == "1"
        ROOT = os.path.dirname(os.path.abspath(__file__))
        tmp = os.path.join(ROOT, ".tmp")
        os.makedirs(tmp, exist_ok=True)
        lock = os.path.join(tmp, "refresh.lock")
        log = os.path.join(tmp, "refresh.log")
        tv = os.path.join(ROOT, "trending_videos.js")

        def _log_tail(n=15):
            try:
                with open(log, "rb") as f:
                    f.seek(0, 2); size = f.tell()
                    f.seek(max(0, size - 8192))
                    return f.read().decode("utf-8", errors="ignore").splitlines()[-n:]
            except Exception: return []

        def _video_count():
            try:
                with open(tv, "r", encoding="utf-8") as f:
                    txt = f.read()
                m = re.search(r"window\.TRENDING_VIDEOS\s*=\s*(\[)", txt)
                return txt.count('"id"') if m else 0
            except Exception: return 0

        # status only — 실행 X
        if is_status:
            running = os.path.exists(lock)
            age = int(_t.time() - os.path.getmtime(lock)) if running else 0
            tv_age = int(_t.time() - os.path.getmtime(tv)) if os.path.exists(tv) else None
            self._json_ok({
                "running": running, "lock_age_sec": age,
                "tv_age_sec": tv_age, "video_count": _video_count(),
                "log_tail": _log_tail(20),
            })
            return

        # 진행 중 체크 (60분 timeout — crawler 전체 ~40분)
        if os.path.exists(lock):
            age = int(_t.time() - os.path.getmtime(lock))
            if age < 3600:
                self._json_ok({
                    "status": "already_running", "age_sec": age,
                    "log_tail": _log_tail(10),
                })
                return
            # 60분 초과 → stale lock 제거
            try: os.remove(lock)
            except: pass

        # 24시간 이내 마지막 갱신이면 skip (force=1로 우회)
        if not force and os.path.exists(tv):
            age = int(_t.time() - os.path.getmtime(tv))
            if age < 86400:
                self._json_ok({
                    "status": "fresh", "age_sec": age,
                    "next_in_sec": 86400 - age,
                    "video_count": _video_count(),
                })
                return

        # lock + 백그라운드 실행
        open(lock, "w").close()
        ts = _t.strftime("%Y-%m-%d_%H%M")
        cmd = (
            f'cd "{ROOT}" && '
            f'/Users/vx/ai-claude/shortform/.venv/bin/python3 crawler.py --all '
            f'--output "trending_results_{ts}.json" > "{log}" 2>&1; '
            f'/Users/vx/ai-claude/shortform/.venv/bin/python3 merge_trending.py >> "{log}" 2>&1; '
            f'rm -f "{lock}"'
        )
        subprocess.Popen(["/bin/bash", "-c", cmd], start_new_session=True)
        self._json_ok({"status": "started", "ts": ts, "poll": "/api/refresh-trending?status=1"})

    def _json_ok(self, obj):
        import json as _j
        body = _j.dumps(obj).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_ig_stream(self, parsed):
        """Instagram 영상 streaming proxy — 디스크 0 (사장님 효율 요구).
        IG에서 받자마자 클라이언트로 즉시 forward. yt-dlp 큰 process 없음.
        Range 헤더 forward로 사장님 seek/forward 가능.
        IG embed extractor URL이 짧은 TTL (sigs) 가 있어서 디스크 캐시 의미 없음 — 매번 fresh."""
        qs = urllib.parse.parse_qs(parsed.query)
        url = (qs.get("url") or [""])[0]
        if not url or not INSTAGRAM_RE.match(url):
            self._error(400, "invalid url"); return
        # 사장님 지적: 디스크 저장 비효율. 진짜 streaming proxy로 변경 (디스크 0).
        # IG에서 받자마자 클라이언트로 즉시 forward. yt-dlp 같은 큰 process 0.
        # Range 헤더도 upstream에 그대로 forward (사장님 seek 가능).
        try:
            video_url = extract_instagram_video_url(url)
            if not video_url:
                self._error(404, "no video_url"); return
            up_headers = {
                "User-Agent": ua(),
                "Referer": "https://www.instagram.com/",
            }
            # 클라이언트 Range 요청 그대로 forward
            range_header = self.headers.get("Range")
            if range_header:
                up_headers["Range"] = range_header
            req = urllib.request.Request(video_url, headers=up_headers)
            upstream = urllib.request.urlopen(req, timeout=15)
            cl_raw = upstream.headers.get("Content-Length")
            cl_int = None
            if cl_raw:
                try: cl_int = int(str(cl_raw).split(",")[0].strip())
                except Exception: pass
            up_status = upstream.status if hasattr(upstream, "status") else 200
            self.send_response(up_status)
            self.send_header("Content-Type", upstream.headers.get("Content-Type") or "video/mp4")
            if cl_int and cl_int > 0:
                self.send_header("Content-Length", str(cl_int))
            self.send_header("Accept-Ranges", "bytes")
            # upstream의 Content-Range도 forward (Range 응답 시)
            cr = upstream.headers.get("Content-Range")
            if cr:
                self.send_header("Content-Range", cr)
            self.end_headers()
            # 직접 chunks forward — 디스크 0
            while True:
                try:
                    chunk = upstream.read(64 * 1024)
                except Exception:
                    break
                if not chunk: break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    break
        except urllib.error.HTTPError as e:
            try: self._error(e.code if e.code else 502, f"upstream {e.code}")
            except Exception: pass
        except Exception as e:
            try: self._error(502, f"stream 실패: {e}")
            except Exception: pass

    def handle_meta(self, parsed):
        """플랫폼별 메타 통합 — views·duration·uploadDate·caption
        YouTube: yt-dlp → 차단 시 oEmbed fallback, TikTok: TikWM, Instagram: embed extractor"""
        qs = urllib.parse.parse_qs(parsed.query)
        url = (qs.get("url") or [""])[0]
        if not url or not ALLOWED_HOSTS.match(url):
            self._error(400, "invalid url"); return
        # 메모리 캐시 hit — 즉시 응답
        cached = _mem_cache_get(url)
        if cached:
            self._json(200, cached); return
        out = {"views": None, "duration": None, "uploadDate": None, "caption": None, "source": None}
        try:
            if INSTAGRAM_RE.match(url):
                ig = extract_instagram_meta(url) or {}
                out["views"] = ig.get("views")
                out["caption"] = ig.get("caption")
                out["uploadDate"] = ig.get("uploadDate")
                out["likes"] = ig.get("likes")
                out["comments"] = ig.get("comments")
                if ig.get("channel"): out["channel"] = ig["channel"]
                src_parts = ["Instagram embed"] if ig else []
                # duration은 IG embed에 없음 → yt-dlp 시도 (안 되면 mp4 ffprobe)
                ytmeta = ytdlp_print_meta(url) or {}
                if ytmeta.get("duration"):
                    out["duration"] = ytmeta["duration"]
                    src_parts.append("yt-dlp")
                # 그래도 duration 없으면 video_url에서 ffprobe로 추출
                if not out.get("duration") and ig.get("video_url"):
                    dur = _probe_mp4_duration(ig["video_url"])
                    if dur:
                        out["duration"] = dur
                        src_parts.append("ffprobe")
                # yt-dlp views 채워주기 (embed에서 못 받았을 때)
                if not out.get("views") and ytmeta.get("views"):
                    out["views"] = ytmeta["views"]
                if not out.get("uploadDate") and ytmeta.get("uploadDate"):
                    out["uploadDate"] = ytmeta["uploadDate"]
                if not out.get("channel") and ytmeta.get("channel"):
                    out["channel"] = ytmeta["channel"]
                if not src_parts:
                    out["source"] = "Instagram embed 차단 (로그인 필요·삭제·비공개 가능)"
                else:
                    out["source"] = " + ".join(src_parts)
            elif "tiktok.com" in url:
                # TikWM 우선 (빠름·정확) — 3초 timeout, 실패하면 즉시 yt-dlp 보강
                try:
                    tw = urllib.request.urlopen(
                        urllib.request.Request(
                            f"https://www.tikwm.com/api/?url={urllib.parse.quote(url)}",
                            headers={"User-Agent": ua()}
                        ), timeout=3
                    )
                    data = json.loads(tw.read())
                    if data.get("code") == 0 and data.get("data"):
                        d = data["data"]
                        out["views"] = d.get("play_count")
                        out["duration"] = d.get("duration")
                        out["caption"] = d.get("title")
                        out["likes"] = d.get("digg_count")
                        out["comments"] = d.get("comment_count")
                        out["shares"] = d.get("share_count")
                        out["saves"] = d.get("collect_count")
                        out["downloads"] = d.get("download_count")
                        if d.get("author"):
                            au = d["author"]
                            out["channel"] = au.get("unique_id") or au.get("nickname")
                            if au.get("avatar"): out["channelAvatar"] = au.get("avatar")
                        # 음악 정보
                        if d.get("music_info"):
                            mi = d["music_info"]
                            out["music"] = {
                                "title": mi.get("title"),
                                "author": mi.get("author"),
                                "original": mi.get("original"),
                                "id": mi.get("id"),
                                "play_url": mi.get("play"),
                                "cover": mi.get("cover"),
                            }
                        # 해시태그 추출 (caption에서)
                        if out.get("caption"):
                            import re as _re
                            tags = _re.findall(r"#([\w가-힣]+)", out["caption"])
                            if tags: out["tags"] = tags[:10]
                        if d.get("create_time"):
                            import datetime
                            out["uploadDate"] = datetime.datetime.fromtimestamp(d["create_time"]).strftime("%Y-%m-%d")
                        out["source"] = "TikWM"
                except Exception: pass
                # 부족하면 yt-dlp 보강
                if not out["duration"]:
                    ytmeta = ytdlp_print_meta(url) or {}
                    for k in ("views","duration","uploadDate","caption"):
                        if not out[k] and ytmeta.get(k): out[k] = ytmeta[k]
                    if not out["source"] and ytmeta:
                        out["source"] = "yt-dlp"
                if not out["source"]:
                    out["source"] = "TikWM·yt-dlp 둘 다 차단 (영상 삭제·비공개 가능)"
            else:
                # YouTube — 3개 무로그인 소스 병렬: HTML 직접 fetch + RYD API + Invidious
                import concurrent.futures as _cf
                yt_results = {}
                with _cf.ThreadPoolExecutor(max_workers=3) as ex:
                    fut_html = ex.submit(fetch_youtube_html, url)
                    fut_ryd = ex.submit(fetch_youtube_rydapi, url)
                    fut_inv = ex.submit(fetch_youtube_invidious, url)
                    try:
                        yt_results["html"] = fut_html.result(timeout=3.5)
                    except Exception: yt_results["html"] = None
                    try:
                        yt_results["ryd"] = fut_ryd.result(timeout=2.5)
                    except Exception: yt_results["ryd"] = None
                    try:
                        yt_results["inv"] = fut_inv.result(timeout=3.5)
                    except Exception: yt_results["inv"] = None

                src_parts = []
                # 우선순위: Invidious (모든 메타 있음) > HTML (views/likes/channel/title/date) > RYD (views/likes 캐시)
                if yt_results["inv"]:
                    out.update({k: v for k, v in yt_results["inv"].items() if v is not None})
                    src_parts.append("Invidious")
                if yt_results["html"]:
                    for k, v in yt_results["html"].items():
                        if k == "source": continue
                        if not out.get(k) and v is not None:
                            out[k] = v
                    src_parts.append("youtube.com HTML")
                if yt_results["ryd"]:
                    # RYD가 더 정확한 views·likes 가능성 → HTML 값 없거나 0이면 보강
                    if not out.get("views") and yt_results["ryd"].get("views"):
                        out["views"] = yt_results["ryd"]["views"]
                    if not out.get("likes") and yt_results["ryd"].get("likes"):
                        out["likes"] = yt_results["ryd"]["likes"]
                    if yt_results["ryd"].get("dislikes") is not None:
                        out["dislikes"] = yt_results["ryd"]["dislikes"]
                    src_parts.append("RYD")
                # 보강 — yt-dlp 시도 (드물게 통과)
                if not out.get("duration"):
                    ytmeta = ytdlp_print_meta(url) or {}
                    if ytmeta.get("duration"): out["duration"] = ytmeta["duration"]
                    if not out.get("views") and ytmeta.get("views"): out["views"] = ytmeta["views"]
                    if ytmeta: src_parts.append("yt-dlp")
                # channel/title 마지막 보강 — oEmbed
                if not out.get("channel") or not out.get("caption"):
                    oembed = fetch_youtube_oembed(url) or {}
                    if not out.get("channel") and oembed.get("channel"): out["channel"] = oembed["channel"]
                    if not out.get("caption") and oembed.get("caption"): out["caption"] = oembed["caption"]
                    if oembed: src_parts.append("oEmbed")
                if not src_parts:
                    out["source"] = "전체 차단 (영상 비공개·삭제·지역 제한 가능)"
                else:
                    out["source"] = " + ".join(src_parts)
            _mem_cache_set(url, out)
            self._json(200, out)
        except Exception as e:
            self._error(500, f"메타 추출 실패: {e}")

    def handle_transcript(self, parsed):
        """영상 자막 추출 — yt-dlp 자동 자막 우선, 실패하면 플랫폼별 caption fallback"""
        qs = urllib.parse.parse_qs(parsed.query)
        url = (qs.get("url") or [""])[0]
        if not url or not ALLOWED_HOSTS.match(url):
            self._error(400, "invalid url"); return
        # 메모리 캐시 — 같은 url 자막 요청 즉시 응답
        cache_key = "sub::" + url
        cached = _mem_cache_get(cache_key)
        if cached:
            self._json(200, cached); return
        tmp = tempfile.mkdtemp(prefix="hookpilot_sub_")
        out_tmpl = os.path.join(tmp, "sub.%(ext)s")
        result = None
        try:
            cmd = [
                YTDLP,
                "--skip-download",
                "--write-auto-subs", "--write-subs",
                "--sub-lang", "ko,en,en-orig",
                "--sub-format", "vtt",
                "--no-warnings", "--quiet",
                "--socket-timeout", "4",
                "--user-agent", ua(),
                "-o", out_tmpl,
                url,
            ]
            subprocess.run(cmd, capture_output=True, timeout=5)
            files = sorted(glob.glob(os.path.join(tmp, "sub*.vtt")))
            ko_files = [f for f in files if ".ko." in f]
            en_files = [f for f in files if ".en" in f]
            target = ko_files[0] if ko_files else (en_files[0] if en_files else (files[0] if files else None))
            if target:
                with open(target, "r", encoding="utf-8", errors="ignore") as f:
                    vtt = f.read()
                text = self._parse_vtt(vtt)
                segments = self._parse_vtt_segments(vtt)
                if text:
                    lang = "ko" if ".ko." in target else "en"
                    result = {"ok": True, "lang": lang, "type": "subtitle", "text": text, "chars": len(text), "segments": segments}
        except Exception:
            pass
        finally:
            try:
                for f in os.listdir(tmp): os.unlink(os.path.join(tmp, f))
                os.rmdir(tmp)
            except Exception: pass

        # 자막 실패 → caption fallback (TikTok·Instagram·YouTube description)
        if not result:
            cap = None
            cap_source = None
            try:
                if INSTAGRAM_RE.match(url):
                    ig = extract_instagram_meta(url) or {}
                    cap = ig.get("caption"); cap_source = "Instagram 캡션"
                elif "tiktok.com" in url:
                    try:
                        tw = urllib.request.urlopen(
                            urllib.request.Request(
                                f"https://www.tikwm.com/api/?url={urllib.parse.quote(url)}",
                                headers={"User-Agent": ua()}
                            ), timeout=3
                        )
                        d = json.loads(tw.read())
                        if d.get("code") == 0:
                            cap = d.get("data", {}).get("title"); cap_source = "TikTok 캡션"
                    except Exception: pass
                else:
                    # YouTube — Invidious description 우선, 없으면 HTML title (해시태그 포함)
                    inv = fetch_youtube_invidious(url) or {}
                    desc = inv.get("description") or inv.get("caption")
                    if desc:
                        cap = desc; cap_source = "YouTube 설명 (Invidious)"
                    else:
                        yh = fetch_youtube_html(url) or {}
                        if yh.get("caption"):
                            cap = yh["caption"]; cap_source = "YouTube 제목+해시태그"
            except Exception: pass
            if cap:
                result = {"ok": True, "lang": "ko", "type": "caption",
                          "text": cap[:2000], "chars": len(cap[:2000]),
                          "source": cap_source}
            else:
                result = {"ok": False, "reason": "자동 캡션·자막·영상 설명 모두 없음"}
        _mem_cache_set(cache_key, result)
        self._json(200, result)

    @staticmethod
    def _parse_vtt(vtt):
        """VTT → 평문. 시간 코드 + inline timestamp 제거, 중복 라인 합치기."""
        lines = vtt.split("\n")
        seen = []
        prev = None
        for ln in lines:
            t = ln.strip()
            if not t or t == "WEBVTT" or t.startswith("Kind:") or t.startswith("Language:"):
                continue
            if "-->" in t: continue
            if re.match(r"^\d+$", t): continue
            # inline <00:00:00.000><c> tag 제거
            clean = re.sub(r"<[^>]+>", "", t).strip()
            if not clean: continue
            if clean == prev: continue
            seen.append(clean)
            prev = clean
        return " ".join(seen)[:3000]

    @staticmethod
    def _parse_vtt_segments(vtt):
        """VTT → 시간 구간별 [{start, end, text}, ...] 배열.
        후킹 분석용 — 시간대별 어떤 멘트 나왔는지 시각화 가능."""
        def _ts_to_sec(ts):
            # 00:00:03.500 → 3.5
            m = re.match(r'(\d+):(\d+):(\d+\.\d+)', ts)
            if not m: return 0.0
            h, mn, s = m.groups()
            return int(h) * 3600 + int(mn) * 60 + float(s)
        segs = []
        cur_start = None
        cur_end = None
        cur_text = []
        for ln in vtt.split("\n"):
            t = ln.strip()
            if "-->" in t:
                # 이전 segment 저장
                if cur_start is not None and cur_text:
                    txt = " ".join(cur_text).strip()
                    if txt:
                        segs.append({"start": round(cur_start, 1), "end": round(cur_end or cur_start, 1), "text": txt})
                parts = t.split("-->")
                cur_start = _ts_to_sec(parts[0].strip())
                cur_end = _ts_to_sec(parts[1].strip().split(" ")[0])
                cur_text = []
            elif t and t != "WEBVTT" and not t.startswith("Kind:") and not t.startswith("Language:") and not re.match(r"^\d+$", t):
                clean = re.sub(r"<[^>]+>", "", t).strip()
                if clean:
                    cur_text.append(clean)
        # 마지막
        if cur_start is not None and cur_text:
            txt = " ".join(cur_text).strip()
            if txt:
                segs.append({"start": round(cur_start, 1), "end": round(cur_end or cur_start, 1), "text": txt})
        # 동일 텍스트 인접 segment 합치기 (yt-dlp가 같은 자막 여러 번 반복)
        merged = []
        for s in segs:
            if merged and merged[-1]["text"] == s["text"]:
                merged[-1]["end"] = s["end"]
            else:
                merged.append(s)
        return merged[:80]

    def handle_prepare(self, parsed):
        """1단계 — mp4를 디스크 임시 저장 후 정적 파일 URL 반환
        디스크 파일명은 영문 ID만 (ASCII-only path), 사용자 표시명은 별도"""
        qs = urllib.parse.parse_qs(parsed.query)
        url = (qs.get("url") or [""])[0]
        title_hint = (qs.get("title") or ["video"])[0]
        if not url:
            self._error(400, "missing url"); return
        if not ALLOWED_HOSTS.match(url):
            self._error(400, "invalid url"); return
        # 오래된 파일 정리 (lazy)
        self._cleanup_old_files()
        # 디스크 파일은 영문 UUID만 — directory traversal·URL escape 문제 회피
        file_id = uuid.uuid4().hex
        safe_user_name = safe_filename(title_hint, "video")
        # === Instagram embed extractor (로그인 불필요) ===
        if INSTAGRAM_RE.match(url):
            try:
                video_url = extract_instagram_video_url(url)
                if video_url:
                    out_name = f"{file_id}.mp4"
                    out_path = os.path.join(DL_DIR, out_name)
                    req = urllib.request.Request(video_url, headers={
                        "User-Agent": ua(),
                        "Referer": "https://www.instagram.com/",
                    })
                    with urllib.request.urlopen(req, timeout=60) as up, open(out_path, "wb") as f:
                        while True:
                            chunk = up.read(64 * 1024)
                            if not chunk: break
                            f.write(chunk)
                    size = os.path.getsize(out_path)
                    self._json(200, {
                        "file_url": f"/api/file/{out_name}",
                        "download_name": safe_user_name + ".mp4",
                        "size": size,
                    })
                    return
            except Exception as e:
                sys.stderr.write(f"[prepare-ig] {e}\n")
        # === yt-dlp 일반 처리 — 속도 최적화 ===
        # 사장님 다운로드 51초 → 15-20초 목표:
        # 1) 720p 이하 mp4 우선 (Shorts는 720p 충분, 4K 무거움)
        # 2) --concurrent-fragments 4 (멀티 fragment 동시 다운)
        # 3) --socket-timeout 10 (느린 fragment 자동 skip)
        out_tmpl = os.path.join(DL_DIR, f"{file_id}.%(ext)s")
        cmd = [
            YTDLP,
            "-f", "best[height<=720][ext=mp4]/best[ext=mp4]/best",
            "--concurrent-fragments", "4",
            "--socket-timeout", "10",
            "--no-playlist", "--no-warnings", "--quiet", "--no-progress",
            "-o", out_tmpl,
            url,
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=DOWNLOAD_TIMEOUT_SEC)
            if proc.returncode != 0:
                err = proc.stderr.decode("utf-8", errors="ignore")[:300]
                if "login required" in err.lower() or "rate-limit" in err.lower():
                    self._error(401, "비공개 영상 또는 로그인 필요"); return
                self._error(500, f"추출 실패: {err}"); return
            files = glob.glob(os.path.join(DL_DIR, f"{file_id}.*"))
            if not files:
                self._error(500, "다운로드 결과 없음"); return
            actual = files[0]
            out_name = os.path.basename(actual)
            size = os.path.getsize(actual)
            ext = os.path.splitext(actual)[1] or ".mp4"
            self._json(200, {
                "file_url": f"/api/file/{out_name}",
                "download_name": safe_user_name + ext,
                "size": size,
            })
        except subprocess.TimeoutExpired:
            self._error(504, "추출 시간 초과 (5분)")
        except Exception as e:
            self._error(500, f"서버 오류: {e}")

    def handle_file(self, parsed):
        """2단계 — 디스크 저장된 mp4 정적 서빙 (attachment)
        파일명은 영문 UUID만 — directory traversal·encoding 문제 없음"""
        name = parsed.path[len("/api/file/"):]
        # URL 디코드
        name = urllib.parse.unquote(name)
        # 안전 검증 — 영문 UUID 형식만 허용 (32 hex + .ext)
        if not re.match(r"^[a-f0-9]{32}\.[a-z0-9]+$", name):
            self._error(400, "invalid filename"); return
        fpath = os.path.join(DL_DIR, name)
        if not os.path.isfile(fpath):
            self._error(404, "파일 만료 — 다시 다운로드 받기"); return
        # 사용자 표시 다운로드 이름 (한글 가능) — RFC 5987 인코딩
        qs = urllib.parse.parse_qs(parsed.query)
        dl_name = (qs.get("name") or [name])[0]
        # ASCII 안전한 fallback (한글 등 non-ASCII 모두 _로 치환)
        ascii_fallback = re.sub(r"[^a-zA-Z0-9._\-]", "_", dl_name)[:60] or "video.mp4"
        # UTF-8 인코딩 (RFC 5987) — 한글 브라우저 표시
        utf8_encoded = urllib.parse.quote(dl_name, safe="")
        size = os.path.getsize(fpath)
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(size))
        # RFC 5987 — 한글 파일명도 브라우저에서 정확히 표시
        self.send_header(
            "Content-Disposition",
            f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{utf8_encoded}'
        )
        self.end_headers()
        with open(fpath, "rb") as f:
            while True:
                chunk = f.read(64 * 1024)
                if not chunk: break
                try: self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError): break

    def _cleanup_old_files(self):
        """1시간 이상 된 다운로드 파일 정리 (lazy 호출)"""
        try:
            now = time.time()
            for fname in os.listdir(DL_DIR):
                fpath = os.path.join(DL_DIR, fname)
                try:
                    if os.path.isfile(fpath) and now - os.path.getmtime(fpath) > FILE_TTL_SEC:
                        os.unlink(fpath)
                except Exception: pass
        except Exception: pass

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try: self.wfile.write(body)
        except Exception: pass

    def handle_probe(self, parsed):
        # yt-dlp 가용 여부 + 버전 응답 (simple.html에서 백엔드 살아있는지 체크)
        try:
            ver = subprocess.run(
                [YTDLP, "--version"], capture_output=True, timeout=5, check=True
            ).stdout.decode().strip()
            body = json.dumps({"ok": True, "ytdlp": ver}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            body = json.dumps({"ok": False, "err": str(e)}).encode()
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def handle_download(self, parsed):
        qs = urllib.parse.parse_qs(parsed.query)
        url = (qs.get("url") or [""])[0]
        title_hint = (qs.get("title") or ["video"])[0]

        if not url:
            self._error(400, "missing url")
            return
        if not ALLOWED_HOSTS.match(url):
            self._error(400, "invalid url — youtube/tiktok/instagram만 허용")
            return

        # === Instagram: embed/captioned 페이지에서 video_url 추출 (로그인 불필요) ===
        if INSTAGRAM_RE.match(url):
            try:
                video_url = extract_instagram_video_url(url)
                if video_url:
                    fname = safe_filename(title_hint, "instagram") + ".mp4"
                    stream_url_to_client(self, video_url, fname)
                    return
            except Exception as e:
                sys.stderr.write(f"[ig-extract] failed: {e}\n")
            # extraction 실패 시 yt-dlp로 fallback (사용자가 cookies 있으면 통과 가능)

        # 임시 폴더에 다운로드 → 파일 읽어서 stream → 파일 삭제
        tmp = tempfile.mkdtemp(prefix="hookpilot_dl_")
        outpath = os.path.join(tmp, "video.%(ext)s")
        cmd = [
            YTDLP,
            "-f", "best[ext=mp4]/best",
            "--no-playlist",
            "--no-warnings",
            "--quiet",
            "--no-progress",
            "-o", outpath,
            url,
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, timeout=DOWNLOAD_TIMEOUT_SEC
            )
            if proc.returncode != 0:
                err = proc.stderr.decode("utf-8", errors="ignore")[:500]
                # Instagram 로그인 필요한 경우
                if "login required" in err.lower() or "rate-limit" in err.lower():
                    self._error(401, "Instagram 로그인 필요 — 외부 다운로더 사용하세요")
                else:
                    self._error(500, f"yt-dlp 실패: {err}")
                return
            # 생성된 파일 찾기
            files = [f for f in os.listdir(tmp) if not f.startswith(".")]
            if not files:
                self._error(500, "다운로드 파일 생성 실패")
                return
            fpath = os.path.join(tmp, files[0])
            ext = os.path.splitext(files[0])[1].lstrip(".") or "mp4"
            size = os.path.getsize(fpath)
            fname = safe_filename(title_hint) + "." + ext

            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(size))
            self.send_header(
                "Content-Disposition",
                f'attachment; filename="{fname}"'
            )
            self.end_headers()
            with open(fpath, "rb") as f:
                while True:
                    chunk = f.read(64 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except subprocess.TimeoutExpired:
            self._error(504, "다운로드 시간 초과 (5분)")
        except Exception as e:
            self._error(500, f"서버 오류: {e}")
        finally:
            # 임시 폴더 정리
            try:
                for f in os.listdir(tmp):
                    os.unlink(os.path.join(tmp, f))
                os.rmdir(tmp)
            except Exception:
                pass

    def _error(self, code, msg):
        body = json.dumps({"error": msg}).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def log_message(self, fmt, *args):
        # 간소화된 로그
        sys.stderr.write(f"[hookpilot] {self.address_string()} {fmt % args}\n")


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True


def _git_pull_loop():
    """매 5분 git pull — GitHub Actions 가 push 한 새 trending_videos.js 자동 동기화.
    사장님 Mac 외부 fetch X. git 호출 (GitHub) 만. 사장님 SNS 안전.
    """
    import threading, time as _t, subprocess
    def _run():
        # 5분 후 첫 pull (백엔드 시작 직후 충돌 회피)
        _t.sleep(60)
        while True:
            try:
                r = subprocess.run(
                    ["git", "pull", "--ff-only"],
                    cwd=ROOT, capture_output=True, timeout=30, check=False
                )
                if r.returncode == 0 and b"Already up to date" not in r.stdout:
                    print(f"[git-pull] 새 풀 동기화: {r.stdout.decode()[:100]}")
            except Exception as e:
                sys.stderr.write(f"[git-pull] skip: {e}\n")
            _t.sleep(300)  # 5분 마다
    threading.Thread(target=_run, daemon=True).start()


def _daily_cron_loop():
    """매일 03:00 + 15:00 crawler + merge 자동 실행 — 백엔드 살아있는 한 매일 신선한 영상 자동 갱신.
    Hang-proof: subprocess.run timeout=1800 (30분 hard limit), 실패 시 1시간 후 재시도.
    하루 2회 갱신: 새벽 + 점심 — 사장님이 언제 들어와도 그날 신선한 영상.
    """
    import threading, time as _t, subprocess
    from datetime import datetime, timedelta
    # 매일 6시간 간격 4회 갱신 — 한 슬롯 실패해도 6시간 안에 회복.
    # 사장님 요구: 매일 신선한 영상, 풀 stale 방지.
    SLOTS = [3, 9, 15, 21]
    def _run_once(tag):
        ts = datetime.now().strftime("%Y-%m-%d_%H%M")
        out_path = f"trending_results_{ts}_{tag}.json"
        log_path = f".tmp/cron_{ts}_{tag}.log"
        print(f"[cron-{tag}] 갱신 시작 — {out_path}")
        try:
            with open(log_path, "w") as logf:
                subprocess.run(
                    ["/Users/vx/ai-claude/shortform/.venv/bin/python3", "-u", "crawler.py",
                     "--all", "--fast", "--output", out_path],
                    cwd=ROOT, stdout=logf, stderr=subprocess.STDOUT,
                    timeout=1800, check=False
                )
                subprocess.run(
                    ["/Users/vx/ai-claude/shortform/.venv/bin/python3", "merge_trending.py"],
                    cwd=ROOT, stdout=logf, stderr=subprocess.STDOUT,
                    timeout=300, check=False
                )
            print(f"[cron-{tag}] 갱신 끝 — 로그 {log_path}")
            # 진단 로그: today 신규 카운트 → 사장님이 cron 동작 검증 가능
            try:
                import re, json
                tv_path = os.path.join(ROOT, "trending_videos.js")
                with open(tv_path, "r", encoding="utf-8") as tf:
                    tv_txt = tf.read()
                m = re.search(r"window\.TRENDING_VIDEOS\s*=\s*(\[.*?\]);", tv_txt, re.DOTALL)
                if m:
                    arr = json.loads(m.group(1))
                    today_str = datetime.now().strftime("%Y-%m-%d")
                    by_plat = {"Shorts": 0, "TikTok": 0, "Reels": 0}
                    by_country = {"KR": 0, "US": 0}
                    for v in arr:
                        if v.get("firstSeen") == today_str:
                            if v.get("platform") in by_plat:
                                by_plat[v.get("platform")] += 1
                            if v.get("country") in by_country:
                                by_country[v.get("country")] += 1
                    total = sum(by_plat.values())
                    print(f"[cron-{tag}] 오늘 신규 (firstSeen={today_str}): 합계 {total} — TT {by_plat['TikTok']} / Shorts {by_plat['Shorts']} / Reels {by_plat['Reels']}")
                    print(f"[cron-{tag}] 국가별: KR {by_country['KR']} / US {by_country['US']}")
                    print(f"[cron-{tag}] 전체 풀: {len(arr)}개")
                    # 신규 0개면 진짜 실패 — 외부 API 일시적 timeout일 가능성, retry 발동
                    if total == 0:
                        print(f"[cron-{tag}] 신규 0개 → 외부 API 일시적 실패, retry 발동")
                        return False
            except Exception as diag_e:
                print(f"[cron-{tag}] 진단 로그 skip: {diag_e}")
            return True
        except subprocess.TimeoutExpired:
            print(f"[cron-{tag}] 30분 timeout — kill하고 다음 슬롯 대기")
            return False
        except Exception as e:
            print(f"[cron-{tag}] 오류: {e}")
            return False
    def _next_slot():
        now = datetime.now()
        for h in SLOTS:
            t = now.replace(hour=h, minute=0, second=0, microsecond=0)
            if t > now:
                return t
        # 오늘 모든 슬롯 지남 → 내일 첫 슬롯
        return (now + timedelta(days=1)).replace(hour=SLOTS[0], minute=0, second=0, microsecond=0)
    def _run_dep_update():
        """매일 03시 cron 직전에 의존성 자동 업데이트 (patch/minor 자동, major 알림). 실패 시 자동 rollback."""
        try:
            log_path = f".tmp/dep_update_{datetime.now().strftime('%Y-%m-%d')}.log"
            subprocess.run(
                ["/Users/vx/ai-claude/shortform/.venv/bin/python3", "daily_dependency_update.py"],
                cwd=ROOT, timeout=600, check=False
            )
            print(f"[dep-update] 완료 — 로그 {log_path}")
        except Exception as e:
            print(f"[dep-update] skip: {e}")
    def _run_health_check():
        """매 cron run 후 자동 회귀 검증 — BACKEND_BASE port 불일치·서버 죽음·API endpoint 회귀 자동 잡음.
        깨진 부분 발견 시 가능한 것 자동 수정 (예: BACKEND_BASE port 일치). 사장님 일일이 지적 X."""
        try:
            subprocess.run(
                ["/Users/vx/ai-claude/shortform/.venv/bin/python3", "health_check.py"],
                cwd=ROOT, timeout=120, check=False
            )
        except Exception as e:
            print(f"[health-check] skip: {e}")
    def _run_regression_tests():
        """매 cron 후 + 매 hour 자동 회귀 테스트. 사장님이 한 번 지적한 모든 회귀 영구 차단.
        새 회귀 발견 시마다 regression_tests.py에 testcase 추가 → 영구 차단."""
        try:
            r = subprocess.run(
                ["/Users/vx/ai-claude/shortform/.venv/bin/python3", "regression_tests.py"],
                cwd=ROOT, timeout=180, capture_output=True
            )
            if r.returncode != 0:
                # 회귀 발견 — 사장님께 즉시 알릴 수 있도록 stderr/stdout 로그 보존
                print(f"[regression] 회귀 발견! 로그: .tmp/regression_{datetime.now().strftime('%Y-%m-%d')}.log")
                print(r.stdout.decode()[-500:] if r.stdout else "")
            else:
                # testcase 수 메시지 stale 방지 — 동적으로 카운트
                import re as _re_local
                try:
                    with open(os.path.join(ROOT, "regression_tests.py"), "r") as _rf:
                        _n = len(_re_local.findall(r"^def test_\d+", _rf.read(), _re_local.M))
                    print(f"[regression] {_n} testcase 모두 통과")
                except Exception:
                    print(f"[regression] 모두 통과")
        except Exception as e:
            print(f"[regression] skip: {e}")
    def _purge_old_artifacts():
        """7일 이전 cron 중간 결과 자동 정리 — 디스크 누수 방지.
        trending_results_*.json + .tmp/cron_*.log + .tmp/dep_update_*.log
        사용자 다운로드한 mp4 (.tmp/ig 안) 는 30일 보호 — 사용자 메모리 '파일 임의 삭제 금지' 보호."""
        try:
            from datetime import datetime, timedelta
            cutoff_7d = (datetime.now() - timedelta(days=7)).timestamp()
            cutoff_30d = (datetime.now() - timedelta(days=30)).timestamp()
            purged_7d = 0
            purged_30d = 0
            # trending_results_*.json (cron 중간 결과, 사용자 손 안 댐)
            for fn in os.listdir(ROOT):
                # trending_results_*.json 영구 보존 — 매일 새 영상 추가하고 옛 영상 누적해야 사장님 요구 (1주일 7천) 충족.
                # 삭제하면 그 안 영상 영구 손실. 디스크 점유 좀 늘어도 풀 누적이 우선.
                # (이전 회귀: 7일 자동 삭제 → 5/16-17 옛 영상 사라질 위기 → 풀 양 줄어드는 진짜 원인)
                pass  # trending_results_*.json 삭제 X
            # .tmp 안 옛 로그
            tmp_dir = os.path.join(ROOT, ".tmp")
            if os.path.isdir(tmp_dir):
                for fn in os.listdir(tmp_dir):
                    if fn.startswith(("cron_", "dep_update_", "c_", "crawler_run", "regression_")) and fn.endswith(".log"):
                        fp = os.path.join(tmp_dir, fn)
                        try:
                            if os.path.getmtime(fp) < cutoff_7d:
                                os.remove(fp); purged_7d += 1
                        except Exception: pass
                # IG 다운로드 캐시 — 30일 보호 (사용자가 자주 보는 영상은 자동 갱신)
                ig_dir = os.path.join(tmp_dir, "ig")
                if os.path.isdir(ig_dir):
                    for root, _, files in os.walk(ig_dir):
                        for fn in files:
                            if fn.endswith((".mp4", ".mp4.tmp", ".json", ".txt")):
                                fp = os.path.join(root, fn)
                                try:
                                    if os.path.getmtime(fp) < cutoff_30d:
                                        os.remove(fp); purged_30d += 1
                                except Exception: pass
            if purged_7d or purged_30d:
                print(f"[purge] 7일+ 옛 결과 {purged_7d}개 / 30일+ IG 캐시 {purged_30d}개 자동 정리")
        except Exception as e:
            print(f"[purge] skip: {e}")
    def _run():
        while True:
            try:
                nxt = _next_slot()
                sleep_sec = (nxt - datetime.now()).total_seconds()
                print(f"[cron] 다음 자동 갱신: {nxt.strftime('%Y-%m-%d %H:%M')} ({int(sleep_sec)}초 후)")
                _t.sleep(max(60, sleep_sec))
                # 매일 03시 슬롯에서만 dep update + 디스크 정리 (15시는 스킵 — 하루 1회로 충분)
                if nxt.hour == SLOTS[0]:
                    _run_dep_update()
                    _purge_old_artifacts()
                ok = _run_once(f"{nxt.hour:02d}h")
                # 매 cron run 후 자동 회귀 검증
                _run_health_check()
                _run_regression_tests()  # 사장님 한 번 지적한 회귀 영구 차단
                # 실패 시 최대 3회 재시도 — 사장님 화면이 12시간 동안 stale 안 되게
                # TikWM 같은 외부 API 일시적 timeout → 5분 후 재시도. 회복 빠르게.
                retry_n = 0
                while not ok and retry_n < 3:
                    retry_n += 1
                    wait_min = 5 if retry_n == 1 else (15 if retry_n == 2 else 60)
                    print(f"[cron] 재시도 {retry_n}/3 — {wait_min}분 후")
                    _t.sleep(wait_min * 60)
                    ok = _run_once(f"{datetime.now().hour:02d}h_retry{retry_n}")
                    if ok:
                        _run_health_check()
                        _run_regression_tests()
            except Exception as e:
                print(f"[cron] loop 오류: {e}. 1시간 후 재시도")
                _t.sleep(3600)
    threading.Thread(target=_run, daemon=True).start()


def _hourly_ig_cron_loop():
    """IG 분산 cron — Reddit hot posts가 시간 단위 거의 안 변해서 6시간 간격으로 변경.
    매 6시간 (00·06·12·18시) DDG + Reddit 55+ sub 호출. 03시 daily cron과 겹치지 않게 6시간 슬롯.
    검증 결과: 매시간 호출하면 같은 데이터 반환 (자원 낭비). 6시간 간격이 새 게시물 들어올 시간 충분.
    """
    import threading, time as _t, random
    from datetime import datetime
    SLOTS = [0, 6, 12, 18]  # 매일 4회 (자정·06·12·18)
    def _next_slot():
        now = datetime.now()
        for h in SLOTS:
            t = now.replace(hour=h, minute=30, second=0, microsecond=0)
            if t > now: return t
        from datetime import timedelta
        return (now + timedelta(days=1)).replace(hour=SLOTS[0], minute=30, second=0, microsecond=0)
    def _run():
        _t.sleep(30)  # 백엔드 시작 30초 후 첫 실행
        while True:
            try:
                # 우선 즉시 1회 실행 (백엔드 시작 직후 fresh IG)
                ts = datetime.now().strftime("%Y-%m-%d_%H")
                output_path = f"trending_results_{ts}_ig.json"
                print(f"[ig-cron] {ts} IG 수집 시작 — {output_path}")
                rc = os.system(
                    f'cd "{ROOT}" && '
                    f'/Users/vx/ai-claude/shortform/.venv/bin/python3 -u crawler.py --instagram --hourly '
                    f'--output "{output_path}" >> .tmp/ig_cron_{ts[:10]}.log 2>&1'
                )
                if rc == 0:
                    os.system(f'cd "{ROOT}" && /Users/vx/ai-claude/shortform/.venv/bin/python3 merge_trending.py >> .tmp/ig_cron_{ts[:10]}.log 2>&1')
                # 다음 6시간 슬롯까지 대기 (±10분 jitter)
                nxt = _next_slot()
                sleep_sec = (nxt - datetime.now()).total_seconds() + random.uniform(-600, 600)
                print(f"[ig-cron] 다음 IG 수집: {nxt.strftime('%H:%M')} ({int(sleep_sec/60)}분 후)")
                _t.sleep(max(300, sleep_sec))
            except Exception as e:
                print(f"[ig-cron] 오류: {e}. 1시간 후 재시도")
                _t.sleep(3600)
    threading.Thread(target=_run, daemon=True).start()


def main():
    os.chdir(ROOT)
    print(f"[hookpilot] 백엔드 시작 — http://localhost:{PORT}")
    print(f"[hookpilot] 정적 파일: {ROOT}")
    print(f"[hookpilot] yt-dlp: {YTDLP}")
    # === GitHub Actions 모드 (사장님 SNS 계정 보호) ===
    # 사장님 Mac 에서 외부 fetch 비활성화 — Actions runner 가 매일 4회 cron 자동 실행.
    # 사장님 Mac 은 사이트 표시 + git pull 만. 외부 사이트 호출 0건.
    USE_LOCAL_CRON = os.environ.get("USE_LOCAL_CRON", "0") == "1"
    if USE_LOCAL_CRON:
        _daily_cron_loop()  # 매일 03·09·15·21시 자동 갱신
        _hourly_ig_cron_loop()  # IG 시간별 분산 수집
    else:
        print("[hookpilot] 사장님 Mac 외부 fetch 비활성화 — GitHub Actions 가 cron 담당")
        _git_pull_loop()  # 5분 마다 git pull → Actions 결과 자동 동기화
    # Cloud (Render·Fly.io) 호환 — $PORT 환경변수 + 0.0.0.0 listen.
    LISTEN_HOST = "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"
    # === 사장님 신뢰 보장 — main loop 영원 try/except + 자동 복구 ===
    # 어떤 exception 와도 process 안 죽음. launchd 재시작 의존 X.
    while True:
        try:
            with ThreadedTCPServer((LISTEN_HOST, PORT), HookpilotHandler) as httpd:
                try:
                    httpd.serve_forever()
                except KeyboardInterrupt:
                    print("\n[hookpilot] 종료 (Ctrl-C)")
                    return
                except Exception as e:
                    import traceback
                    sys.stderr.write(f"\n[hookpilot CRASH] {e}\n{traceback.format_exc()}\n")
                    sys.stderr.flush()
                    # 5초 대기 후 재시작
                    import time as _t
                    _t.sleep(5)
                    continue
        except Exception as outer:
            import traceback as _tb
            sys.stderr.write(f"\n[hookpilot OUTER-CRASH] {outer}\n{_tb.format_exc()}\n")
            sys.stderr.flush()
            import time as _t
            _t.sleep(10)
            continue


if __name__ == "__main__":
    # 사장님 신뢰 보장 — 어떤 fatal 에러도 process 안 죽음 (launchd 재시작 안 필요)
    while True:
        try:
            main()
            break  # KeyboardInterrupt 시 정상 종료
        except SystemExit:
            break
        except Exception as e:
            import traceback, time, sys
            sys.stderr.write(f"\n[hookpilot FATAL] {e}\n{traceback.format_exc()}\n")
            sys.stderr.flush()
            time.sleep(15)  # 15초 후 재시작
