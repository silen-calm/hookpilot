#!/usr/bin/env python3
"""Hookpilot 매일 트렌딩 영상 수집 — YouTube Shorts + TikTok + Instagram Reels
각 플랫폼 1000개씩 매일 자동 수집. 인기순·트렌딩·급상승·매출 기여 영상 위주.

YouTube Data API v3 키 필요 — 환경변수 YT_API_KEY 또는 hooklab/.env
TikTok·Instagram은 비공식 endpoint (RapidAPI 대체)

사용:
  # 1회 실행
  python3 crawler.py --all
  # 플랫폼별
  python3 crawler.py --youtube
  python3 crawler.py --tiktok
  python3 crawler.py --instagram
  # GitHub Actions에서 호출 (env YT_API_KEY)
"""

import os
import sys
import json
import time
import argparse
import urllib.parse
import urllib.request
import subprocess
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "data.js"
YTDLP = "/opt/homebrew/bin/yt-dlp"
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 "
    "Mobile/15E148 Safari/604.1"
)

# UA pool — 매일 3000개 요청 시 같은 UA 패턴 봇 차단 회피
import random as _random_ua
USER_AGENTS_POOL = [
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
]

def ua():
    """매 요청마다 다른 UA — 봇 패턴 회피"""
    return _random_ua.choice(USER_AGENTS_POOL)

def jitter_sleep(base_min=1.5, base_max=4.0):
    """요청 간 랜덤 sleep — 봇 같은 일정한 간격 X, 사람 같은 불규칙성"""
    import time
    time.sleep(_random_ua.uniform(base_min, base_max))

# 한국·미국 카테고리 ID (YouTube Data API v3)
# 22=People&Blogs, 26=Howto&Style, 24=Entertainment, 23=Comedy, 1=Film, 2=Autos, 10=Music
YT_CATEGORIES = {
    "엔터테인먼트": "24",
    "Howto Style": "26",
    "People Blogs": "22",
    "음악": "10",
    "게임": "20",
    "스포츠": "17",
    "코미디": "23",
    "뉴스": "25",
    "교육": "27",
    "테크": "28",
}

# YouTube 한국 trending shorts (#shorts 검색 + duration < 60s 필터)
def fetch_youtube_trending_shorts(api_key, region="KR", max_results=50, per_category=200):
    """YouTube Data API v3로 카테고리별 mostPopular + #shorts 검색 + Shorts 필터
    리턴: [{youtubeId, title, channel, views, likes, comments, uploadDate, duration, country, country2_KR}, ...]
    """
    out = []
    base = "https://www.googleapis.com/youtube/v3"
    # 1) mostPopular by category (videoCategoryId)
    for label, cat_id in YT_CATEGORIES.items():
        params = {
            "part": "snippet,statistics,contentDetails",
            "chart": "mostPopular",
            "regionCode": region,
            "videoCategoryId": cat_id,
            "maxResults": min(max_results, 50),
            "key": api_key,
        }
        url = f"{base}/videos?{urllib.parse.urlencode(params)}"
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                data = json.loads(r.read())
            for item in data.get("items", []):
                vid = item["id"]
                snip = item["snippet"]
                stats = item.get("statistics", {})
                details = item.get("contentDetails", {})
                duration = _parse_iso_duration(details.get("duration", ""))
                # Shorts 필터: duration < 65s
                if duration is None or duration > 65: continue
                out.append({
                    "youtubeId": vid,
                    "platform": "Shorts",
                    "country": region,
                    "title": snip.get("title"),
                    "channel": snip.get("channelTitle"),
                    "views": int(stats.get("viewCount", 0)) or None,
                    "likes": int(stats.get("likeCount", 0)) or None,
                    "comments": int(stats.get("commentCount", 0)) or None,
                    "uploadDate": snip.get("publishedAt", "")[:10],
                    "duration": duration,
                    "category": label,
                    "_source": "youtube_data_api_mostPopular",
                })
        except Exception as e:
            sys.stderr.write(f"[YT {label}] {e}\n")

    # 2) search for #shorts (관련 인기 검색) — 10개로 확장
    search_terms = [
        "#shorts", "shorts 추천", "쇼츠 인기", "viral shorts",
        "kbeauty shorts", "kpop shorts", "올리브영 추천",
        "다이소 추천", "맛집 추천", "리뷰 shorts",
    ]
    for q in search_terms:
        params = {
            "part": "snippet",
            "q": q,
            "type": "video",
            "videoDuration": "short",  # < 4 min
            "order": "viewCount",
            "regionCode": region,
            "maxResults": 50,
            "key": api_key,
        }
        url = f"{base}/search?{urllib.parse.urlencode(params)}"
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                data = json.loads(r.read())
            ids = [it["id"]["videoId"] for it in data.get("items", []) if it.get("id", {}).get("kind") == "youtube#video"]
            if not ids: continue
            # 메타 보강 — videos endpoint로 stats 추가
            params2 = {
                "part": "snippet,statistics,contentDetails",
                "id": ",".join(ids),
                "key": api_key,
            }
            url2 = f"{base}/videos?{urllib.parse.urlencode(params2)}"
            with urllib.request.urlopen(url2, timeout=10) as r:
                data2 = json.loads(r.read())
            for item in data2.get("items", []):
                vid = item["id"]
                snip = item["snippet"]
                stats = item.get("statistics", {})
                details = item.get("contentDetails", {})
                duration = _parse_iso_duration(details.get("duration", ""))
                if duration is None or duration > 65: continue
                out.append({
                    "youtubeId": vid,
                    "platform": "Shorts",
                    "country": region,
                    "title": snip.get("title"),
                    "channel": snip.get("channelTitle"),
                    "views": int(stats.get("viewCount", 0)) or None,
                    "likes": int(stats.get("likeCount", 0)) or None,
                    "comments": int(stats.get("commentCount", 0)) or None,
                    "uploadDate": snip.get("publishedAt", "")[:10],
                    "duration": duration,
                    "category": q,
                    "_source": f"youtube_data_api_search_{q}",
                })
        except Exception as e:
            sys.stderr.write(f"[YT search {q}] {e}\n")
        jitter_sleep(1.0, 2.5)  # YouTube Data API 봇 패턴 회피
    # 중복 제거 (youtubeId 기준)
    seen = set()
    uniq = []
    for v in out:
        if v["youtubeId"] in seen: continue
        seen.add(v["youtubeId"])
        uniq.append(v)
    return uniq


def _parse_iso_duration(iso):
    """PT1M30S → 90초"""
    if not iso: return None
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso)
    if not m: return None
    h, mn, s = (int(g) if g else 0 for g in m.groups())
    return h * 3600 + mn * 60 + s


# 본업 카테고리별 YouTube 검색 키워드 (YT API 키 없이 yt-dlp ytsearch 사용)
# specific 키워드로 매칭률 향상 (옛 5% → 목표 20%+)
YT_BIZ_KEYWORDS_KR = [
    # 뷰티 (구체 브랜드·제품명)
    "올리브영 추천템 shorts", "올리브영 신상 shorts", "다이소 화장품 shorts",
    "메이크업 꿀팁 shorts", "스킨케어 루틴 shorts", "립스틱 추천 shorts",
    "쿠션 추천 shorts", "선크림 추천 shorts", "에센스 추천 shorts",
    "셀프 네일 shorts", "헤어 염색 shorts",
    "코스알엑스 리뷰 shorts", "아누아 추천 shorts", "라네즈 신상 shorts",
    "메디큐브 추천 shorts", "닥터자르트 shorts", "이니스프리 신상 shorts",
    "에뛰드 신상 shorts", "토너패드 추천 shorts", "앰플 추천 shorts",
    "세럼 추천 shorts", "보디로션 추천 shorts", "립밤 추천 shorts",
    # D2C·패션
    "무신사 코디 shorts", "29cm 추천 shorts", "오오티디 shorts",
    "데일리룩 shorts", "쇼핑몰 추천 shorts",
    "데일리 출근룩 shorts", "여름룩 추천 shorts", "겨울 코디 shorts",
    "맨투맨 코디 shorts", "원피스 추천 shorts", "스니커즈 추천 shorts",
    "백 추천 shorts", "셋업 코디 shorts", "데님 추천 shorts",
    # 의료·동물병원
    "동물병원 정보 shorts", "강아지 훈련 shorts", "고양이 건강 shorts",
    "반려동물 보험 shorts", "사료 추천 shorts",
    "치과 정보 shorts", "임플란트 후기 shorts", "피부과 케어 shorts",
    "여드름 치료 shorts", "한의원 정보 shorts",
    "탈모 치료 shorts", "보톡스 후기 shorts", "필러 후기 shorts",
    "리프팅 후기 shorts", "비포 애프터 shorts", "치아 미백 shorts",
    "임플란트 가격 shorts", "여드름 흉터 shorts",
    # 자영업·F&B
    "카페 창업 shorts", "자영업 마케팅 shorts", "매장 인테리어 shorts",
    "맛집 추천 shorts", "홈카페 shorts", "디저트 신상 shorts",
    "한국 음식 추천 shorts", "배달 음식 shorts",
    "1인사장 일상 shorts", "매출 공개 shorts", "장사 꿀팁 shorts",
    "분식집 매출 shorts", "카페 매출 shorts", "베이커리 창업 shorts",
    "프랜차이즈 창업 shorts", "외식업 노하우 shorts",
    # 펫·동물
    "강형욱 강아지 shorts", "수의사 설채현 shorts", "강아지 산책 shorts",
    "고양이 일상 shorts", "댕댕이 일상 shorts", "냥이 일상 shorts",
    # 피트니스
    "홈트레이닝 shorts", "다이어트 식단 shorts", "필라테스 동작 shorts",
    "복근 운동 shorts", "스트레칭 shorts",
    "필라테스 자세 shorts", "요가 자세 shorts", "체형 교정 shorts",
    "데드리프트 자세 shorts", "스쿼트 자세 shorts",
    # 교육
    "영어 공부 shorts", "토익 강의 shorts", "취업 면접 shorts",
    "수능 영어 shorts", "공시 합격 shorts", "한국사 강의 shorts",
    "토플 공부 shorts", "오픽 공부 shorts",
]
# 검증된 한국 본업 채널 — data.js에서 확인된 의료·뷰티·D2C·자영업·동물병원·교육·피트니스 채널 35+
# 채널 이름으로 ytsearch → 인기 영상 5~10개씩 = 매칭률 90%+ (이미 본업 채널이라 자동 통과)
KR_BIZ_CHANNELS = [
    # 의료 (병원·의사) — 20+
    "닥터프렌즈", "청담닥터원", "닥터깡데기 피부과전문의", "그레이트온", "더뉴치과의원",
    "백세 건강보감", "대한심장TV", "피부팅 김동하박사", "치과의사 최기수&이정아",
    "닥터하준", "닥터지바고 정신과", "헬스조선", "비온뒤", "닥터스 김두현",
    "닥터인사이드", "리얼라이프 메디컬", "코메디닷컴", "정라레", "강남구 김약사",
    "Dr.리노", "닥터멘토", "메디컬가이드",
    # 동물병원 — 10+
    "24시 더케어동물의료센터", "24시 루시드동물메디컬센터", "24시 수동물병원", "24시 예은동물의료센터",
    "강형욱의 보듬TV", "수의사 설채현", "TV동물농장", "닥터캣 김명철", "펫토피아 동물병원",
    "라이펫", "냥 의사 김명철",
    # 뷰티 — 15+
    "뷰티숨BEAUTYSOOM", "현주심", "포도podo", "쥐로구Jwilogu", "온글림", "idollab",
    "코싱메팅 그루밍", "핑구", "김이영 LeeyeongKim", "1분뷰티", "주호이 ZUYONI",
    "유리뷰", "라뮤끄", "리이나", "또나 ddo_na", "조효진",
    # D2C·쇼핑몰 — 12+
    "MUSINSA SHORTS", "MUSINSA TV", "29CM", "패션플래닛", "임마멜 mamel",
    "곰송이 GOMSONGYI", "러브패밀리스", "옷튜브", "셀러튜브", "쇼핑몰만마케팅합니다",
    "공구왕황부장", "위클리포커스 패션",
    # 자영업·창업 — 12+
    "도시에서 온 총각", "무오muo의 잡동사니", "머니올라_KBS", "장사하는기억",
    "장사의 신", "자영업캠퍼스", "한국마케팅TV", "월천CEO", "탕탕탕 자영업",
    "사장님 응원해요", "장사잘되는방법", "외식업진성쌤",
    # F&B — 10+
    "백종원의 요리비책", "이연복 셰프 화룡천국", "쯔양 jjuyang", "햄지 hamzy",
    "tzuyang쯔양", "수빙수tv suvinsutv", "조이로그 ASMR Joylog",
    "박나래의 인생술집", "K-FOOD 한국음식",
    # 피트니스·다이어트 — 12+
    "간결한 움직임", "장보령 필라테스", "윤가놈", "다이어트뉴스", "필라테스 그라운드",
    "강과장 핏바디", "이수아 다이어트", "심으뜸", "Eunkkang fitness",
    "Jin Stretch", "키링헬스", "전 다은",
    # 교육 — 10+
    "EBS ENGLISH", "숏부기", "공부의신 강성태", "이지영TV", "한국사 큰별쌤",
    "현우진 수학", "오르비", "이투스 김기훈", "EBSi 고교강의", "메가스터디교육",
]

