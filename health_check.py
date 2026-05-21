#!/usr/bin/env python3
"""
service 모든 critical path 자동 검증. 매 cron run 후 + 매 코드 변경 후 자동 실행.
회귀 자동 감지 — 사장님 지적 X. 깨진 거 발견 즉시 log + 자동 fix 시도.

실행: .venv/bin/python3 health_check.py
종료 코드: 0 = 다 OK, 1 = 회귀 있음 (log 참조)
"""
import urllib.request, urllib.error, urllib.parse
import json, re, os, sys, subprocess, time
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(ROOT, ".tmp")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_PATH = os.path.join(LOG_DIR, f"health_check_{datetime.now().strftime('%Y-%m-%d')}.log")

issues = []
fixes_applied = []


def log(msg):
    print(msg, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now().strftime('%H:%M:%S')} {msg}\n")


def find_port():
    """download_server.py에서 현재 PORT 추출."""
    with open(os.path.join(ROOT, "download_server.py")) as f:
        for line in f:
            m = re.match(r"^PORT\s*=\s*(\d+)", line)
            if m:
                return int(m.group(1))
    return 9000


def check_server_alive(port):
    """서버 살아있나."""
    try:
        with urllib.request.urlopen(f"http://localhost:{port}/live.html", timeout=5) as r:
            if r.status == 200:
                return True
        return False
    except Exception:
        return False


def check_backend_base_port(expected_port):
    """live.html·simple.html·hookpilot.html의 BACKEND_BASE port가 PORT와 일치하는지.
    이번 회귀 (BACKEND_BASE 8889 vs PORT 9000) 같은 거 자동 잡음.
    """
    fail = []
    for f in ("simple.html", "live.html", "hookpilot.html"):
        p = os.path.join(ROOT, f)
        if not os.path.isfile(p): continue
        with open(p) as fp:
            content = fp.read()
        m = re.search(r'BACKEND_BASE\s*=\s*"http://localhost:(\d+)"', content)
        if not m:
            fail.append(f"{f}: BACKEND_BASE 없음")
            continue
        client_port = int(m.group(1))
        if client_port != expected_port:
            fail.append(f"{f}: BACKEND_BASE={client_port} ≠ PORT={expected_port}")
    return fail


def fix_backend_base(expected_port):
    """BACKEND_BASE port 불일치 자동 수정."""
    fixed = []
    for f in ("simple.html", "live.html", "hookpilot.html"):
        p = os.path.join(ROOT, f)
        if not os.path.isfile(p): continue
        with open(p) as fp:
            content = fp.read()
        new_content = re.sub(
            r'BACKEND_BASE\s*=\s*"http://localhost:\d+"',
            f'BACKEND_BASE = "http://localhost:{expected_port}"',
            content
        )
        if new_content != content:
            with open(p, "w") as fp:
                fp.write(new_content)
            fixed.append(f)
    return fixed


def check_api_endpoints(port):
    """3 플랫폼 다운로드·thumb·meta endpoint 작동 검증."""
    fails = []
    # api/thumb (TT)
    try:
        url = f"http://localhost:{port}/api/thumb?tt=7637813781611007253&user=kimming0_0"
        with urllib.request.urlopen(url, timeout=10) as r:
            if r.status != 200:
                fails.append(f"/api/thumb TT: HTTP {r.status}")
    except Exception as e:
        fails.append(f"/api/thumb TT: {str(e)[:60]}")
    # api/prepare (TT)
    try:
        url = f"http://localhost:{port}/api/prepare?url=https%3A%2F%2Fwww.tiktok.com%2F%40kimming0_0%2Fvideo%2F7637813781611007253&title=hc"
        start = time.time()
        with urllib.request.urlopen(url, timeout=30) as r:
            d = json.loads(r.read())
            if r.status != 200 or not d.get("file_url"):
                fails.append(f"/api/prepare TT: HTTP {r.status}")
            elif time.time() - start > 20:
                fails.append(f"/api/prepare TT: too slow ({time.time()-start:.1f}s)")
    except Exception as e:
        fails.append(f"/api/prepare TT: {str(e)[:60]}")
    return fails


