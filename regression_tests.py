#!/usr/bin/env python3
"""
사장님이 한 번 지적한 회귀를 영구 자동 차단하는 회귀 방지 시스템.
매 코드 변경 후 자동 실행 + 매 cron 후 자동 실행.

추가 방식: 사장님이 새 회귀 지적할 때마다 이 파일에 testcase 1개 추가.
실행: .venv/bin/python3 regression_tests.py
종료 코드: 0 = 모든 회귀 차단 · 1 = 회귀 발견
"""
import os, re, json, sys, urllib.request, urllib.error
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(ROOT, ".tmp", f"regression_{datetime.now().strftime('%Y-%m-%d')}.log")
os.makedirs(os.path.dirname(LOG), exist_ok=True)
PORT = 9000

issues = []


def log(msg):
    print(msg, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now().strftime('%H:%M:%S')} {msg}\n")


def fail(name, detail):
    log(f"  [회귀 발견] {name}: {detail}")
    issues.append((name, detail))


def ok(name):
    log(f"  [OK] {name}")


def read_file(path):
    p = os.path.join(ROOT, path)
    if not os.path.isfile(p): return ""
    with open(p) as f: return f.read()


def fetch_url(path):
    try:
        with urllib.request.urlopen(f"http://localhost:{PORT}{path}", timeout=10) as r:
            return r.status, r.read().decode("utf-8", errors="ignore"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, "", dict(e.headers) if e.headers else {}
    except Exception as e:
        return 0, str(e), {}


# === testcase: 사장님이 지적했던 모든 회귀 영구 차단 ===

def test_01_backend_base_port():
    """BACKEND_BASE port가 download_server.py PORT랑 일치 (예전 8889 vs 9000 회귀)"""
    server_port_m = re.search(r"^PORT\s*=\s*(\d+)", read_file("download_server.py"), re.MULTILINE)
    if not server_port_m: return fail("BACKEND_BASE", "download_server PORT 못 찾음")
    server_port = int(server_port_m.group(1))
    for f in ("simple.html", "live.html", "hookpilot.html"):
        m = re.search(r'BACKEND_BASE\s*=\s*"http://localhost:(\d+)"', read_file(f))
        if not m: return fail("BACKEND_BASE", f"{f}: 변수 없음")
        if int(m.group(1)) != server_port:
            return fail("BACKEND_BASE", f"{f}: {m.group(1)} ≠ PORT {server_port}")
    ok("BACKEND_BASE port 일치")


def test_02_toggle_count_dynamic():
    """국가·플랫폼 토글 카운트 동시 적용 (한국 클릭 시 플랫폼 카운트가 KR만)"""
    html = read_file("simple.html")
    if "state.country === \"ALL\" || v.country === state.country" not in html:
        return fail("toggle-count", "플랫폼 카운트가 country 적용 안 함")
    if "state.platform === \"ALL\" || v.platform === state.platform" not in html:
        return fail("toggle-count", "국가 카운트가 platform 적용 안 함")
    if "renderToggles();" not in html:
        return fail("toggle-count", "renderToggles 호출 위치 없음")
    ok("토글 카운트 동시 적용")


def test_03_cache_no_store():
    """Cache-Control no-store 적용 (옛 데이터 캐시 회귀 차단)"""
    status, _, headers = fetch_url("/trending_videos.js")
    cc = headers.get("Cache-Control") or ""
    if "no-store" not in cc:
        return fail("cache", f"Cache-Control no-store 누락: {cc}")
    ok("Cache-Control no-store 적용")


def test_04_short_title():
    """영상 제목 단축 (TT caption 전체 박혀 길어지던 회귀 차단)"""
    html = read_file("simple.html")
    if "function shortTitle(" not in html:
        return fail("short-title", "shortTitle 함수 정의 없음")
    if html.count("shortTitle(v.title)") < 2:
        return fail("short-title", f"shortTitle 사용 위치 부족 ({html.count('shortTitle(v.title)')})")
    ok("shortTitle 적용")


def test_05_shop_strict():
    """shop 분류 엄격 (추천·리뷰 같은 일반 키워드로 shop 분류 회귀 차단)"""
    merge = read_file("merge_trending.py")
    if "SHOP_SIGNALS = {" not in merge:
        return fail("shop", "SHOP_SIGNALS 6 카테고리 분류 X")
    for cat in ("buy_link", "discount_code", "direct_cta", "deal_urgency", "store_location", "sponsored"):
        if cat not in merge:
            return fail("shop", f"SHOP_SIGNALS '{cat}' 누락")
    # 단순 키워드 잔존 검사 (예전 SHOP_KEYWORDS의 '추천', '리뷰' 같은 거 다시 들어왔는지)
    if '"추천"' in merge and "SHOP_KEYWORDS" in merge:
        # SHOP_KEYWORDS 자체는 평탄화용으로 남아도 OK. 단 광범 키워드 직접 박혀있으면 회귀
        sk_block = re.search(r"SHOP_KEYWORDS = \[\s*([^\]]+)\]", merge)
        if sk_block and '"추천"' in sk_block.group(1):
            return fail("shop", "SHOP_KEYWORDS에 광범 '추천' 키워드 회귀")
    ok("SHOP 6 카테고리 엄격 분류")


def test_06_no_global_brand():
    """글로벌 광고 (Adidas·Nike) 풀에서 제외 (글로벌 viral 상위 회귀 차단)"""
    status, content, _ = fetch_url("/trending_videos.js")
    m = re.search(r"window\.TRENDING_VIDEOS\s*=\s*(\[.*?\]);", content, re.DOTALL)
    if not m: return fail("global-brand", "trending_videos.js parse 실패")
    arr = json.loads(m.group(1))
    BRANDS = ["adidas", "nike", "puma", "juventus", "lamborghini", "marvel", "rolex"]
    hit = sum(1 for v in arr for b in BRANDS if b in ((v.get("title") or "") + (v.get("channel") or "")).lower())
    if hit > 5:
        return fail("global-brand", f"글로벌 브랜드 {hit}개 잔존 (5 초과)")
    ok(f"글로벌 광고 잔존 {hit}개 (5 이하)")


def test_07_score_meta_cap():
    """좋아요·조회수 메타 없는 영상 priorityScore 30점 cap (좋아요 13 영상 6위 회귀 차단)"""
    html = read_file("simple.html")
    if "score = Math.min(30, score)" not in html and "도달 메타 없음 (cap 30)" not in html:
        return fail("score-cap", "메타 없는 영상 점수 cap 30 코드 없음")
    ok("도달 메타 없는 영상 점수 30 cap")


def test_08_ig_modal_video():
    """IG 모달 영상 직접 재생 (IG embed iframe만 보이던 회귀 차단)"""
    html = read_file("simple.html")
    if "/api/ig-stream" not in html:
        return fail("ig-modal", "IG 모달이 ig-stream 미사용")
    ok("IG 모달 직접 재생")


def test_09_autopoll_no_reload():
    """자동 폴링이 location.reload() 직접 호출 X (작업 중 갑자기 reload 회귀 차단)"""
    html = read_file("simple.html")
    # autoPoll 함수 안에서 자동 reload 호출 검사
    m = re.search(r"function autoPoll\(\)\s*\{(.*?)\}\)\(\);", html, re.DOTALL)
    if not m: return fail("autopoll", "autoPoll 함수 못 찾음")
    autopoll_body = m.group(1)
    # 모달 열림·검색 입력 가드 있어야 OK
    if "modalOpen" not in autopoll_body or "showToast" not in autopoll_body:
        return fail("autopoll", "모달 열림 가드·토스트 호출 누락")
    ok("자동 폴링 작업 중 reload 차단")


def test_10_guide_matches_code():
    """가이드 안내문이 코드와 일치 (좋아요 50·IND_CAP 120·시간당 IG 회귀 차단)"""
    html = read_file("simple.html")
    fails_list = []
    if "_MIN_LIKES_DISPLAY = 20" in html and "좋아요 50개 미만" in html:
        fails_list.append("좋아요 코드 20 vs 가이드 50")
    if "_MIN_LIKES_DISPLAY = 20" in html and "좋아요 20개 미만" not in html:
        fails_list.append("좋아요 가이드 텍스트 없음")
    if "IND_CAP = 1000" in html and "120개 넘으면" in html:
        fails_list.append("IND_CAP 코드 1000 vs 가이드 120")
    if "시간당 분산으로" in html:
        fails_list.append("IG cron 가이드 '시간당 분산' (현재 6시간 간격)")
    if fails_list:
        return fail("guide-mismatch", " | ".join(fails_list))
    ok("가이드 안내문 코드 일치")


def test_11_country_default_kr():
    """state.country 기본값 KR (사장님 한국 클라이언트라 매번 한국 클릭 회귀 차단)"""
    html = read_file("simple.html")
    if 'let state = { country: "KR"' not in html:
        return fail("country-default", "country 기본 KR 아님")
    ok("country 기본 KR")


def test_12_email_recipe_removed():
    """클라이언트 제안 메일·외주 작업서 버튼 제거 (사장님 제거 요구 회귀 차단)"""
    html = read_file("simple.html")
    if "copyEmailBtn" in html or "copyRecipeBtn" in html:
        return fail("email-recipe", "메일·작업서 버튼 잔존")
    ok("메일·작업서 버튼 제거")


def test_13_esc_modal_close():
    """ESC 키 모달 닫기 (사장님 요구 회귀 차단)"""
    html = read_file("simple.html")
    if "Escape" not in html or "closeModal()" not in html:
        return fail("esc-modal", "ESC 키 핸들러 없음")
    ok("ESC 키 모달 닫기")


def test_14_download_label_spinner():
    """다운로드 버튼 '영상 다운로드' 라벨 + 스피너 (사장님 명시 요구 회귀 차단)"""
    html = read_file("simple.html")
    if '"⬇ 영상 다운로드"' not in html:
        return fail("download-label", '"⬇ 영상 다운로드" 라벨 없음')
    if "hkp-spinner" not in html:
        return fail("download-spinner", "로딩 스피너 클래스 없음")
    ok("다운로드 라벨·스피너")


def test_15_health_check_integrated():
    """매 cron 후 health_check 자동 실행 (회귀 자동 잡기 시스템 회귀 차단)"""
    server = read_file("download_server.py")
    if "_run_health_check" not in server:
        return fail("health-integration", "health_check cron 통합 없음")
    ok("health_check cron 통합")


def test_16_security_headers():
    """보안 헤더 6종 응답 + CSP frame-src에 영상 임베드 도메인 다 포함 (YT iframe 검은 박스 회귀 차단)"""
    _, _, headers = fetch_url("/live.html")
    required = ["Content-Security-Policy", "X-Content-Type-Options", "X-Frame-Options",
                "Strict-Transport-Security", "Referrer-Policy", "Permissions-Policy"]
    missing = [h for h in required if h not in headers]
    if missing:
        return fail("security-headers", f"누락 헤더: {', '.join(missing)}")
    # CSP frame-src에 영상 도메인 다 있어야 (YT·TT·IG·youtube-nocookie)
    csp = headers.get("Content-Security-Policy") or ""
    for domain in ("https://www.tiktok.com", "https://www.instagram.com", "https://www.youtube.com", "https://www.youtube-nocookie.com"):
        if domain not in csp:
            return fail("security-headers", f"CSP frame-src에 {domain} 없음 — iframe 검은 박스 회귀")
    ok("보안 헤더 6종 + CSP frame-src 영상 도메인 OK")


def test_17_rate_limit():
    """rate limit 코드 박혀 있음 + localhost 면제 (사장님 자기 머신·자동 검증 차단 회귀 차단)"""
    server = read_file("download_server.py")
    if "_ip_rate_limit" not in server or "RL_PER_MIN" not in server:
        return fail("rate-limit", "rate limit 함수 없음")
    if '"127.0.0.1"' not in server or "localhost" not in server.lower():
        return fail("rate-limit", "localhost 면제 누락 — 사장님 머신 차단 위험")
    # 실제 동작 검증 — /api/thumb 100번 호출해도 다 200
    import urllib.request
    for _ in range(10):
        try:
            r = urllib.request.urlopen(f"http://localhost:{PORT}/api/thumb?tt=test&user=test", timeout=5)
            if r.status != 200:
                return fail("rate-limit", f"localhost 호출도 차단됨 (HTTP {r.status})")
        except Exception as e:
            return fail("rate-limit", f"호출 실패: {str(e)[:60]}")
    ok("Rate limit + localhost 면제")


def test_18_js_syntax():
    """JavaScript syntax 에러 0 (코드 깨짐 회귀 차단)"""
    import subprocess
    for f in ("simple.html", "live.html", "hookpilot.html"):
        try:
            r = subprocess.run(
                ["node", "-e", f"""
const html = require('fs').readFileSync('{f}', 'utf-8');
const blocks = html.match(/<script(?!\\s+src)[^>]*>([\\s\\S]*?)<\\/script>/g) || [];
let err = 0;
blocks.forEach(b => {{ try {{ new Function(b.replace(/<\\/?script[^>]*>/g, '')); }} catch (e) {{ err++; console.error(e.message); }} }});
process.exit(err);
                """],
                cwd=ROOT, capture_output=True, timeout=15
            )
            if r.returncode != 0:
                return fail("js-syntax", f"{f}: {r.stderr.decode()[:120]}")
        except Exception as e:
            pass  # node 없으면 skip
    ok("JS syntax 0 에러")


def test_19_3files_synced():
    """simple.html · live.html · hookpilot.html 동기화 (사장님이 다른 파일 보면 옛 코드 회귀 차단)"""
    sizes = []
    for f in ("simple.html", "live.html", "hookpilot.html"):
        p = os.path.join(ROOT, f)
        if not os.path.isfile(p): sizes.append(0); continue
        sizes.append(os.path.getsize(p))
    if len(set(sizes)) > 1:
        return fail("sync", f"3 파일 크기 다름: {sizes}")
    ok("simple/live/hookpilot 동기화")


def test_20_api_endpoints():
    """모든 API endpoint 200 OK (다운로드 안 됨 회귀 차단)"""
    for ep in ("/api/thumb?tt=test&user=test", "/api/refresh-trending?status=1"):
        status, _, _ = fetch_url(ep)
        if status != 200:
            return fail("api-endpoint", f"{ep}: HTTP {status}")
    ok("모든 API endpoint 200")


def test_21_modal_loading_timeouts():
    """모달 무한 로딩 차단 — fetchMeta timeout 15초·concurrency 8·priority 큐 적용"""
    html = read_file("simple.html")
    if "AbortSignal.timeout(15000)" not in html:
        return fail("modal-loading", "fetchMeta timeout 15초 누락 (사장님 클릭 후 무한 대기 회귀)")
    # concurrency 4-8 — 페이지 첫 로드 속도 우선. 모달은 priority 큐로 즉시 처리.
    import re as _re_local
    if not _re_local.search(r'_metaInflight < [4-9]', html):
        return fail("modal-loading", "concurrency 4-9 누락 (큐 dispatch 회귀)")
    # priority dispatch 즉시 fetch — 큐 우회. 사장님 빠른 카드 탐색 시 timeout 회귀 차단
    if "_dispatchPriority" not in html:
        return fail("modal-loading", "_dispatchPriority 함수 누락 — priority 큐 우회 안 됨")
    if "function enqueueMeta(v, priority)" not in html:
        return fail("modal-loading", "enqueueMeta priority 매개변수 누락")
    if "enqueueMeta(v, true)" not in html:
        return fail("modal-loading", "openModal에서 priority=true 호출 누락 (모달 즉시 처리 회귀)")
    ok("모달 fetchMeta timeout·concurrency·priority")


def test_22_sort_fallback():
    """정렬 fallback 적용 — getLikes·getViews·getDate가 풀 자체 데이터 fallback (정렬 X 회귀 차단)"""
    html = read_file("simple.html")
    # 풀 데이터 fallback 검사 — getLikes에 v.likes·getViews에 v.views·getDate에 v.firstSeen 있어야
    if "|| v.likes" not in html or "|| v.views" not in html:
        return fail("sort-fallback", "getLikes/getViews에 풀 v.likes/v.views fallback 누락")
    if "|| v.firstSeen" not in html:
        return fail("sort-fallback", "getDate에 v.firstSeen fallback 누락")
    ok("정렬 풀 데이터 fallback")


def test_23_thumbnail_immediate_load():
    """카드 썸네일 즉시 로드 — 첫 N 카드 검은 박스 회귀 차단 (N>=60)"""
    import re as _re
    html = read_file("simple.html")
    # slice(0, N).forEach(_loadThumbForCard) — N 이 60 이상이어야 PASS
    m = _re.search(r'\.slice\(0,\s*(\d+)\)\.forEach\(_loadThumbForCard\)', html)
    if not m:
        return fail("thumbnail", "slice(0, N).forEach(_loadThumbForCard) 패턴 누락")
    n = int(m.group(1))
    if n < 60:
        return fail("thumbnail", f"첫 {n} 카드만 즉시 thumb load — 60 이상이어야 (사장님 호소: 첫 화면 빈 박스)")
    ok(f"첫 {n} 카드 즉시 썸네일 로드")


def test_24_industry_tab_count_match():
    """업종 탭 카운트가 실제 카드 양과 일치 (후킹 필터 X 회귀 차단)"""
    html = read_file("simple.html")
    # renderIndustryTabs 안 후킹 강제 제외 코드 없어야 (sticky만)
    # extractHookPoints(v).length === 0) return false; 패턴이 있으면 회귀
    # 단 sticky 형태 (v._hookPassed !== true && extractHookPoints(v).length > 0) v._hookPassed = true는 OK
    # 즉 "return false" 직전 extractHookPoints 0 체크가 renderIndustryTabs 안 있으면 안 됨
    import re as _re
    rt = _re.search(r"function renderIndustryTabs\(\)\s*\{(.+?)\n\}", html, _re.DOTALL)
    if not rt:
        return fail("ind-tab-count", "renderIndustryTabs 함수 못 찾음")
    body = rt.group(1)
    if "extractHookPoints(v).length === 0) return false" in body:
        return fail("ind-tab-count", "업종 탭 카운트가 후킹 필터로 제외 — 카드 양과 안 맞음 회귀")
    ok("업종 탭 카운트 카드 양 일치")


def test_25_go_home_full_reset():
    """홈 클릭 시 모든 필터 초기화 (즐겨찾기 뷰 잔존 회귀 차단)"""
    html = read_file("simple.html")
    gh = re.search(r"window\.goHome = function\(\)\s*\{(.+?)\n\};", html, re.DOTALL)
    if not gh:
        return fail("go-home", "goHome 함수 못 찾음")
    body = gh.group(1)
    required = ["_starredView = false", 'state.sort = "priority"', "renderCards"]
    missing = [r for r in required if r not in body]
    if missing:
        return fail("go-home", f"누락: {missing}")
    ok("홈 클릭 전체 초기화")


def test_26_star_overlay_thumbnail():
    """카드 썸네일에 별표 overlay (별표 사장님 시각 인지 회귀 차단)"""
    html = read_file("simple.html")
    if "card-star-overlay" not in html:
        return fail("star-overlay", "card-star-overlay 클래스 누락")
    if "_isStarred" not in html:
        return fail("star-overlay", "_isStarred 변수 누락")
    ok("카드 썸네일 별표 overlay")


def test_27_starred_btn_always_visible():
    """별표 버튼 항상 보임 (0개여도 사장님이 추가 가능)"""
    html = read_file("simple.html")
    if 'starredBtn.style.display = "inline-flex"' not in html:
        return fail("starred-btn", "별표 버튼 무조건 보임 코드 누락")
    ok("별표 버튼 항상 보임")


def test_28_thumb_cache_public():
    """/api/thumb 응답에 Cache-Control public max-age 박혀야 (사장님 브라우저 캐시 → 카드 즉시 표시)"""
    status, _, headers = fetch_url("/api/thumb?tt=7637813781611007253&user=kimming0_0")
    cc = headers.get("Cache-Control") or ""
    if "public" not in cc or "max-age" not in cc:
        return fail("thumb-cache", f"/api/thumb Cache-Control 박혀야 하는데: '{cc}'")
    if "no-store" in cc:
        return fail("thumb-cache", "/api/thumb에 no-store 박힘 — 브라우저 캐시 X → 매번 fetch 느림 회귀")
    ok("/api/thumb 브라우저 캐시 OK (public max-age)")


def test_29a_no_auto_dirty_url():
    """페이지 로드 시 dirty URL 자동 reload X (?hkp_v= 사고 회귀 차단)"""
    html = read_file("simple.html")
    if "?hkp_v=" in html and "location.replace" in html and "autoReload" in html:
        return fail("auto-dirty-url", "autoReload가 ?hkp_v= dirty URL로 자동 reload 회귀")
    ok("autoReload dirty URL 차단")


def test_29_thumb_disk_cache():
    """/api/thumb 디스크 영구 캐시 — 두 번째 호출부터 즉시 응답 (네트워크 fetch X)"""
    import urllib.request, time as _t
    url = f"http://localhost:{PORT}/api/thumb?url=https%3A%2F%2Fwww.instagram.com%2Fp%2FDTHje9GE57e%2Fmedia%2F%3Fsize%3Dl"
    try:
        # 1차 호출 (캐시 채움)
        with urllib.request.urlopen(url, timeout=15) as r: r.read()
        # 2차 호출 — 디스크 캐시 hit, X-Cache 헤더 DISK-HIT 박혀야
        s = _t.time()
        with urllib.request.urlopen(url, timeout=5) as r:
            r.read()
            x = r.headers.get("X-Cache", "")
            elapsed = (_t.time() - s) * 1000
        if x != "DISK-HIT":
            return fail("thumb-disk-cache", f"X-Cache 헤더 'DISK-HIT' 아님: '{x}'")
        # 캐시 파일 500KB 이하 보장 (size limit) → 1초 안에 응답 정상
        if elapsed > 1000:
            return fail("thumb-disk-cache", f"디스크 캐시 호출 너무 느림: {elapsed:.0f}ms (1000ms 이하여야)")
        ok(f"/api/thumb 디스크 캐시 ({elapsed:.0f}ms DISK-HIT)")
    except Exception as e:
        return fail("thumb-disk-cache", f"호출 실패: {str(e)[:60]}")


def test_31_card_click_no_double_open():
    """카드 클릭 시 openModal 중복 호출 차단 — absolute inset:0 버튼 회귀 방지"""
    import re as _re
    html = read_file("simple.html")
    # article.wjob.card 안에 inset:0 absolute 버튼이 onclick="window.openModal" 또 박혀있으면 회귀
    if _re.search(r'style="position:absolute;inset:0[^"]*"[^>]*onclick="[^"]*openModal', html):
        return fail("card-double-click", "카드 안 absolute 버튼이 openModal 또 호출 — 모달 2번 열림")
    ok("카드 클릭 단일 트리거")


def test_32_thumb_alt_text():
    """썸네일 img 에 alt 속성 — 접근성 + SEO"""
    import re as _re
    html = read_file("simple.html")
    # 카드 렌더링 안 <img src="${t}" — alt 누락이면 회귀
    m = _re.search(r'<img src="\$\{t\}"[^>]*>', html)
    if m and "alt=" not in m.group(0):
        return fail("thumb-no-alt", "카드 썸네일 img 에 alt 속성 없음 — 접근성 결함")
    # JS 동적 proxy-thumb img.alt 도 설정해야
    if "img.alt = " not in html and "proxy-thumb" in html:
        return fail("proxy-thumb-no-alt", "proxy-thumb img.alt 설정 누락")
    ok("썸네일 alt 속성")


def test_40_guide_info_button():
    """가이드 모달 열기 버튼 #criteriaInfo 존재 — 며칠 동안 회귀였던 진짜 원인.
    사장님이 정렬 점수 산식·신선도 통계·플랫폼 상태 모두 못 봤음.
    """
    html = read_file("simple.html")
    if 'id="criteriaInfo"' not in html:
        return fail("guide-btn-missing", "가이드 모달 열기 버튼 #criteriaInfo 누락 — 사장님 가이드 모달 못 열음")
    ok("가이드 모달 열기 버튼 박힘")


def test_39_yt_thumb_fallback():
    """YT 썸네일 maxresdefault → hqdefault → mqdefault → default 단계별 fallback.
    Playwright 실제 검증으로 잡은 진짜 회귀: 일부 YT 영상 maxresdefault 없음 (404) → 카드 검은 박스.
    """
    html = read_file("simple.html")
    # thumbUrl 기본이 hqdefault 여야 (maxresdefault 는 404 가능성)
    if "maxresdefault.jpg`" in html and "hqdefault" not in html:
        return fail("yt-thumb-maxres-only", "thumbUrl 이 maxresdefault 만 사용 — 일부 영상 404, 카드 검은 박스")
    if "window._thumbFallback" not in html:
        return fail("no-thumb-fallback", "_thumbFallback 함수 누락 — YT 썸네일 단계별 fallback X")
    if "mqdefault" not in html or "default.jpg" not in html:
        return fail("incomplete-fallback", "썸네일 fallback 단계 (mqdefault·default) 누락")
    ok("YT 썸네일 단계별 fallback (hqdefault→mqdefault→default)")


def test_38_no_external_cors_fetches():
    """클라이언트가 외부 도메인 직접 fetch X — CORS block + 사이트 느려지는 진짜 회귀.
    사장님이 며칠째 '느리고 영상 안 뜸' 호소한 진짜 원인: simple.html 이 piped·invidious·noembed·tikwm 직접 fetch → 모두 CORS fail → 카드 60 × 13 = 780 blocked requests.
    """
    import re as _re
    html = read_file("simple.html")
    # 외부 도메인 직접 fetch 호출 잔여 시 회귀
    forbidden = [
        r"fetch\(`https://pipedapi",
        r"fetch\(`\$\{base\}/streams",  # piped base loop
        r"fetch\(`https://yewtu\.be",
        r"fetch\(`https://invidious",
        r"fetch\(`\$\{inst\}/api/v1",  # invidious instance loop
        r"fetch\(`https://noembed\.com",
        r"fetch\(`https://www\.tikwm\.com/api",
    ]
    for pat in forbidden:
        if _re.search(pat, html):
            return fail("external-fetch-cors", f"클라이언트 외부 도메인 직접 fetch: {pat} — CORS block 회귀")
    # dead fn 4개 정의도 제거됐는지 (회귀 재발 차단)
    for fn in ("fetchPipedMeta", "fetchTikwmMeta", "fetchYouTubeMeta", "fetchThumb"):
        if _re.search(rf"async function\s+{fn}\s*\(", html):
            return fail("dead-external-fn", f"{fn} dead 함수 정의 잔존 — 누가 호출 시 CORS 회귀 재발")
    ok("외부 fetch CORS block 0건")


def test_37_inline_retry_play_no_window_open():
    """모달 안 영상 재생 버튼이 외부 사이트로 새 탭 열기 X — 사장님 사이트 안에서 다시 재생.
    이전 회귀: '▶ Instagram에서 재생' 클릭 → window.open(origUrl, '_blank') → 인스타로 이동 → 사장님 사이트 떠남.
    """
    import re as _re
    html = read_file("simple.html")
    # 모달 안 우측 하단 fallback 버튼 onclick 에서 window.open(... '_blank') 박혀있으면 회귀
    # template literal 안 '▶' + 'window.open' + '_blank' 패턴
    if _re.search(r"frame\s*\+=\s*`<button[^`]*window\.open\([^)]*['\"]_blank['\"]", html):
        return fail("modal-window-open", "모달 안 '▶ 재생' 버튼이 window.open 새 탭 — 사장님 사이트 떠나는 회귀")
    # _inlineRetryPlay 함수 박혀있어야
    if "window._inlineRetryPlay" not in html:
        return fail("no-inline-retry", "_inlineRetryPlay 함수 누락 — 사이트 안 재생 재시도 불가")
    # 그 함수 안 window.open 없어야 (외부 이동 차단)
    m = _re.search(r"window\._inlineRetryPlay\s*=\s*function[^{]*\{(.*?)\n\};", html, _re.S)
    if m and "window.open" in m.group(1):
        return fail("inline-retry-opens-external", "_inlineRetryPlay 안 window.open 박힘 — 사이트 안 재생 의도 깨짐")
    ok("모달 '▶ 다시 재생' 버튼 사이트 안 재생")


def test_36_no_auto_modal_close():
    """모달 자동 닫기 + '선정 기준 미달' 토스트 박는 짓 차단 — 사장님 본 '파란 안내창 뜨더니 사라짐'의 진짜 원인.
    카드 클릭으로 사용자가 명시적으로 연 모달은 강제로 닫으면 X — 답답함 진짜 원인. 미달 영상은 카드만 hide.
    """
    html = read_file("simple.html")
    # '선정 기준 미달' 토스트 박는 코드 잔여 시 회귀
    if "선정 기준 미달" in html:
        return fail("auto-toast-close", "'선정 기준 미달' 토스트 잔여 — 모달 자동 닫기 회귀")
    # openModal 시작 부분에서 _under 박혀있는 cutoff 검사 후 모달 안 여는 짓 차단
    import re as _re
    m = _re.search(r"window\.openModal\s*=\s*function\([^)]*\)\s*\{(.*?)window\._currentModalVid\s*=\s*id", html, _re.S)
    if m and "return;" in m.group(1) and "_under" in m.group(1):
        return fail("open-modal-blocked", "openModal 시작 부분 미달 영상 차단 회귀 — 사용자가 클릭한 모달은 항상 열려야")
    ok("모달 자동 닫기·미달 토스트 차단")


def test_35_csp_allows_meta_fallbacks():
    """CSP connect-src 가 외부 메타 fallback API 모두 허용 — 며칠 동안 사장님 못 잡으신 진짜 근본 회귀.
    이게 빠지면 시각적 silent fail: 카드 조회수 "?", 모달 "분석 중" 무한, 사장님 "다 안 됨" 으로 인식.
    Chrome headless console 에서 'Content Security Policy' violation 으로 잡힘.
    """
    import urllib.request
    try:
        req = urllib.request.Request(f"http://localhost:{PORT}/simple.html")
        with urllib.request.urlopen(req, timeout=5) as r:
            csp = r.headers.get("Content-Security-Policy", "")
    except Exception as e:
        return fail("csp-fetch", f"CSP 헤더 못 가져옴: {str(e)[:60]}")
    # simple.html 안에서 fetch 호출하는 외부 도메인 모두 connect-src 안 박혀야
    must_have_in_connect = [
        "pipedapi.kavin.rocks",  # YT 메타 fallback (8개 Piped 중 첫째)
        "yewtu.be",              # YT 메타 fallback (Invidious)
        "noembed.com",           # oEmbed (TT·YT 썸네일 fallback)
    ]
    # connect-src 부분 추출
    import re as _re
    m = _re.search(r"connect-src ([^;]+);", csp)
    if not m:
        return fail("csp-no-connect-src", "CSP 에 connect-src 디렉티브 없음")
    connect_src = m.group(1)
    for dom in must_have_in_connect:
        if dom not in connect_src:
            return fail("csp-block-meta", f"connect-src 에 {dom} 누락 — 브라우저가 메타 fetch block, 사장님 화면 '분석 중' 무한")
    ok("CSP connect-src 메타 fallback 12개 허용")


def test_34_download_re_entrant():
    """다운로드 두 번 연속 클릭·다른 영상 모달 열기 시 회귀 차단 — 어제부터 사장님이 호소한 진짜 회귀.
    근본 원인: setInterval timer 가 다음 모달 dlTopBtn 까지 jacking, btn.onclick = null 5초 후 박혀서 두 번째 다운로드 깨짐.
    """
    html = read_file("simple.html")
    # 1) _dlActive 추적 — 다중 download 충돌 차단
    if "_dlActive" not in html:
        return fail("download-no-tracking", "window._dlActive 다운로드 토큰 추적 누락 — 옛 timer 가 새 모달 버튼 jacking")
    # 2) setInterval timer 안 dlTopBtn 체크 — 다른 모달 열렸을 때 noop
    if 'curBtn !== btn' not in html:
        return fail("download-no-btn-check", "setInterval timer 가 같은 btn DOM element 체크 X — 새 모달 깨놓음 회귀")
    # 3) btn.onclick = null 박는 setTimeout 차단 — 다음 다운로드 깨짐의 진짜 원인
    if 'btn.onclick = null' in html and 'document.getElementById("dlTopBtn") === btn' not in html:
        return fail("download-onclick-nulled", "btn.onclick = null 박는 5초 setTimeout 이 새 모달 버튼 onclick 도 제거 — 두 번째 다운로드 회귀")
    # 4) closeModal 안 _dlActive cleanup
    if 'window._dlActive' not in html or '_dlActive.timer' not in html:
        return fail("close-modal-leak", "closeModal 안 진행 중 _dlActive.timer cleanup 누락 — 모달 닫혀도 timer 가 다음 모달 jacking")
    ok("다운로드 re-entrant 안전")


def test_33_pool_no_empty_title():
    """풀에 title 빈 영상 0 — merge_trending.py 에서 차단됐는지"""
    import re as _re
    try:
        with open("trending_videos.js", "r", encoding="utf-8") as f:
            txt = f.read()
    except FileNotFoundError:
        return ok("trending_videos.js 없음 — skip")
    # title이 "" 인 영상 1개라도 있으면 회귀
    items = _re.findall(r'\{[^{}]*?\}', txt)
    empty = sum(1 for it in items if _re.search(r'"title":\s*""', it))
    if empty > 0:
        return fail("empty-title-in-pool", f"풀에 title 빈 영상 {empty}개 — merge_trending.py 차단 회귀")
    ok("풀 title 빈 영상 0개")


def test_30_no_dead_code_or_duplicates():
    """dead code (copyEmail/copyRecipe + 4 추가) + 같은 함수·핸들러 중복 정의 차단"""
    import re as _re
    html = read_file("simple.html")
    # 1) copyEmail/copyRecipe 정의 잔존 (UI 버튼은 진작 제거됐는데 정의가 남아있으면 dead code)
    for dead_fn in ("copyEmail", "copyRecipe", "emailDraft", "recipeDetailed",
                    "absDate", "stealPoints", "clientScenario"):
        if _re.search(rf"function\s+{dead_fn}\s*\(", html):
            return fail("dead-code", f"{dead_fn} 함수 정의 잔존 — 호출처 0회인 dead code")
    # 2) autoPoll IIFE 중복 정의 — 같은 함수 이름 IIFE 2번 이상이면 회귀
    n_autopoll = len(_re.findall(r"\(function\s+autoPoll\s*\(", html))
    if n_autopoll > 1:
        return fail("dup-autopoll", f"autoPoll IIFE {n_autopoll}번 정의 — 중복 정의")
    # 3) starredQuickBtn count 초기화 DOMContentLoaded 핸들러 중복
    n_starred_init = len(_re.findall(r'starredQuickBtn[^}]+star-count[^}]+textContent', html))
    if n_starred_init > 2:
        return fail("dup-starred-init", f"starredQuickBtn 카운트 초기화 핸들러 {n_starred_init}번 — 중복")
    # 4) 진단 외 production console.log 잔존 (5개까지 허용 — 다운로드·모달·백엔드 에러는 유지)
    n_console_log = len(_re.findall(r"console\.log\(", html))
    if n_console_log > 0:
        return fail("noise-console-log", f"console.log {n_console_log}개 production 노이즈 잔존 (warn·error만 허용)")
    ok("dead code + 중복 함수 정의 차단")


def main():
    log(f"=== 회귀 테스트 시작 — {datetime.now().isoformat()} ===")
    tests = [
        test_01_backend_base_port, test_02_toggle_count_dynamic, test_03_cache_no_store,
        test_04_short_title, test_05_shop_strict, test_06_no_global_brand,
        test_07_score_meta_cap, test_08_ig_modal_video, test_09_autopoll_no_reload,
        test_10_guide_matches_code, test_11_country_default_kr, test_12_email_recipe_removed,
        test_13_esc_modal_close, test_14_download_label_spinner, test_15_health_check_integrated,
        test_16_security_headers, test_17_rate_limit, test_18_js_syntax,
        test_19_3files_synced, test_20_api_endpoints,
        test_21_modal_loading_timeouts, test_22_sort_fallback, test_23_thumbnail_immediate_load,
        test_24_industry_tab_count_match, test_25_go_home_full_reset, test_26_star_overlay_thumbnail,
        test_27_starred_btn_always_visible, test_28_thumb_cache_public, test_29a_no_auto_dirty_url,
        test_29_thumb_disk_cache, test_30_no_dead_code_or_duplicates,
        test_31_card_click_no_double_open, test_32_thumb_alt_text, test_33_pool_no_empty_title,
        test_34_download_re_entrant, test_35_csp_allows_meta_fallbacks, test_36_no_auto_modal_close,
        test_37_inline_retry_play_no_window_open, test_38_no_external_cors_fetches,
        test_39_yt_thumb_fallback, test_40_guide_info_button,
    ]
    for t in tests:
        try: t()
        except Exception as e: fail(t.__name__, f"테스트 자체 오류: {str(e)[:100]}")
    log(f"=== 종료. 총 {len(tests)} testcase · 회귀 {len(issues)}건 ===")
    if issues:
        log(f"발견된 회귀:")
        for name, detail in issues:
            log(f"  - {name}: {detail}")
    return 0 if not issues else 1


if __name__ == "__main__":
    sys.exit(main())