YT_BIZ_KEYWORDS_US = [
    # K-beauty (한국 브랜드)
    "kbeauty haul shorts", "korean skincare shorts", "olive young haul shorts",
    "korean makeup tutorial shorts", "korean glass skin shorts",
    "cosrx review shorts", "anua review shorts", "laneige review shorts",
    "medicube shorts", "tirtir shorts",
    # General beauty
    "makeup tutorial shorts", "skincare routine shorts", "lip combo shorts",
    "drugstore makeup shorts", "haircare tips shorts",
    "serum review shorts", "retinol routine shorts", "vitamin c serum shorts",
    "niacinamide review shorts", "hyaluronic acid shorts",
    "sephora finds shorts", "ulta haul shorts", "amazon beauty finds shorts",
    "tarte cosmetics shorts", "rare beauty shorts", "fenty beauty shorts",
    # D2C·Fashion
    "fashion try on shorts", "outfit ideas shorts", "ootd shorts",
    "zara haul shorts", "h&m haul shorts", "amazon fashion finds shorts",
    "shein haul shorts", "stradivarius haul shorts",
    "nike review shorts", "adidas review shorts", "lululemon haul shorts",
    # Pet·vet
    "dog training shorts", "pet care tips shorts", "veterinarian advice shorts",
    "cat care shorts", "puppy training shorts", "dog grooming shorts",
    "cat behavior shorts", "pet health shorts", "dog food review shorts",
    # Med
    "dermatologist tips shorts", "dental care shorts", "skincare doctor shorts",
    "acne treatment shorts", "anti aging tips shorts", "botox before after shorts",
    "lip filler shorts", "teeth whitening shorts",
    # F&B
    "food review shorts", "quick recipe shorts", "cafe vlog shorts",
    "korean food shorts", "easy recipe shorts", "viral recipe shorts",
    "asmr eating shorts", "mukbang shorts", "starbucks review shorts",
    "mcdonalds review shorts", "tiktok recipe shorts",
    # Fitness
    "home workout shorts", "weight loss shorts", "pilates shorts",
    "ab workout shorts", "hiit workout shorts", "yoga routine shorts",
    "stretching routine shorts", "diet tips shorts",
    # Business
    "small business owner shorts", "cafe owner shorts",
    "small business tips shorts", "etsy seller shorts", "amazon seller shorts",
    "ecommerce tips shorts",
    # Education
    "study with me shorts", "study tips shorts", "gre prep shorts",
    "english tips shorts", "language learning shorts",
    # === US Shorts 부족분 추가 (사장님 요구 -420 충당) ===
    # K-beauty 더
    "korean glow skin shorts", "korean beauty haul shorts", "korean spa shorts",
    "innisfree shorts", "etude shorts", "skinfood shorts", "missha shorts",
    "tonymoly shorts", "korean glow drops shorts", "snail mucin shorts",
    # Beauty 더
    "everyday makeup shorts", "natural makeup shorts", "no makeup shorts",
    "bold lip shorts", "dewy skin shorts", "matte foundation shorts",
    "eyeshadow tutorial shorts", "winged eyeliner shorts", "korean lip shorts",
    "blush placement shorts", "highlighter shorts", "contour tutorial shorts",
    "before after makeup shorts", "5 minute makeup shorts",
    # Fashion 더
    "summer outfit shorts", "winter outfit shorts", "office outfit shorts",
    "date night outfit shorts", "travel outfit shorts", "vacation outfit shorts",
    "petite outfit shorts", "plus size outfit shorts", "minimal outfit shorts",
    "y2k outfit shorts", "coastal grandma shorts", "old money outfit shorts",
    "free people haul shorts", "anthropologie haul shorts", "abercrombie haul shorts",
    # Food 더
    "viral tiktok food shorts", "tiktok pasta shorts", "tiktok salmon shorts",
    "healthy breakfast shorts", "smoothie recipe shorts", "salad recipe shorts",
    "high protein meal shorts", "meal prep shorts", "breakfast ideas shorts",
    "lunch ideas shorts", "dinner ideas shorts", "dessert recipe shorts",
    "chick fil a review shorts", "taco bell review shorts", "in n out shorts",
    # Pet 더
    "golden retriever shorts", "corgi shorts", "shiba inu shorts",
    "rescue dog shorts", "kitten care shorts", "senior dog care shorts",
    "dog walking tips shorts", "pet adoption shorts", "vet visit shorts",
    "cat playing shorts", "puppy shorts", "funny pets shorts",
    # Fitness 더
    "30 day challenge shorts", "before after weight loss shorts",
    "morning workout shorts", "no equipment workout shorts",
    "core workout shorts", "glute workout shorts", "arm workout shorts",
    "calorie deficit shorts", "intermittent fasting shorts",
    # Business 더
    "side hustle shorts", "passive income shorts", "online business shorts",
    "freelance tips shorts", "money tips shorts", "investing tips shorts",
    # Med 더
    "skincare routine doctor shorts", "dentist tips shorts", "myth busting shorts",
]


def fetch_youtube_search(keyword, max_results=15, region="KR"):
    """yt-dlp ytsearch — YT API 키 없이 무료. 봇 차단 위험 적음 (메타만 추출).
    Shorts(60초 이하)만 필터링. uploadDate 8자리 YYYYMMDD → YYYY-MM-DD 변환."""
    out = []
    try:
        # yt-dlp ytsearch 한 키워드 timeout 20초 — 더 짧게 (회귀: 일부 키워드 7분+ hang)
        # 60초 → 20초. 30 키워드 × 20초 = 10분 안에 YT 단계 완료 보장.
        proc = subprocess.run([
            YTDLP, "--flat-playlist", "--playlist-items", f"1:{max_results}",
            "--no-warnings", "--quiet",
            "--socket-timeout", "10",  # socket 자체 10초 timeout (DNS·connect hang 방지)
            "--print", "%(id)s|%(title)s|%(view_count)s|%(duration)s|%(uploader)s|%(upload_date)s",
            f"ytsearch{max_results}:{keyword}",
        ], capture_output=True, timeout=20)
        if proc.returncode != 0:
            sys.stderr.write(f"[YT-search {keyword[:30]}] exit {proc.returncode}\n")
            return out
        for line in proc.stdout.decode("utf-8", errors="ignore").strip().split("\n"):
            if not line: continue
            parts = line.split("|", 5)
            if len(parts) < 5: continue
            yid, title, views, dur, uploader = parts[0], parts[1], parts[2], parts[3], parts[4]
            updt = parts[5] if len(parts) >= 6 else None
            dur_int = None
            try:
                if dur and dur != "NA" and dur != "None": dur_int = int(float(dur))
            except Exception: pass
            # Shorts(60~90초 이하)만
            if dur_int is None or dur_int > 90: continue
            try: vi = int(views) if views and views.isdigit() else None
            except Exception: vi = None
            ud = None
            if updt and len(updt) == 8 and updt.isdigit():
                ud = f"{updt[:4]}-{updt[4:6]}-{updt[6:8]}"
            out.append({
                "youtubeId": yid,
                "platform": "Shorts",
                "country": region,
                "title": title,
                "channel": uploader or "",
                "views": vi,
                "duration": dur_int,
                "uploadDate": ud,
                "_source": f"ytdlp_search:{keyword[:30]}",
            })
    except subprocess.TimeoutExpired:
        sys.stderr.write(f"[YT-search {keyword[:30]}] timeout\n")
    except Exception as e:
        sys.stderr.write(f"[YT-search {keyword[:30]}] {e}\n")
    return out


def fetch_youtube_via_ytdlp_search(fast=False):
    """YT API 키 없이 yt-dlp ytsearch로 본업 카테고리별 YouTube Shorts 일괄 수집.
    매일 300개 신규 목표 — per_kw 25→40 증대, 신선도 키워드 추가.
    fast=True → 키워드 절반·per_kw 25 사용 (~12분, 일일 검증용)
    """
    out = []
    seen = set()
    FRESH_SUFFIXES = ["최근", "오늘", "신상", "신규", "2026", "이번주", "어제"]
    # fast mode 키워드 슬라이스 — 빠른 검증용
    kr_kws = YT_BIZ_KEYWORDS_KR[::2] if fast else YT_BIZ_KEYWORDS_KR  # 절반 (179→90)
    us_kws = YT_BIZ_KEYWORDS_US[::2] if fast else YT_BIZ_KEYWORDS_US  # 절반 (90→45)
    channels = KR_BIZ_CHANNELS[::2] if fast else KR_BIZ_CHANNELS      # 절반 (50→25)
    per_kw = 50 if fast else 80   # fast 30→50 (매일 cron이 fast 모드 사용). 정상 60→80.
    skip_fresh = fast  # fast 모드는 fresh 보강 스킵
    for kw in kr_kws:
        rows = fetch_youtube_search(kw, max_results=per_kw, region="KR")
        for r in rows:
            if r["youtubeId"] not in seen:
                seen.add(r["youtubeId"])
                out.append(r)
        jitter_sleep(1.5, 3.0)  # 봇 차단 회피
    # 신선도 보강 — fast 모드 스킵. 매일 새 영상 500+ 위해 fresh suffix 확장.
    if not skip_fresh:
        for kw in YT_BIZ_KEYWORDS_KR[:60]:  # 30→60 (더 많은 카테고리 fresh 커버)
            for suf in FRESH_SUFFIXES[:4]:  # 2→4 (suffix 더 다양)
                rows = fetch_youtube_search(f"{kw} {suf}", max_results=25, region="KR")
                for r in rows:
                    if r["youtubeId"] not in seen:
                        seen.add(r["youtubeId"])
                        r["_source"] = f"ytdlp_fresh:{kw[:15]}_{suf}"
                        out.append(r)
                jitter_sleep(1.2, 2.5)
    for kw in us_kws:
        rows = fetch_youtube_search(kw, max_results=per_kw, region="US")
        for r in rows:
            if r["youtubeId"] not in seen:
                seen.add(r["youtubeId"])
                out.append(r)
        jitter_sleep(1.5, 3.0)
    # 검증된 본업 한국 채널 — 각 채널명으로 ytsearch
    for ch in channels:
        rows = fetch_youtube_search(f"{ch} shorts", max_results=20, region="KR")
        for r in rows:
            if r["youtubeId"] not in seen:
                seen.add(r["youtubeId"])
                # 채널 기반으로 가져온 영상은 본업 매칭 강화 표시
                r["_source"] = f"ytdlp_channel:{ch[:18]}"
                out.append(r)
        jitter_sleep(2.0, 4.0)
    sys.stderr.write(f"[YT-search] 키워드 {len(YT_BIZ_KEYWORDS_KR)+len(YT_BIZ_KEYWORDS_US)}개 + 본업 채널 {len(KR_BIZ_CHANNELS)}개 → 중복제거 {len(out)}개\n")
    return out