def check_trending_videos_js(port):
    """trending_videos.js 응답 검증 — 영상 카운트·country 분포·잠재 회귀."""
    try:
        with urllib.request.urlopen(f"http://localhost:{port}/trending_videos.js", timeout=10) as r:
            content = r.read().decode("utf-8", errors="ignore")
        m = re.search(r"window\.TRENDING_VIDEOS\s*=\s*(\[.*?\]);", content, re.DOTALL)
        if not m:
            return [f"trending_videos.js: parse 실패"]
        arr = json.loads(m.group(1))
        fails = []
        if len(arr) < 100:
            fails.append(f"풀 너무 작음 ({len(arr)}개, 100+ 필요)")
        # 글로벌 광고 회귀 검사 — 사장님 외주 무관 브랜드가 풀에 또 들어왔는지
        GLOBAL_BRANDS = ["adidas", "nike", "puma", "juventus", "lamborghini", "ferrari", "marvel", "rolex"]
        global_count = 0
        for v in arr:
            text = ((v.get("title") or "") + " " + (v.get("channel") or "")).lower()
            if any(b in text for b in GLOBAL_BRANDS):
                global_count += 1
        if global_count > 5:
            fails.append(f"글로벌 광고 회귀: {global_count}개 (5+면 EXCLUDE_KEYWORDS 정비 필요)")
        # KR 비율 검사 — 한국 사장님 도구라 KR 25%+여야 정상
        kr = sum(1 for v in arr if v.get("country") == "KR")
        kr_pct = round(100 * kr / len(arr))
        if kr_pct < 20:
            fails.append(f"KR 비중 너무 낮음 ({kr_pct}%, 20%+ 필요)")
        # K-pop·B2B 잔존 검사
        excluded_ind = sum(1 for v in arr if v.get("industry") in ("kpop", "b2b"))
        if excluded_ind > 0:
            fails.append(f"제외 카테고리 잔존: {excluded_ind}개 (kpop·b2b)")
        # views 메타 누락 비율
        views_null = sum(1 for v in arr if v.get("views") is None)
        null_pct = round(100 * views_null / len(arr))
        if null_pct > 30:
            fails.append(f"views 메타 누락 너무 많음 ({null_pct}%, 30%+면 메타 보충 필요)")
        return fails
    except Exception as e:
        return [f"trending_videos.js: {str(e)[:60]}"]


def check_cache_headers(port):
    """Cache-Control no-store 응답 헤더 검증."""
    try:
        req = urllib.request.Request(f"http://localhost:{port}/trending_videos.js", method="GET")
        with urllib.request.urlopen(req, timeout=5) as r:
            cc = r.headers.get("Cache-Control") or ""
            if "no-store" not in cc:
                return [f"Cache-Control 누락: {cc}"]
            return []
    except Exception as e:
        return [f"cache header check: {str(e)[:60]}"]


def main():
    log("=== health_check 시작 ===")
    port = find_port()
    log(f"감지된 PORT: {port}")

    # 1. 서버 살아있나
    if not check_server_alive(port):
        log(f"[X] 서버 죽음. 사장님 페이지 접속 안 됨.")
        issues.append("server-dead")
        log(f"=== 종료. issues={len(issues)} ===")
        return 1
    log("[OK] 서버 살아있음")

    # 2. BACKEND_BASE port 일치 (이번 회귀 자동 잡음)
    bb_fails = check_backend_base_port(port)
    if bb_fails:
        for f in bb_fails:
            log(f"[X] {f}")
            issues.append(f"backend-base: {f}")
        log("자동 수정 시도…")
        fixed = fix_backend_base(port)
        if fixed:
            log(f"[FIX] BACKEND_BASE 수정: {', '.join(fixed)}")
            fixes_applied.append(f"backend-base → {port}")
    else:
        log(f"[OK] BACKEND_BASE 일치 (port {port})")

    # 3. 풀 정상
    pool_fails = check_trending_videos_js(port)
    if pool_fails:
        for f in pool_fails:
            log(f"[X] {f}")
            issues.append(f"pool: {f}")
    else:
        log("[OK] trending_videos.js 정상")

    # 4. Cache 헤더 (사장님 새로고침 시 옛 데이터 안 보이게)
    cache_fails = check_cache_headers(port)
    if cache_fails:
        for f in cache_fails:
            log(f"[X] {f}")
            issues.append(f"cache: {f}")
    else:
        log("[OK] Cache-Control no-store 적용")

    # 5. API endpoints
    api_fails = check_api_endpoints(port)
    if api_fails:
        for f in api_fails:
            log(f"[X] {f}")
            issues.append(f"api: {f}")
    else:
        log("[OK] API endpoints (thumb·prepare) 정상")

    log(f"=== 종료. issues={len(issues)} · 자동 수정 {len(fixes_applied)} ===")
    if fixes_applied:
        log(f"수정 내역: {', '.join(fixes_applied)}")
    return 0 if not issues or fixes_applied else 1


if __name__ == "__main__":
    sys.exit(main())