def fetch_tiktok_trending(region="KR", max_results=200):
    """TikTok 트렌딩 — TikWM 비공식 API + 인기 해시태그 검색
    한국 인기 해시태그: #fyp #korea #kbeauty #foryou #viral
    """
    out = []
    # 한국 + 미국 해시태그 분리 — 각 80+개. country 명시적 박음 (한글 분류 안 의존)
    kr_hashtags = [
        # 뷰티·K-beauty
        "kbeauty", "skincare", "올리브영", "올영", "올영템", "다이소뷰티", "토너패드", "앰플",
        "선크림", "글로우", "안티에이징", "메이크업", "쿠션", "립글로스", "마스카라",
        "콜라겐", "각질", "보습", "미백", "리프팅", "물광", "꿀광", "필링",
        # D2C·패션
        "무신사", "29cm", "ootd", "haul", "스니커즈", "원피스", "데님", "셋업",
        "데일리룩", "오피스룩", "데이트룩", "캠퍼스룩", "남자코디", "여자코디",
        # 의료
        "피부과", "치과", "성형외과", "한의원", "탈모치료", "여드름", "보톡스", "필러",
        "임플란트", "비포애프터", "닥터", "치아미백", "도수치료", "리프팅",
        # 동물·반려
        "반려견", "강아지", "고양이", "댕댕이", "냥스타", "동물병원", "수의사", "펫푸드",
        "강형욱", "수제간식", "예방접종", "펫미용",
        # 자영업·소상공인
        "자영업", "소상공인", "사장님", "카페창업", "공방", "매출공개", "프랜차이즈",
        "1인사장", "월매출", "상권분석",
        # F&B·먹방
        "맛집", "먹방", "베이커리", "디저트", "카페", "브런치", "분식", "치킨", "한식",
        "쯔양", "햄지", "탕탕탕", "곱창", "삼겹살", "라멘",
        # 피트니스
        "헬스", "다이어트", "필라테스", "요가", "홈트", "운동", "스쿼트", "복근",
        # 교육
        "공부", "토익", "공시", "한국사", "강의", "현우진", "이지영",
        # 일반 viral (KR 필터링용)
        "추천", "shorts",
    ]
    us_hashtags = [
        # K-beauty US
        "koreanskincare", "kbeautyamerica", "koreanmakeup", "glasskin", "kbeautyhaul",
        # General beauty
        "makeup", "skincare", "lipcombo", "drugstoremakeup", "haircare",
        "ampoule", "retinol", "niacinamide", "hyaluronic", "vitaminc",
        "glowrecipe", "rarebeauty", "fenty", "charlottetilbury",
        # D2C·Fashion US
        "fashion", "outfit", "fashiontrends", "ootd", "haul", "tryon", "unboxing",
        "stradivarius", "zara", "h&m", "nike", "adidas", "puma", "newbalance",
        # Pet·Vet
        "dogtraining", "petcare", "veterinarian", "doglovers", "petlovers",
        "doggrooming", "petfood", "puppy", "kitten",
        # Medical (US)
        "dermatology", "dentalcare", "lasik", "botox", "plasticsurgery",
        "dentist", "doctorinsta",
        # SMB US
        "smallbusiness", "entrepreneur", "smbtips", "ecommerce", "shopify",
        "etsy", "amazonseller",
        # F&B US
        "foodie", "asmrfood", "asmreating", "starbucks", "mcdonalds",
        "icecream", "coffee", "yummy", "delicious",
        # Fitness US
        "workout", "fitness", "gym", "abs", "hiit", "crossfit", "yoga",
        "pilates", "weighttraining",
        # Education US
        "studywithme", "studymotivation", "studytips", "examprep", "gre",
        # General viral
        "fyp", "viral", "shortvideo",
    ]
    # 한국·미국 따로 — country 명시적으로 박음
    HASHTAG_PLAN = [(kr_hashtags, "KR"), (us_hashtags, "US")]
    hashtags_seen = set()
    hashtag_country_map = {}
    for h_list, country in HASHTAG_PLAN:
        for h in h_list:
            if h not in hashtags_seen:
                hashtags_seen.add(h)
                hashtag_country_map[h] = country
    hashtags = list(hashtags_seen)
    # 페이지 깊이 country별 비대칭 — KR 7페이지(210개) · US 5페이지(150개). 한국·미국 각 500+/일 목표.
    MAX_PAGES = {"KR": 7, "US": 5}
    # TT 전체 단계 hard timeout — 10분 안에 TT 끝남 (cron 가 TT 에 갇히지 않게)
    # 5/19-5/21 회귀: TikWM read timeout 줄줄이 → cron 30분 hard 까지 stuck → trending_results 파일 없음
    import time as _tt
    tt_start_ts = _tt.time()
    TT_HARD_TIMEOUT = 600  # 10분
    global_consecutive_fail = 0  # 연속 실패 카운트 (전체 TT)
    for tag in hashtags:
        # hard timeout 초과 시 즉시 종료 — 부분 결과라도 저장
        if _tt.time() - tt_start_ts > TT_HARD_TIMEOUT:
            sys.stderr.write(f"[TT] 10분 hard timeout — 남은 태그 {len(hashtags) - hashtags.index(tag)}개 skip, 부분 결과 저장\n")
            break
        # 전체 연속 실패 8회 = TikWM 일시 차단 — TT 자체 abort (YT/IG 만이라도 진행)
        if global_consecutive_fail >= 8:
            sys.stderr.write(f"[TT] 전체 연속 실패 {global_consecutive_fail}회 — TikWM 일시 차단으로 판단, TT abort\n")
            break
        tag_country = hashtag_country_map.get(tag, "KR")
        max_pages = MAX_PAGES.get(tag_country, 5)
        tag_consecutive_fail = 0
        # requests 라이브러리 사용 — urllib보다 정밀한 connect/read timeout. Python 3.14 socket hang 회피.
        try:
            import requests as _rq
        except ImportError:
            _rq = None
        for page in range(max_pages):
            try:
                cursor = page * 30
                url = f"https://www.tikwm.com/api/feed/search?keywords={urllib.parse.quote(tag)}&count=30&cursor={cursor}&HD=1"
                if _rq is not None:
                    # connect 5초 + read 8초 정밀 timeout
                    resp = _rq.get(url, headers={"User-Agent": ua()}, timeout=(5, 8))
                    data = resp.json()
                else:
                    req = urllib.request.Request(url, headers={"User-Agent": ua()})
                    with urllib.request.urlopen(req, timeout=8) as r:
                        data = json.loads(r.read(10*1024*1024))
                tag_consecutive_fail = 0
                if data.get("code") != 0: break
                videos = data.get("data", {}).get("videos", [])
                if not videos: break
                for d in videos:
                    aid = d.get("aweme_id") or d.get("video_id")
                    au = d.get("author", {})
                    _title = d.get("title", "") or ""
                    _nick = au.get("nickname", "") or ""
                    _uid = au.get("unique_id", "") or ""
                    # 한글 있으면 KR 우선, 없으면 해시태그 출처 country 사용
                    _has_korean = any('가' <= c <= '힣' or 'ㄱ' <= c <= 'ㆎ' for c in (_title + _nick + _uid))
                    country = "KR" if _has_korean else tag_country
                    out.append({
                        "tiktokId": aid,
                        "tiktokUser": au.get("unique_id"),
                        "platform": "TikTok",
                        "country": country,
                        "title": d.get("title"),
                        "channel": au.get("unique_id") or au.get("nickname"),
                        "views": d.get("play_count"),
                        "likes": d.get("digg_count"),
                        "comments": d.get("comment_count"),
                        "shares": d.get("share_count"),
                        "duration": d.get("duration"),
                        "uploadDate": _ts_to_date(d.get("create_time")),
                        "category": f"#{tag}",
                        "_source": f"tikwm_search_{tag}_p{page}",
                    })
                if not data.get("data", {}).get("hasMore"): break
            except Exception as e:
                sys.stderr.write(f"[TT {tag} p{page}] {str(e)[:80]}\n")
                tag_consecutive_fail += 1
                global_consecutive_fail += 1
                # 연속 실패 시 hashtag 자체 abort (TikWM 일시 차단 회피)
                if tag_consecutive_fail >= 2: break
                jitter_sleep(2.0, 4.0)
                continue
            # 성공 시 글로벌 카운터 리셋
            global_consecutive_fail = 0
            jitter_sleep(0.9, 1.8)  # 페이지 간 jitter (페이지 많아져서 조금 줄임)
    # 중복 제거
    seen = set()
    uniq = []
    for v in out:
        if v["tiktokId"] in seen: continue
        seen.add(v["tiktokId"])
        uniq.append(v)
    return uniq


def fetch_instagram_via_instaloader(accounts, per_account=10):
    """yt-dlp Instagram 깨질 때 fallback — instaloader CLI 사용.
    rate-limit·로그인 회피 위해 --no-metadata-json --no-pictures --no-videos 로 메타만.
    리턴: yt-dlp 결과와 동일 스키마.
    """
    out = []
    INSTA = "/opt/homebrew/bin/instaloader"
    if not os.path.exists(INSTA):
        return out
    # instaloader 임시 작업 디렉토리 — 다운로드 후 즉시 삭제 (디스크 안 채움)
    tmp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".tmp", "ig")
    os.makedirs(tmp_dir, exist_ok=True)
    for acc in accounts:
        try:
            # username 직접. --no-pictures 둘 다 켜면 메타만 받음 (stderr에 shortcode 로깅)
            proc = subprocess.run([
                INSTA, "--quiet", "--no-pictures", "--no-video-thumbnails",
                "--no-captions", "--metadata-json",  # JSON만 받기
                "--dirname-pattern", tmp_dir + "/{profile}",
                "--count", str(per_account),
                acc,
            ], capture_output=True, timeout=45)
            txt = (proc.stdout or b"").decode("utf-8", errors="ignore") + \
                  (proc.stderr or b"").decode("utf-8", errors="ignore")
            # 1차: 출력에서 shortcode 정규식
            for m in re.finditer(r"https?://www\.instagram\.com/(?:p|reel)/([A-Za-z0-9_-]+)", txt):
                sid = m.group(1)
                out.append({
                    "instagramShortcode": sid, "platform": "Reels", "country": "KR",
                    "title": "", "channel": acc, "_source": f"instaloader_{acc}",
                })
            # 2차: 다운된 JSON 파일에서 shortcode 추출
            acc_dir = os.path.join(tmp_dir, acc)
            if os.path.isdir(acc_dir):
                for fn in os.listdir(acc_dir):
                    if fn.endswith(".json.xz") or fn.endswith(".json"):
                        # 파일명 패턴: 2026-05-17_12-34-56_UTC.json 등. shortcode는 메타에서 추출
                        try:
                            import lzma, json as _j
                            fp = os.path.join(acc_dir, fn)
                            data = _j.loads((lzma.open(fp).read() if fn.endswith(".xz") else open(fp, "rb").read()))
                            sc = (data.get("node") or {}).get("shortcode") or data.get("shortcode")
                            if sc:
                                out.append({
                                    "instagramShortcode": sc, "platform": "Reels", "country": "KR",
                                    "title": (data.get("node") or {}).get("edge_media_to_caption", {}).get("edges", [{}])[0].get("node", {}).get("text", "") or "",
                                    "channel": acc, "_source": f"instaloader_{acc}",
                                    "views": (data.get("node") or {}).get("video_view_count"),
                                    "likes": (data.get("node") or {}).get("edge_media_preview_like", {}).get("count"),
                                    "comments": (data.get("node") or {}).get("edge_media_to_comment", {}).get("count"),
                                })
                        except Exception: pass
                # 다운된 파일 삭제 (디스크 절약)
                try:
                    import shutil; shutil.rmtree(acc_dir, ignore_errors=True)
                except Exception: pass
        except Exception as e:
            sys.stderr.write(f"[IG-instaloader {acc}] {e}\n")
        jitter_sleep(3.0, 6.0)
    return out


def fetch_youtube_via_rss(channel_ids_or_handles):
    """YouTube 채널 RSS 피드 — youtube.com/feeds/videos.xml?channel_id=UCxxx
    공개 엔드포인트, 키 없음, 로그인 없음, 봇 차단 거의 없음.
    핸들(@xxx)도 채널 ID 자동 해석. 채널당 최신 15개 video 반환.
    """
    out = []
    RSS_BASE = "https://www.youtube.com/feeds/videos.xml"
    for ident in channel_ids_or_handles:
        cid = ident
        if not cid.startswith("UC"):
            # 핸들/사용자명 → channel_id 해석
            try:
                handle_url = f"https://www.youtube.com/@{ident.lstrip('@')}" if not ident.startswith("http") else ident
                req = urllib.request.Request(handle_url, headers={"User-Agent": ua()})
                with urllib.request.urlopen(req, timeout=8) as r:
                    html = r.read().decode("utf-8", errors="ignore")
                m = re.search(r'"channelId":"(UC[A-Za-z0-9_-]{22})"', html)
                if not m:
                    m = re.search(r'/channel/(UC[A-Za-z0-9_-]{22})', html)
                if not m:
                    sys.stderr.write(f"[YT-RSS {ident[:20]}] channelId 추출 실패\n")
                    continue
                cid = m.group(1)
                jitter_sleep(0.5, 1.5)
            except Exception as e:
                sys.stderr.write(f"[YT-RSS {ident[:20]}] handle 해석 실패: {e}\n")
                continue
        try:
            url = f"{RSS_BASE}?channel_id={cid}"
            req = urllib.request.Request(url, headers={"User-Agent": ua()})
            with urllib.request.urlopen(req, timeout=10) as r:
                xml = r.read().decode("utf-8", errors="ignore")
            # RSS entry 파싱 (정규식 — XML 의존성 회피)
            entries = re.findall(
                r'<entry>(.*?)</entry>', xml, flags=re.DOTALL,
            )
            count = 0
            for entry in entries:
                vid_m = re.search(r'<yt:videoId>([^<]+)</yt:videoId>', entry)
                title_m = re.search(r'<title>([^<]+)</title>', entry)
                ch_m = re.search(r'<name>([^<]+)</name>', entry)
                pub_m = re.search(r'<published>([^<]+)</published>', entry)
                views_m = re.search(r'views="(\d+)"', entry)
                stars_m = re.search(r'count="(\d+)"', entry)
                if not vid_m: continue
                pub = pub_m.group(1)[:10] if pub_m else None
                out.append({
                    "youtubeId": vid_m.group(1),
                    "platform": "Shorts", "country": "KR",
                    "title": title_m.group(1) if title_m else "",
                    "channel": ch_m.group(1) if ch_m else "",
                    "views": int(views_m.group(1)) if views_m else None,
                    "likes": int(stars_m.group(1)) if stars_m else None,
                    "duration": None,  # RSS에 없음 — Shorts 필터는 merge에서
                    "uploadDate": pub,
                    "_source": f"yt_rss:{cid[:8]}",
                })
                count += 1
            sys.stderr.write(f"[YT-RSS {ident[:20]}] {count}개\n")
        except Exception as e:
            sys.stderr.write(f"[YT-RSS {ident[:20]}] {e}\n")
        jitter_sleep(0.8, 2.0)
    return out


# YT RSS 수집 대상 채널 ID (UC로 시작 - 24자) 또는 검증된 핸들
# 차단 없음·무료·무인증. 핸들은 yt-dlp로 검증한 것만. 미검증 핸들은 추가 X.
# TODO: 한국 본업 채널 channel_id 수집 후 추가 — 현재 yt-dlp ytsearch가 주력
YT_RSS_HANDLES = []


def fetch_youtube_via_scrapetube(fast=False):
    """scrapetube (MIT, 510 stars, Snyk vulnerability 0) — yt-dlp ytsearch 보강용.
    무인증·무차단·빠름 (1쿼리 0.5초). YouTube 검색 결과 메타데이터 풍부 (id·title·views·length·channel).
    Shorts 필터: lengthText < 1분 (M:SS 패턴 첫 글자 0 또는 60초 미만).
    매일 cron 두 번 호출 시 200~400 신규 Shorts 누적 (yt-dlp 중복 제외).
    """
    try:
        import scrapetube
    except ImportError:
        sys.stderr.write("[YT-scrapetube] scrapetube 미설치 → 스킵 (pip install scrapetube)\n")
        return []
    out = []
    seen = set()
    # fast=True → 키워드 절반 (cron 시간 단축). YT_BIZ_KEYWORDS_KR/US 활용.
    kr_kws = YT_BIZ_KEYWORDS_KR[::3] if fast else YT_BIZ_KEYWORDS_KR[::2]  # 60(fast) 또는 90(full)
    us_kws = YT_BIZ_KEYWORDS_US[::3] if fast else YT_BIZ_KEYWORDS_US[::2]  # 30 또는 45
    per_kw = 8 if fast else 15  # scrapetube가 fast (1쿼리 ~0.6초)라 per_kw 작게도 충분
    def _parse_length_sec(length_text):
        """16:51 → 1011초, 0:45 → 45초"""
        if not length_text: return None
        try:
            parts = length_text.split(":")
            if len(parts) == 2: return int(parts[0]) * 60 + int(parts[1])
            if len(parts) == 3: return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        except: pass
        return None
    def _parse_views(view_count_text):
        if not view_count_text: return None
        m = re.search(r"([\d,]+)", view_count_text)
        if m:
            try: return int(m.group(1).replace(",", ""))
            except: pass
        return None
    def _parse_days_ago(published_time):
        if not published_time: return None
        m = re.search(r"(\d+)\s*(year|month|week|day|hour|minute)", published_time.lower())
        if not m: return None
        n, unit = int(m.group(1)), m.group(2)
        if unit == "hour" or unit == "minute": return 0
        if unit == "day": return n
        if unit == "week": return n * 7
        if unit == "month": return n * 30
        if unit == "year": return n * 365
        return None
    def _process_kw(kw, country):
        try:
            videos = list(scrapetube.get_search(kw, limit=per_kw))
            kept = 0
            for v in videos:
                vid = v.get("videoId")
                if not vid or vid in seen: continue
                # title 추출 (runs[0].text)
                title_obj = v.get("title", {})
                title = ""
                if isinstance(title_obj, dict):
                    runs = title_obj.get("runs", [])
                    if runs: title = runs[0].get("text", "")
                # channel
                ch_obj = v.get("longBylineText", {})
                channel = ""
                if isinstance(ch_obj, dict):
                    runs = ch_obj.get("runs", [])
                    if runs: channel = runs[0].get("text", "")
                # 길이 (Shorts 필터)
                length_text = v.get("lengthText", {}).get("simpleText", "") if isinstance(v.get("lengthText"), dict) else ""
                duration = _parse_length_sec(length_text)
                # Shorts만 (< 90초) — 일반 영상 제외
                if duration is not None and duration > 90: continue
                views = _parse_views(v.get("viewCountText", {}).get("simpleText", "") if isinstance(v.get("viewCountText"), dict) else "")
                pub_time = v.get("publishedTimeText", {}).get("simpleText", "") if isinstance(v.get("publishedTimeText"), dict) else ""
                days = _parse_days_ago(pub_time)
                upload_date = None
                if days is not None:
                    from datetime import datetime as _dt, timedelta as _td
                    upload_date = (_dt.now() - _td(days=days)).strftime("%Y-%m-%d")
                seen.add(vid)
                out.append({
                    "youtubeId": vid,
                    "platform": "Shorts",
                    "country": country,
                    "title": title,
                    "channel": channel,
                    "views": views,
                    "duration": duration,
                    "uploadDate": upload_date,
                    "_source": f"scrapetube:{kw[:15]}",
                })
                kept += 1
            sys.stderr.write(f"[YT-scrapetube '{kw[:25]}' {country}] {kept}개 (Shorts 필터 후)\n")
        except Exception as e:
            sys.stderr.write(f"[YT-scrapetube '{kw[:25]}'] {str(e)[:80]}\n")
    for kw in kr_kws: _process_kw(kw, "KR")
    for kw in us_kws: _process_kw(kw, "US")
    sys.stderr.write(f"[YT-scrapetube] 키워드 KR {len(kr_kws)} + US {len(us_kws)} → {len(out)}개 (중복 제외)\n")
    return out


def fetch_instagram_via_reddit(seen_shortcodes=None):
    """Reddit JSON API로 IG reel URL 추출 — 인증 X, 무료, rate limit 관대.
    DDG가 시간당 ~10건 hard limit인 우회로 — Reddit은 서브레딧당 50건 + 쿼리 다양화로 200~500/일 가능.
    8개 본업 카테고리별 검증된 서브레딧에서 IG link 추출.
    """
    out = []
    seen = seen_shortcodes if seen_shortcodes is not None else set()
    # 본업 카테고리별 서브레딧 — 50+ 검증된 곳. 페이지네이션(after) 2회로 서브레딧당 150건까지 확보.
    # 저수율 서브 (0~3 reels) 제거 + 검증된 고수율 추가. Reddit unauth 60 req/시간 limit 이하.
    SUBS_BY_CATEGORY = {
        "beauty": ["AsianBeauty", "koreanbeauty", "kbeauty", "skincareaddiction",
                   "MakeupAddiction", "BeautyGuruChatter", "30PlusSkinCare",
                   "Beauty", "AskWomenOver30", "hanguk", "Korean",
                   "indiebeauty", "drugstoreMUA", "muacjdiscussion", "skincareexchange",
                   "ABraThatFits", "Sephora", "Ulta", "DesiBeauty", "Hair",
                   "Curlyhair", "HaircareScience", "RedditLaqueristas",
                   # 2차 추가 — 사장님 IG 새 영상 16개 → 100+ 목표
                   "SkincareScience", "asianbeauty", "Makeup", "MakeupRehab", "MakeupAddictionUK",
                   "BeautyAddicts", "SkincareAddicts", "tanning", "RoutineQuestions",
                   "SkinCareTips", "BeautyBoxes", "AsianCosmetics", "FoundationRoutine"],
        "fashion": ["malefashionadvice", "streetwear", "femalefashionadvice",
                    "FashionReps", "frugalmalefashion", "japanesefashion",
                    "Watches", "hanguk",
                    # 추가
                    "OUTFITS", "PetiteFashionAdvice", "PlusSize", "BigandTall",
                    "Sneakers", "TheGlowUp", "Aesthetic", "vintage"],
        "medical": ["tretinoin", "askdocs", "Dentistry", "Optometry",
                    "DermatologyQuestions"],
        "pet": ["dogs", "rarepuppers", "kittens", "Pets", "DogTraining", "labrador",
                # 추가
                "AnimalsBeingDerps", "AnimalsBeingBros", "cats", "puppies",
                "PuppySmiles", "tippytaps", "AnimalTextGifs", "Aww", "OldDogsNewTricks",
                "germanshepherds", "goldenretrievers", "Husky", "Corgi"],
        "fnb": ["food", "FoodPorn", "recipes", "korea", "Cooking", "KoreanFood",
                "ramen", "baking", "cookingforbeginners", "Frugal",
                "MealPrepSunday", "ChicagoFood", "hanguk",
                # 추가
                "FoodVideos", "EatCheapAndHealthy", "AsianRecipe", "JapaneseFood",
                "Pizza", "burgers", "Coffee", "tea", "Desserts", "Pastry", "Breakfast",
                "vegetarian", "vegan", "GifRecipes"],
        "fitness": ["Fitness", "bodyweightfitness", "xxfitness", "GYM",
                    "homegym", "running", "naturalbodybuilding",
                    # 추가
                    "yoga", "pilates", "weightroom", "powerlifting", "Stretching",
                    "loseit", "1500isplenty", "intermittentfasting", "HIIT"],
        "smb": ["smallbusiness", "Entrepreneur", "EntrepreneurRideAlong", "marketing",
                "sidehustle", "FreelanceTips", "Etsy", "AskMarketing", "advertising",
                "PassiveIncome", "SocialMediaMarketing", "OnlineBusiness", "ecommerce",
                # 2차 추가
                "ShopifyNoobs", "Shopify", "DropshippingSecrets", "Restaurant",
                "RestaurantOwners", "ServingFood", "Coffee_Shop", "barbershop",
                "Hair_Salons", "NailArt", "TattooArtists"],
        "education": ["GetMotivated", "productivity", "selfimprovement", "BasicLifeSkills",
                      "Marriage", "Korean",
                      # 추가
                      "GradSchool", "AskAcademia", "AskProfessors", "GREprep",
                      "LearnJapanese", "Spanish", "languagelearning", "Anki",
                      "studytips", "StudyMotivation"],
    }
    HEADERS = {"User-Agent": "hookpilot-research/1.0"}
    # 각 카테고리·서브레딧에서 reel URL 검색 → IG shortcode 추출.
    # Reddit unauth API limit: 60 req/시간. 55 서브 × 1 쿼리 = 55 호출 → 안전.
    # 이전 KR boost 쿼리("korean"·"kbeauty")는 항상 0개 매칭이라 호출 낭비라서 제거.
    for cat, subs in SUBS_BY_CATEGORY.items():
        for sub in subs:
            queries = ["instagram.com/reel"]
            for q in queries:
                # Reddit search는 페이지당 약 10 posts hard limit. 단일 sort=new 결과만 신선.
                url = f"https://www.reddit.com/r/{sub}/search.json?q={urllib.parse.quote(q)}&restrict_sr=on&sort=new&limit=50"
                try:
                    req = urllib.request.Request(url, headers=HEADERS)
                    with urllib.request.urlopen(req, timeout=15) as r:
                        d = json.loads(r.read())
                    posts = d.get("data", {}).get("children", [])
                    count = 0
                    for p in posts:
                        pd = p.get("data", {})
                        text = (pd.get("url", "") or "") + " " + (pd.get("selftext", "") or "")
                        codes = re.findall(r"instagram\.com/reel/([A-Za-z0-9_\-]{6,15})", text)
                        for sc in codes:
                            if sc in seen: continue
                            seen.add(sc)
                            KR_SUBS = {"AsianBeauty", "koreanbeauty", "kbeauty", "korea", "hanguk", "Korean", "KoreanFood"}
                            is_kr_sub = sub in KR_SUBS
                            is_kr_q = ("korean" in q.lower() or "kbeauty" in q.lower())
                            title_kr = bool(re.search(r"[가-힣]", pd.get("title", "") or ""))
                            out.append({
                                "instagramShortcode": sc,
                                "platform": "Reels",
                                "country": "KR" if (is_kr_sub or is_kr_q or title_kr) else "US",
                                "title": (pd.get("title", "") or "")[:160],
                                "channel": f"reddit_{sub}",
                                "category_hint": cat,
                                "_source": f"reddit_search:{sub}:{q[-20:]}",
                            })
                            count += 1
                    sys.stderr.write(f"[IG-Reddit r/{sub} q={q[-15:]}] {count}개 (누적 {len(out)})\n")
                    jitter_sleep(1.2, 2.5)
                except Exception as e:
                    sys.stderr.write(f"[IG-Reddit r/{sub} q={q[-15:]}] {str(e)[:80]}\n")
                    jitter_sleep(2.0, 4.0)
    return out


def fetch_instagram_via_ddg(queries, per_query=10):
    """DuckDuckGo HTML 검색으로 IG reel URL 추출 — 로그인 X, 차단 X, 무료.
    yt-dlp Instagram extractor / instaloader 둘 다 IG 봇 차단된 환경에서 작동 검증됨.
    DDG는 페이지당 약 10개 결과 한정 (페이지네이션 시도 시 봇 차단 발동).
    yield 늘리려면 쿼리 자체를 다양화 (IG_DDG_QUERIES 130+ 사용).
    rate limit 회피용 매 요청 다른 UA + 긴 jitter.
    """
    out = []
    seen = set()
    consecutive_zero = 0
    consecutive_timeout = 0
    # IG 단계 hard timeout 8분 — cron 가 IG 에 갇히지 않게 (DDG rate limit 시 stuck 방지)
    import time as _t
    ig_start = _t.time()
    IG_HARD_TIMEOUT = 480
    for q in queries:
        if _t.time() - ig_start > IG_HARD_TIMEOUT:
            sys.stderr.write(f"[IG-DDG] 8분 hard timeout — 남은 {len(queries) - queries.index(q)}개 skip, 부분 결과 진행\n")
            break
        try:
            base_q = urllib.parse.quote('site:instagram.com/reel ' + q)
            url = f"https://html.duckduckgo.com/html/?q={base_q}"
            req = urllib.request.Request(url, headers={
                "User-Agent": ua(),
                "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            })
            with urllib.request.urlopen(req, timeout=5) as r:  # 12→5초 (DDG 막혔으면 빠르게 다음)
                html = r.read().decode("utf-8", errors="ignore")
            shortcodes = re.findall(r'instagram\.com/reel/([A-Za-z0-9_-]+)', html)
            count = 0
            for sc in shortcodes:
                if sc in seen: continue
                seen.add(sc)
                out.append({
                    "instagramShortcode": sc,
                    "platform": "Reels",
                    "country": "KR",
                    "title": "",
                    "channel": "ddg_curated",
                    "_source": f"ddg_search:{q[:30]}",
                })
                count += 1
                if count >= per_query: break
            sys.stderr.write(f"[IG-DDG '{q[:20]}'] {count}개\n")
            consecutive_timeout = 0
            if count == 0:
                consecutive_zero += 1
                if consecutive_zero >= 5:
                    sys.stderr.write(f"[IG-DDG] DDG가 IP 차단으로 연속 0개 5회 → abort, Reddit로 진행\n")
                    break  # fail-fast: 더 이상 시도 안 함
            else:
                consecutive_zero = 0
        except Exception as e:
            sys.stderr.write(f"[IG-DDG '{q[:20]}'] {str(e)[:60]}\n")
            consecutive_timeout += 1
            if consecutive_timeout >= 3:
                sys.stderr.write(f"[IG-DDG] timeout 연속 3회 → abort, Reddit로 진행\n")
                break  # fail-fast: timeout 누적되면 abort
        jitter_sleep(1.5, 3.0)  # 쿼리 간 jitter — rate limit 회피
    return out


def fetch_instagram_via_bing(queries, per_query=10, seen_shortcodes=None):
    """Bing HTML 검색 — DDG가 못 잡은 IG reel URL 추가 수집.
    site:instagram.com/reel 쿼리 + 한국어/영어 키워드 혼용.
    """
    out = []
    seen = seen_shortcodes if seen_shortcodes is not None else set()
    for q in queries:
        try:
            url = f"https://www.bing.com/search?q={urllib.parse.quote('site:instagram.com/reel ' + q)}&count=30"
            req = urllib.request.Request(url, headers={
                "User-Agent": ua(),
                "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
            })
            with urllib.request.urlopen(req, timeout=12) as r:
                html = r.read().decode("utf-8", errors="ignore")
            shortcodes = re.findall(r'instagram\.com/reel/([A-Za-z0-9_-]+)', html)
            count = 0
            for sc in shortcodes:
                if sc in seen: continue
                seen.add(sc)
                out.append({
                    "instagramShortcode": sc,
                    "platform": "Reels",
                    "country": "KR",
                    "title": "",
                    "channel": "bing_curated",
                    "_source": f"bing_search:{q[:30]}",
                })
                count += 1
                if count >= per_query: break
            sys.stderr.write(f"[IG-Bing '{q[:20]}'] {count}개\n")
        except Exception as e:
            sys.stderr.write(f"[IG-Bing '{q[:20]}'] {e}\n")
        jitter_sleep(2.0, 4.5)
    return out


# IG 검색 키워드 — 매일 cron에서 카테고리별로 검색해 새 영상 자동 발견
# 목표: 매일 300+ 신규 IG reel. DDG가 유일한 무료 자동 경로(Bing/Brave 차단 확인).
# 카테고리당 변형 쿼리 다양화 + 영문 키워드 추가로 중복도 낮춤.
IG_DDG_QUERIES = [
    # 뷰티 (한국 브랜드·제품·인기 키워드)
    "올리브영 추천", "올리브영 신상", "올리브영 핫템", "다이소 화장품",
    "메이크업 추천", "메이크업 꿀팁", "스킨케어 루틴", "여름 스킨케어",
    "선크림 추천", "마스카라 추천", "립스틱 추천", "쿠션 추천",
    "토너패드 추천", "앰플 추천", "세럼 추천", "보디로션 추천",
    "립밤 추천", "코스알엑스 리뷰", "아누아 리뷰", "라네즈 신상",
    "메디큐브 추천", "다이소 뷰티템", "올영 신상템", "올영 베스트",
    "헤어 염색", "셀프 네일", "글로우 메이크업", "쿨톤 메이크업",
    # D2C·패션
    "무신사 코디", "무신사 추천", "29cm 추천", "데일리룩", "여름룩 추천",
    "스트리트 코디", "오피스룩", "데이트룩", "남자코디", "여자코디",
    "맨투맨 코디", "원피스 추천", "스니커즈 추천", "셋업 코디",
    "여름 패션", "장마철 패션", "오프숄더", "린넨 코디", "여름 원피스",
    "남자 옷 추천", "여자 옷 추천",
    # 의료·미용
    "피부과 시술", "치과 후기", "성형 후기", "보톡스 후기", "리프팅 후기",
    "탈모 치료", "필러 후기", "임플란트 후기", "여드름 치료", "한의원 정보",
    "비포애프터 시술", "치아미백 후기", "쌍수 후기", "코수술 후기",
    "지방흡입 후기", "스킨부스터 후기", "리쥬란 후기", "프락셀 후기",
    # 펫·동물
    "강아지 훈련", "고양이 건강", "동물병원", "반려견 일상",
    "댕댕이 일상", "냥이 일상", "강형욱 훈련", "수의사 상담",
    "강아지 사료 추천", "고양이 사료 추천", "강아지 미용", "강아지 산책",
    "반려묘 케어", "강아지 영양제", "펫 호텔",
    # 자영업·창업
    "카페창업", "자영업 매출", "1인사장", "프랜차이즈 창업",
    "매장 인테리어", "장사 꿀팁", "프랜차이즈 본사", "외식업 노하우",
    "베이커리 창업", "분식집 창업", "치킨집 창업", "스튜디오 창업",
    "공방 창업", "사장님 일상", "월매출 공개", "점주 일상",
    # F&B·먹방
    "맛집 추천 서울", "맛집 추천 부산", "먹방 한국", "디저트 카페",
    "베이커리 추천", "분식 맛집", "치킨 맛집", "한식 맛집",
    "와인바 추천", "오마카세", "쯔양 먹방", "햄지 먹방",
    "감성카페", "성수동 맛집", "압구정 맛집", "홍대 맛집",
    "도쿄 맛집", "오사카 맛집",
    # 피트니스
    "다이어트 홈트", "필라테스 자세", "복근 운동", "유산소",
    "스쿼트 자세", "데드리프트 자세", "체형 교정", "요가 자세",
    "다이어트 식단", "여름 다이어트", "PT 후기", "헬스 루틴",
    "골반 교정", "굽은 등 교정", "코어 운동",
    # 교육
    "토익 공부", "영어공부 vlog", "공시 합격", "한국사 강의",
    "수능 영어", "오픽 공부", "독학 vlog", "공부 자극",
    "스터디 vlog", "공부 루틴",
    # 영문 (K-beauty US, 글로벌 fashion)
    "kbeauty review", "korean skincare routine", "olive young haul",
    "cosrx review", "anua review", "laneige review",
    "makeup tutorial", "skincare routine", "lip combo",
    "outfit of the day", "ootd haul", "summer fashion",
    "dog training tips", "vet advice", "pet care",
    "dermatologist tips", "dental care", "cosmetic procedure",
    "cafe vlog korea", "food review seoul", "small business tips",
    "home workout", "pilates routine", "weight loss tips",
    # 검증된 IG 본업 계정 + reel — DDG가 계정 페이지 결과 더 잘 찾음
    "olive_young_official reel", "musinsa reel", "29cm_official reel",
    "marketkurly_official reel", "kream.co.kr reel",
    "amorepacific_official reel", "innisfree_official reel",
    "cosrx_official reel", "anua_official reel", "laneige_kr reel",
    "starbuckskorea reel", "burgerking_kr reel", "paris_baguette_kr reel",
    "stylenanda_official reel", "8seconds_official reel", "uniqlo_kr reel",
    "daiso.kr reel", "kakaostyle reel",
    # === KR Reels 부족분 추가 (사장님 요구 -386 충당) ===
    # 한국 본업 카테고리 long-tail
    "다이소 핫템", "다이소 신상", "다이소 추천템", "다이소 화장품",
    "올영 베스트", "올영 새벽배송", "올영 매대", "올영 추천템",
    "쿠팡 핫딜", "쿠팡 추천", "쿠팡 와우", "쿠팡 베스트",
    "무신사 매대", "무신사 베스트", "무신사 단독상품", "무신사 신상",
    "여름 화장법", "장마 메이크업", "여름 패션 코디", "장마 코디",
    "휴가 메이크업", "데일리 메이크업", "출근 메이크업", "데이트 메이크업",
    "여드름 후기", "기미 후기", "트러블 후기", "민감성 피부 추천",
    "주름 관리", "탄력 관리", "리프팅 시술 후기", "안티에이징",
    "병원 후기 영상", "성형 일기", "쌍수 결과", "코수술 결과",
    "강아지 사회화", "강아지 분리불안", "고양이 행동 교정", "수의사 추천",
    "동네 카페 추천", "신상 카페", "감성 카페 인테리어", "디저트 가게",
    "골목식당", "맛집 베스트", "혼밥 추천", "도시락 추천",
    "다이어트 도시락", "단백질 식단", "저칼로리 레시피", "키토 식단",
    "필라테스 효과", "요가 자세", "홈트 30일 챌린지", "유산소 추천",
    "공시 합격 후기", "한국사 강의 추천", "수능 강사 추천", "토익 만점",
    "주식 초보", "재테크 시작", "월급 모으기", "월급 받는 법",
    "퇴근 후 일상", "직장인 일상", "프리랜서 일상", "1인 사업 일상",
    # Reddit 서브레딧 매핑 (영문)
    "korean food asmr reel", "korean girl makeup reel", "korean grocery haul reel",
    "korean cafe reel", "seoul vlog reel", "k-style fashion reel",
    "kpop dance cover reel", "korean drama reaction reel",
]


def fetch_instagram_trending():
    """Instagram reels — 한국 인기 계정 50+ 다양화 (목표 500개)
    계정당 10~15개 reel × 50계정 = 500~750개. yt-dlp --flat-playlist
    실패 계정은 instaloader fallback 자동 시도.
    """
    out = []
    failed_accounts = []  # yt-dlp 실패 → instaloader fallback
    # 한국 인기 reels 계정 — 업종 다양화 (커머스·뷰티·F&B·엔터·테크·인플)
    # 한국 본업 영역 IG 계정만 (K-pop·엔터·자동차 제외 — merge가 어차피 거름)
    accounts = [
        # 커머스·리테일 (사장님 외주 직결)
        "olive_young_official", "musinsa.com", "29cm_official", "daiso.kr",
        "kakaostyle", "marketkurly_official", "kream.co.kr", "soldout_official",
        # 뷰티·화장품
        "amorepacific_official", "innisfree_official", "etudeofficial",
        "missha_official", "thefaceshop_official", "cosrx_official",
        "anua_official", "tirtir_korea", "medicube_official", "laneige_kr",
        # 병의원·의료 (의사 인플루언서)
        "dr_oz_korea", "drwoo.skin", "drchungplastic", "kangnamulleyu",
        "dr_simon_skin", "drlee_dental", "yonsei_dentist",
        # 동물병원
        "vet_doctor_korea", "petdoctor_kim", "kbpet_clinic",
        # F&B·맛집
        "starbuckskorea", "mcdonalds_kr", "burgerking_kr", "subwaykorea",
        "domino_kr", "paris_baguette_kr", "twosomeplace", "ediya_official",
        # 패션 D2C (사장님 외주 시장)
        "stylenanda_official", "8seconds_official", "spao_official",
        "topten_korea", "guess_korea", "uniqlo_kr",
        # 피트니스
        "fitness_korea", "pilates_seoul", "fitclub_kr",
        # 자영업·창업
        "smb_consulting_kr", "1in_sajang", "jangsa_master",
    ]
    sys.stderr.write(f"[IG] 인기 계정 {len(accounts)}개 reels feed 수집 시작 (Firefox 쿠키 사용 — IG 로그인 상태면 작동)\n")
    # yt-dlp --cookies-from-browser 옵션: 사용자 Firefox/Chrome IG 로그인 쿠키 자동 사용 → 봇 차단 우회
    # 사용자가 IG 로그인 안 한 상태면 fail → 다음 방법으로 fallback
    BROWSER_COOKIE_OPTS = ["--cookies-from-browser", "firefox"]  # firefox 우선, 안 되면 chrome
    for acc in accounts:
        success = False
        for browser_opts in [["--cookies-from-browser", "firefox"], ["--cookies-from-browser", "chrome"], []]:
            try:
                cmd = [YTDLP, "--flat-playlist", "--playlist-items", "1:12",
                       "--no-warnings", "--quiet"] + browser_opts + [
                       "--print", "%(id)s|%(title)s|%(view_count)s|%(duration)s|%(timestamp)s",
                       f"https://www.instagram.com/{acc}/reels/"]
                proc = subprocess.run(cmd, capture_output=True, timeout=20)
                if proc.returncode == 0 and proc.stdout.strip():
                    success = True
                    break
            except Exception:
                continue
        if not success:
            failed_accounts.append(acc)
            continue
        try:
            for line in proc.stdout.decode().strip().split("\n"):
                if not line: continue
                parts = line.split("|", 4)
                if len(parts) < 5: continue
                sid, title, views, dur, ts = parts
                out.append({
                    "instagramShortcode": sid,
                    "platform": "Reels",
                    "country": "KR",
                    "title": title,
                    "channel": acc,
                    "views": int(views) if views and views.isdigit() else None,
                    "duration": int(float(dur)) if dur and dur != "NA" else None,
                    "uploadDate": _ts_to_date(int(ts)) if ts and ts.isdigit() else None,
                    "_source": f"yt_dlp_ig_{acc}",
                })
        except Exception as e:
            sys.stderr.write(f"[IG {acc}] {e}\n")
            failed_accounts.append(acc)
        jitter_sleep(2.0, 4.5)  # Instagram 봇 차단 가장 엄격 — 긴 jitter
    # yt-dlp 깨진 계정 — instaloader CLI도 45초 timeout 누적되어 실용성 X. 다음 작업에서 IG embed 스크래핑 시도.
    if failed_accounts:
        sys.stderr.write(f"[IG] yt-dlp 실패 {len(failed_accounts)}개. instaloader CLI도 IG 봇 차단으로 timeout 누적 → 이번엔 0개로 종료. 다음 작업에서 IG embed 우회 시도.\n")
    return out


def _ts_to_date(ts):
    if not ts: return None
    try:
        import datetime
        return datetime.datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d")
    except Exception:
        return None


# ============================================================================
# 수집 기준 — 업계 표준 알고리즘 (Engagement Rate + Velocity + Time Decay)
#
# 참고 — 인플루언서 마케팅 분석 표준 (SocialBakers, HypeAuditor, Phlanx):
#   Engagement Rate (ER) = (likes + comments + shares) / views × 100
#   - 0~1%: 평범
#   - 1~3.5%: 평균
#   - 3.5~6%: 좋음 (high engagement)
#   - 6%+: 매우 우수 (viral)
#
# 참고 — Reddit hot / Hacker News 알고리즘 (시간 가중):
#   Hot = (votes - 1) / (hours + 2)^1.8
#   Velocity = views / hours_since_upload
#
# 참고 — tiktok-scraper (GitHub 4.5k★), youtube-trending-scraper:
#   trending = view_count × engagement_rate / time_decay
#
# 절대 컷오프 (이 미만은 무조건 제외):
#   - views < 5000 (1만 미만 영상은 트렌딩이라 부르기 어려움)
#   - likes < 50
#   - 1년 초과 + views < 100000
# ============================================================================

# 절대 컷오프
MIN_VIEWS = 2000   # 사용자 요청 2000+ 매일 — 컷 완화
MIN_LIKES = 20     # 컷 완화 (2000회 매칭)
MIN_VIEWS_OLD = 50_000  # 1년 초과 영상 (merge에서 1년 컷 적용되므로 영향 작음)

# 매출 기여 키워드 (한국·영어)
COMMERCE_KEYWORDS_KO = [
    "매출", "판매", "만원", "억원", "조원", "완판", "품절", "베스트", "1위",
    "추천템", "필템", "득템", "하울", "언박싱", "리뷰", "할인",
    "올영", "올리브영", "다이소", "무신사", "29cm", "29CM",
    "쿠팡", "네이버쇼핑", "스토어", "shop", "구매", "주문",
]
COMMERCE_KEYWORDS_EN = [
    "sales", "revenue", "million", "billion", "viral", "must-buy", "must-have",
    "best of", "top 5", "haul", "review", "unboxing", "shop",
    "amazon", "tiktokshop", "tiktok shop", "yt shopping",
]

# 인증된 한국 대형 브랜드 (채널 권위 +)
AUTHORITY_CHANNELS = {
    "daiso", "daisokr", "daiso_korea", "다이소", "olive_young_official", "olive_young",
    "musinsa", "musinsa.com", "musinsa_official",
    "29cm", "29cm_official",
    "kakao_corp", "samsung", "lg",
    "cjonstyle", "ssg", "coupang", "naver",
    "kbeauty", "korea_unboxed", "trenda.bility",
    # K-pop·엔터
    "hybe", "smtown", "jype", "yg", "blackpink", "newjeans", "ive", "lesserafim",
    "bts.bighitofficial", "twice", "ateez",
    # 게임
    "krafton", "nexon", "ncsoft", "smilegate", "kakaogames",
    # 테크·IT
    "kakao", "naver_official", "samsungelectronics", "lge_official",
}


# 업종 자동 분류 키워드 (영상 제목/캡션·해시태그·채널명 매칭)
INDUSTRY_KEYWORDS = {
    "kpop": ["kpop", "k-pop", "케이팝", "bts", "blackpink", "newjeans", "ive", "aespa", "twice", "tomorrowxtogether", "txt", "stray kids", "skz", "seventeen", "enhypen", "lesserafim", "tws", "riize", "babymonster", "idol"],
    "entertainment": ["예능", "방송", "tv쇼", "드라마", "변호인", "예능제작", "comedy", "vlog", "브이로그", "리액션", "reaction", "리뷰", "review", "challenge", "챌린지"],
    "gaming": ["게임", "gaming", "게임플레이", "에스포츠", "esports", "lol", "롤", "발로란트", "valorant", "오버워치", "pubg", "배그", "마인크래프트", "minecraft", "스팀", "콘솔", "switch"],
    "tech": ["테크", "tech", "ai", "ai추천", "챗gpt", "chatgpt", "스마트폰", "갤럭시", "아이폰", "iphone", "맥북", "macbook", "노트북", "laptop", "review tech", "unboxing tech"],
    "finance": ["주식", "투자", "재테크", "부동산", "코인", "비트코인", "finance", "stocks", "투자자", "월급쟁이"],
    "travel": ["여행", "travel", "호텔", "hotel", "리조트", "공항", "airport", "맛집투어", "vlog 해외"],
    "auto": ["자동차", "car", "테슬라", "tesla", "현대차", "기아", "bmw", "벤츠", "mercedes"],
    "lifestyle": ["일상", "데일리", "daily", "오늘", "vlog 일상", "방정리", "정리", "moving"],
    "beauty": ["뷰티", "화장품", "메이크업", "스킨케어", "kbeauty", "k-beauty", "올리브영", "다이소뷰티", "올영", "립", "lip", "쿠션", "파운데이션", "selca"],
    "d2c": ["d2c", "29cm", "무신사", "musinsa", "패션", "쇼핑몰", "온라인몰", "홈쇼핑", "ssg", "쿠팡", "위메프"],
    "med": ["병의원", "병원", "의원", "피부과", "성형", "강남언니", "치과", "안과", "정형외과", "한방", "한의원"],
    "food": ["맛집", "f&b", "음식", "베이커리", "카페", "디저트", "라떼", "버거", "치킨", "피자", "한식", "양식"],
    "pet": ["반려", "강아지", "고양이", "동물병원", "수의사", "펫", "댕댕이", "냥이", "puppy", "cat", "dog"],
    "fitness": ["헬스", "운동", "다이어트", "pt", "필라테스", "요가", "헬스장", "홈트", "workout", "gym"],
    "smb": ["자영업", "동네맛집", "사장님", "프랜차이즈", "창업", "소상공인"],
    "edu": ["강의", "학원", "인강", "수능", "공부", "에듀", "코딩", "영어", "study"],
    "creator": ["크리에이터", "인플루언서", "유튜버", "틱톡커", "인플", "셀럽"],
}


def classify_industry(v):
    """제목/캡션·해시태그·채널명 기반 업종 자동 분류"""
    text = " ".join([
        (v.get("title") or "").lower(),
        (v.get("channel") or "").lower(),
        " ".join(v.get("tags") or []).lower(),
        " ".join(v.get("categories") or []).lower(),
    ])
    if not text.strip(): return "creator"
    # 가장 많이 매칭된 업종 반환
    scores = {}
    for ind, keywords in INDUSTRY_KEYWORDS.items():
        n = sum(1 for k in keywords if k.lower() in text)
        if n: scores[ind] = n
    if not scores: return "creator"  # default
    return max(scores.items(), key=lambda x: x[1])[0]


# === 차단·로그인 응답 키워드 — 봇 차단 응답이 영상 데이터로 잘못 들어가는 것 방지 ===
BLOCK_PATTERNS = [
    "log in", "login", "sign up", "sign in", "signup",
    "로그인", "회원가입", "가입하기",
    "login required", "로그인 필요", "로그인이 필요",
    "page not found", "페이지를 찾을 수 없",
    "this content isn't available", "콘텐츠를 사용할 수",
    "rate limit", "too many requests",
    "captcha", "verify", "verification",
    "이용약관", "terms of service", "terms of use",
    "video unavailable", "영상을 사용할 수 없",
    "private video", "비공개 영상",
    "이 동영상은 더 이상", "this video is no longer",
]

def is_blocked_response(v):
    """title·channel에 차단·로그인 키워드 있으면 봇 차단 응답으로 간주"""
    t = (v.get("title") or "").lower().strip()
    c = (v.get("channel") or "").lower().strip()
    if not t or t in ("n/a", "na", "null", "none", "-"): return True
    combined = t + " " + c
    return any(p in combined for p in BLOCK_PATTERNS)


def passes_hard_filter(v):
    """절대 컷오프 — 이 미만은 점수 매기지 않고 즉시 제외
    YT-search·channel·instaloader는 views/likes 메타 미수집 → 컷오프 우회. fetched 후 검증.
    """
    if is_blocked_response(v): return False, "차단·로그인 응답 의심"
    views = v.get("views") or 0
    likes = v.get("likes") or 0
    src = v.get("_source") or ""
    # 메타 누락이 본질인 소스 (yt-dlp·instaloader·검색엔진 큐레이션 IG) 모두 컷오프 우회
    META_LESS_SOURCES = ("ytdlp_search", "ytdlp_channel", "ytdlp_fresh", "instaloader",
                          "ddg_search", "reddit_search", "bing_search", "yt_rss")
    if any(s in src for s in META_LESS_SOURCES):
        return True, None
    if views < MIN_VIEWS: return False, f"조회수 {views} < {MIN_VIEWS}"
    if likes < MIN_LIKES: return False, f"좋아요 {likes} < {MIN_LIKES}"
    days_ago = _days_ago(v.get("uploadDate"))
    if days_ago > 365 and views < MIN_VIEWS_OLD:
        return False, f"1년 초과 + 조회수 부족 ({_fmt(views)})"
    return True, None


def balance_platforms(videos, per_platform=80):
    """플랫폼별 cap만 적용 — 각 플랫폼 독립적으로 상위 per_platform개 유지.
    IG가 0이라고 Shorts·TikTok 풀까지 잘리지 않게.
    """
    by_plat = {"Shorts": [], "TikTok": [], "Reels": []}
    for v in videos:
        p = v.get("platform")
        if p in by_plat:
            by_plat[p].append(v)
    # 각 플랫폼 내부에서 점수 높은 순 정렬
    for p in by_plat:
        by_plat[p].sort(key=lambda x: x.get("_score") or 0, reverse=True)
    # 각 플랫폼 독립 cap (다른 플랫폼 양과 무관)
    balanced = []
    for p in by_plat:
        balanced.extend(by_plat[p][:per_platform])
    return balanced


def score_video(v):
    """업계 표준 알고리즘 — Engagement Rate × Velocity × Time Decay
    참고: SocialBakers, HypeAuditor, tiktok-scraper, Reddit hot
    리턴: {score: 0-100, axes: {er, velocity, authority, commerce, freshness}, reasons: []}"""
    axes = {}
    reasons = []
    views = max(v.get("views") or 0, 1)
    likes = v.get("likes") or 0
    comments = v.get("comments") or 0
    shares = v.get("shares") or 0
    saves = v.get("saves") or 0
    caption = (v.get("title") or "").lower()
    channel = (v.get("channel") or "").lower()
    days = max(_days_ago(v.get("uploadDate")), 1)

    # === A. Engagement Rate (0~40점) — 업계 표준 ER ===
    # ER = (likes + comments + shares + saves) / views × 100
    interactions = likes + comments + shares + saves
    er = interactions / views * 100
    if er >= 10: a_er = 40; reasons.append(f"ER {er:.1f}% (viral)")
    elif er >= 6: a_er = 32; reasons.append(f"ER {er:.1f}% (매우 우수)")
    elif er >= 3.5: a_er = 22; reasons.append(f"ER {er:.1f}% (좋음)")
    elif er >= 1: a_er = 12; reasons.append(f"ER {er:.1f}% (평균)")
    else: a_er = 4
    axes["er"] = a_er

    # === B. View Velocity (0~30점) — Reddit/HN 시간 가중 ===
    # velocity = views / days_since_upload (일평균 조회수)
    velocity = views / days
    if velocity >= 1_000_000: a_v = 30; reasons.append(f"일평균 {_fmt(velocity)}회 (초급상승)")
    elif velocity >= 100_000: a_v = 25; reasons.append(f"일평균 {_fmt(velocity)}회 (급상승)")
    elif velocity >= 10_000: a_v = 18; reasons.append(f"일평균 {_fmt(velocity)}회 (떠오름)")
    elif velocity >= 1_000: a_v = 10
    else: a_v = 3
    axes["velocity"] = a_v

    # === C. 채널 권위 (0~15점) — 인증 브랜드 + 팔로워 ===
    a_auth = 0
    ch_lower = channel.replace("@", "").replace(" ", "")
    if any(auth in ch_lower for auth in AUTHORITY_CHANNELS):
        a_auth = 15; reasons.append(f"인증 브랜드 ({channel})")
    elif v.get("channelFollowers", 0) >= 1_000_000:
        a_auth = 12; reasons.append(f"팔로워 {_fmt(v['channelFollowers'])}")
    elif v.get("channelFollowers", 0) >= 100_000:
        a_auth = 8; reasons.append(f"팔로워 {_fmt(v['channelFollowers'])}")
    elif v.get("channelFollowers", 0) >= 10_000:
        a_auth = 4
    axes["authority"] = a_auth

    # === D. 매출·커머스 시그널 (0~15점) ===
    a_com = 0
    cap_kr = sum(1 for k in COMMERCE_KEYWORDS_KO if k in caption)
    cap_en = sum(1 for k in COMMERCE_KEYWORDS_EN if k.lower() in caption)
    if cap_kr + cap_en >= 3: a_com += 10; reasons.append(f"커머스 키워드 {cap_kr+cap_en}개")
    elif cap_kr + cap_en >= 1: a_com += 5
    tags = v.get("tags") or []
    commerce_tags = [t for t in tags if any(k in t.lower() for k in ["shop", "oliveyoung", "musinsa", "29cm", "kbeauty", "haul"])]
    if commerce_tags: a_com += 5; reasons.append(f"커머스 태그 {len(commerce_tags)}개")
    axes["commerce"] = min(a_com, 15)

    # === 시간 감쇠 보너스 (최근일수록 +) ===
    # 7일 이내면 점수 +1.15배, 30일 이내 +1.08배, 90일 이내 +1.03배
    if days <= 7: decay_bonus = 1.15
    elif days <= 30: decay_bonus = 1.08
    elif days <= 90: decay_bonus = 1.03
    else: decay_bonus = 1.0
    axes["freshness"] = round((decay_bonus - 1) * 100, 1)
    if days <= 7: reasons.append(f"신선 ({days}일)")

    total = (axes["er"] + axes["velocity"] + axes["authority"] + axes["commerce"]) * decay_bonus
    return {"score": min(int(round(total)), 100), "axes": axes, "reasons": reasons[:5]}


def _days_ago(date_str):
    if not date_str: return 999
    try:
        import datetime
        d = datetime.datetime.strptime(date_str[:10], "%Y-%m-%d")
        return (datetime.datetime.now() - d).days
    except Exception:
        return 999


def _fmt(n):
    if not n: return "0"
    n = int(n)
    if n >= 1_0000_0000: return f"{n/1_0000_0000:.1f}억"
    if n >= 10000: return f"{n/10000:.1f}만"
    if n >= 1000: return f"{n/1000:.1f}K"
    return str(n)


def apply_quality_filter(videos, min_score=50):
    """3단계 필터링:
    1) 절대 컷오프 (passes_hard_filter) — 쓰레기 영상 즉시 제외
    2) 점수화 (Engagement Rate + Velocity + Authority + Commerce)
    3) 최소 점수 50 미만 제외 (50점 = '좋음' 등급 기준)"""
    rejected_hard = 0
    rejected_score = 0
    scored = []
    for v in videos:
        ok, reason = passes_hard_filter(v)
        if not ok:
            rejected_hard += 1
            continue
        # 업종 자동 분류 (kpop·엔터·게임·테크 등 17개)
        if not v.get("industry"):
            v["industry"] = classify_industry(v)
        result = score_video(v)
        v["_score"] = result["score"]
        v["_axes"] = result["axes"]
        v["_reasons"] = result["reasons"]
        # YT search 영상은 likes·comments 메타 없어 ER 0 → 점수 낮음. min_score 우회 (조회수 통과는 이미 hard_filter에서)
        is_yt_search = "ytdlp_search" in (v.get("_source") or "")
        if result["score"] >= min_score or is_yt_search:
            scored.append(v)
        else:
            rejected_score += 1
    scored.sort(key=lambda x: x["_score"], reverse=True)
    return scored, {"hard": rejected_hard, "score": rejected_score}


def _title_normalize(t):
    """제목 유사도 비교용 — 이모지·해시태그·공백·특수문자 제거"""
    if not t: return ""
    t = re.sub(r"[#@][\w가-힣]+", " ", t.lower())
    t = re.sub(r"[^\w가-힣\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def dedupe_by_title_similarity(videos, threshold=0.88):
    """플랫폼 ID dedupe 후 추가 — 같은 제목·채널 8자 이상 일치하면 중복으로 간주.
    크로스 플랫폼 같은 영상 (예: YT Shorts → TikTok 재업로드) 제거.
    """
    seen_norm = {}  # normalized_title → (idx, channel)
    out = []
    removed = 0
    for v in videos:
        norm = _title_normalize(v.get("title"))
        if len(norm) < 8:
            out.append(v); continue
        # 동일 정규화 + 동일/유사 채널이면 중복
        key = norm[:60]
        if key in seen_norm:
            removed += 1
            continue
        seen_norm[key] = True
        out.append(v)
    if removed:
        sys.stderr.write(f"[dedupe-title] 제목 유사도로 추가 제거 {removed}개\n")
    return out


def write_results(results, path, min_score=50, per_platform=80):
    """검증된 트렌딩 영상만 저장 — 3 플랫폼 균등 배치 적용"""
    # 플랫폼 ID dedupe (1차) — 동일 id 제거
    seen_ids = set()
    pre_dedup = []
    for v in results:
        k = v.get("youtubeId") or v.get("tiktokId") or v.get("instagramShortcode")
        if k and k in seen_ids: continue
        if k: seen_ids.add(k)
        pre_dedup.append(v)
    # 제목 유사도 dedupe (2차) — 크로스 플랫폼 중복 잡기
    results = dedupe_by_title_similarity(pre_dedup)
    filtered, rej = apply_quality_filter(results, min_score=min_score)
    balanced = balance_platforms(filtered, per_platform=per_platform)
    # 균등 배치 후 통계
    from collections import Counter
    plat_counts = Counter(v.get("platform") for v in balanced)
    summary = {
        "total_collected": len(results),
        "passed_filter": len(filtered),
        "balanced_output": len(balanced),
        "platform_distribution": dict(plat_counts),
        "rejected_hard_filter": rej["hard"],
        "rejected_low_score": rej["score"],
        "rejected_by_balance": len(filtered) - len(balanced),
        "filter_threshold": min_score,
        "per_platform_cap": per_platform,
        "criteria": {
            "min_views": MIN_VIEWS,
            "min_likes": MIN_LIKES,
            "min_views_for_old_videos": MIN_VIEWS_OLD,
            "block_pattern_count": len(BLOCK_PATTERNS),
            "scoring": "Engagement Rate(40) + Velocity(30) + Authority(15) + Commerce(15) × Time Decay",
            "safety": "차단·로그인 응답 키워드 감지 시 즉시 제외 + 3 플랫폼 균등 배치",
        },
        "videos": balanced,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"[crawler] 수집 {len(results)}개 → 절대컷오프 제외 {rej['hard']}개 · 점수미달 {rej['score']}개 → 통과 {len(filtered)}개")
    print(f"  기준: 조회수 ≥ {MIN_VIEWS}, 좋아요 ≥ {MIN_LIKES}, 1년 초과 영상은 조회수 ≥ {_fmt(MIN_VIEWS_OLD)}")
    print(f"  점수: Engagement Rate(40) + Velocity(30) + Authority(15) + Commerce(15) × Time Decay(7일내 ×1.15)")
    if filtered:
        print(f"\n  최상위 10건:")
        for v in filtered[:10]:
            print(f"    {v['_score']}점 · {v.get('platform')} · {_fmt(v.get('views'))}회 · {(v.get('title') or '')[:35]} — {','.join(v.get('_reasons',[])[:2])}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--youtube", action="store_true")
    ap.add_argument("--tiktok", action="store_true")
    ap.add_argument("--instagram", action="store_true")
    ap.add_argument("--platform", choices=["youtube", "tiktok", "instagram", "all"], help="GitHub Actions 분산 cron용")
    ap.add_argument("--health-check", action="store_true", help="수집 X, 기존 영상 메타 검증만")
    ap.add_argument("--fast", action="store_true", help="키워드/per_kw 절반으로 빠른 검증 (~12분)")
    ap.add_argument("--hourly", action="store_true", help="IG 시간별 분산 — 무작위 12 쿼리만 (DDG hard limit 우회)")
    ap.add_argument("--output", default=str(ROOT / "trending_results.json"))
    args = ap.parse_args()

    # --platform 옵션 → --youtube/--tiktok/--instagram/--all 매핑
    if args.platform:
        if args.platform == "youtube": args.youtube = True
        elif args.platform == "tiktok": args.tiktok = True
        elif args.platform == "instagram": args.instagram = True
        elif args.platform == "all": args.all = True

    # Health check 모드 — 수집 X, data.js 기존 영상 일부 샘플 검증
    if args.health_check:
        print("[health] 기존 영상 200개 샘플 메타 검증 시작…")
        health = {"checked": 0, "ok": 0, "blocked": 0, "deleted": 0}
        try:
            data_js = open(ROOT / "data.js").read()
            ids = re.findall(r'(youtubeId|tiktokId|instagramShortcode):"([^"]+)"', data_js)[:200]
            for kind, vid in ids:
                health["checked"] += 1
                jitter_sleep(0.3, 1.0)
            health["status"] = "monitor only — full validation not implemented"
        except Exception as e:
            health["error"] = str(e)
        with open(args.output.replace("trending_results", "health_result"), "w") as f:
            json.dump(health, f, ensure_ascii=False, indent=2)
        print(f"[health] {health}")
        return

    yt_key = os.environ.get("YT_API_KEY", "").strip()
    results = []
    if args.all or args.youtube:
        if yt_key:
            print("[YT] Data API v3 trending shorts 수집 시작…")
            try:
                yt = fetch_youtube_trending_shorts(yt_key)
                print(f"[YT-API] {len(yt)}개")
                results.extend(yt)
            except Exception as e:
                sys.stderr.write(f"[YT-API] 수집 실패 ({e})\n")
        else:
            # YT_API_KEY 없으면 yt-dlp ytsearch fallback — 본업 카테고리별 검색 (무료, 봇 차단 위험 적음)
            print(f"[YT-search] YT_API_KEY 없음 → yt-dlp ytsearch로 본업 카테고리별 수집 시작…{' (fast)' if args.fast else ''}")
            try:
                yt = fetch_youtube_via_ytdlp_search(fast=args.fast)
                print(f"[YT-search] {len(yt)}개")
                results.extend(yt)
            except Exception as e:
                sys.stderr.write(f"[YT-search] 수집 실패 ({e})\n")
            # YouTube 채널 RSS — 공개 피드, 차단 없음, 핵심 본업 채널 최신 15개씩
            print(f"[YT-RSS] 본업 채널 RSS 수집 시작 ({len(YT_RSS_HANDLES)}개)…")
            try:
                yr = fetch_youtube_via_rss(YT_RSS_HANDLES)
                print(f"[YT-RSS] {len(yr)}개")
                results.extend(yr)
            except Exception as e:
                sys.stderr.write(f"[YT-RSS] 실패 ({e})\n")
            # scrapetube (Snyk vuln 0, MIT) — yt-dlp ytsearch 보강. 무인증 1쿼리 0.5초.
            print(f"[YT-scrapetube] 검색 보강 시작…")
            try:
                ys = fetch_youtube_via_scrapetube(fast=args.fast)
                print(f"[YT-scrapetube] {len(ys)}개")
                results.extend(ys)
            except Exception as e:
                sys.stderr.write(f"[YT-scrapetube] 실패 ({e})\n")
        # YT 단계 끝 — 부분 저장 (TT/IG 실패해도 YT 결과는 살림)
        if results:
            try:
                write_results(results, args.output, min_score=0, per_platform=500)
                print(f"[partial-save] YT 단계 후 {len(results)}개 저장")
            except Exception as e:
                sys.stderr.write(f"[partial-save] YT 후 저장 실패: {e}\n")
    if args.all or args.tiktok:
        print("[TT] trending 수집 시작…")
        try:
            tt = fetch_tiktok_trending()
            print(f"[TT] {len(tt)}개")
            results.extend(tt)
        except Exception as e:
            sys.stderr.write(f"[TT] 수집 실패 ({e}) — 다음 cron에서 자동 재시도\n")
        # TT 단계 끝 — 부분 저장 (IG 실패해도 YT+TT 결과는 살림)
        if results:
            try:
                write_results(results, args.output, min_score=0, per_platform=500)
                print(f"[partial-save] TT 단계 후 {len(results)}개 저장")
            except Exception as e:
                sys.stderr.write(f"[partial-save] TT 후 저장 실패: {e}\n")
    if args.all or args.instagram:
        # DuckDuckGo HTML 검색 — IG 자동 수집 유일하게 검증된 경로
        # DDG는 시간당 ~5-10건 hard limit → --hourly 모드는 무작위 12 쿼리만 추출
        # 시간성 prefix 추가 — 매일 다른 날짜·주차·달 박아서 검색 결과 다양성 확보 (어제와 같은 결과 stale 회피)
        from datetime import datetime as _dt
        _now = _dt.now()
        _month_kr = f"{_now.month}월"
        _week_kr = ["1째주", "2째주", "3째주", "4째주", "5째주"][min(4, (_now.day - 1) // 7)]
        _time_prefixes = [
            "오늘",          # 매 슬롯 동일
            "이번주",        # 일주일 동일
            _month_kr,       # 월별 다름
            f"{_month_kr} {_week_kr}",  # 주별 다름
            f"{_now.year}년 신상",
            "최신",
            "신상",
        ]
        _augmented = list(IG_DDG_QUERIES)
        # 매 슬롯마다 prefix × base keyword 5개씩 augment (=쿼리 양 +35)
        # 매일 다른 seed — 같은 날 같은 슬롯엔 같은 random sample, 다음날엔 진짜 다른 sample
        import random as _r
        _seed = int(_now.strftime("%Y%m%d")) + _now.hour
        _local_r = _r.Random(_seed)
        _bases = _local_r.sample(IG_DDG_QUERIES, min(5, len(IG_DDG_QUERIES)))
        for _p in _time_prefixes:
            for _b in _bases:
                _augmented.append(f"{_p} {_b}")
        if args.hourly:
            # hourly sample 도 매일·매시간 다른 seed
            queries_to_use = _local_r.sample(_augmented, min(12, len(_augmented)))
            print(f"[IG-DDG hourly] {len(queries_to_use)} 쿼리 (seed={_seed}, 매일 다른 sample)")
        else:
            queries_to_use = _augmented
            print(f"[IG-DDG] {len(queries_to_use)} 쿼리 × 10 URL (시간성 augment + 매일 다른 seed)")
        try:
            ig_ddg = fetch_instagram_via_ddg(queries_to_use, per_query=10)
            print(f"[IG-DDG] {len(ig_ddg)}개 수집")
            results.extend(ig_ddg)
        except Exception as e:
            sys.stderr.write(f"[IG-DDG] 수집 실패 ({e})\n")
        # Reddit JSON API — DDG hard limit 우회용. 55+ 서브레딧 × 1~3 쿼리.
        # hourly 모드에서도 실행 — Reddit "new" 정렬은 시간마다 최신 글 추가되므로 hourly 누적 효과 있음.
        try:
            seen_sc = {v.get("instagramShortcode") for v in results if v.get("instagramShortcode")}
            ig_reddit = fetch_instagram_via_reddit(seen_shortcodes=seen_sc)
            print(f"[IG-Reddit] {len(ig_reddit)}개 수집 (DDG 중복 제외)")
            results.extend(ig_reddit)
        except Exception as e:
            sys.stderr.write(f"[IG-Reddit] 수집 실패 ({e})\n")
    if not (args.all or args.youtube or args.tiktok or args.instagram):
        ap.print_help()
        return

    # === IG video_url 미리 추출 (사장님 IG 즉시 재생 + 사장님 Mac IP 0 hit) ===
    # Actions runner Azure IP에서 IG embed 페이지 fetch → mp4 URL 추출
    # 풀에 _ig_mp4 저장 → 클라이언트가 백엔드 거치지 않고 직접 사용 (TTFB 0초)
    # 만료 시 (1-2시간) 백엔드 ig-stream fallback 자동
    ig_videos = [v for v in results if v.get("platform") == "Reels" and v.get("instagramShortcode") and not v.get("_ig_mp4")]
    if ig_videos:
        print(f"[IG-prefetch] {len(ig_videos)}개 영상 video_url 추출 시작 (사장님 즉시 재생용)")
        extracted = 0
        for v in ig_videos:
            url = f"https://www.instagram.com/reel/{v['instagramShortcode']}/"
            embed_url = f"https://www.instagram.com/reel/{v['instagramShortcode']}/embed/captioned/"
            try:
                req = urllib.request.Request(embed_url, headers={
                    "User-Agent": ua(),
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "en-US,en;q=0.9",
                })
                resp = urllib.request.urlopen(req, timeout=8)
                html = resp.read(96 * 1024).decode("utf-8", errors="ignore")
                m = re.search(r'video_url\\":\\"([^"]+)\\"', html)
                if m:
                    vu = m.group(1).encode().decode("unicode_escape").replace("\\u0026", "&")
                    if vu.startswith("http"):
                        v["_ig_mp4"] = vu
                        v["_ig_mp4_ts"] = int(time.time())
                        extracted += 1
            except Exception:
                pass
            # IG rate limit — 0.5초 sleep (분당 120회 이하, IG 부하 안전)
            time.sleep(0.5)
        print(f"[IG-prefetch] {extracted}/{len(ig_videos)}개 추출 성공 (사장님 즉시 재생)")

    # min_score=0 + per_platform=500 — merge_trending.py에서 본업 매칭·시간 컷오프 적용. 크롤러는 거친 풀만.
    write_results(results, args.output, min_score=0, per_platform=500)
    print(f"\n[total] {len(results)}개 수집 완료")


if __name__ == "__main__":
    main()
