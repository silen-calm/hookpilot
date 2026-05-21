# shortform — 매출 직결 영상 사령탑

1인 디자이너 출신 한국 1인 사업자가 클라이언트 매출 견인용 숏폼 외주를 만들 때 참고할 영상만 골라 보여주는 도구. 조회수 많은 영상이 아니라 매출 직결 영상만.

대상 업종 (사장님 외주 영역) 8가지: 뷰티·D2C·병의원·동물병원·자영업·F&B·피트니스·교육

플랫폼 3가지: TikTok · YouTube Shorts · Instagram Reels

## 1. 폴더 구조

- hookpilot.html — 메인 SPA (작업용 소스. 약 230KB)
- simple.html, simple-fresh.html — 백엔드가 서빙하는 사본 (hookpilot.html 동기화본)
- crawler.py — 영상 자동 수집 (TikTok·YouTube·Instagram)
- merge_trending.py — 수집 결과를 본업 매칭·시간대별 컷오프 후 trending_videos.js로 통합
- download_server.py — 백엔드 (포트 8889) + 매일 03:00 자동 cron 내장
- data.js — 수동 검증된 영상 1000개+ (이전 세션에서 큐레이션)
- trending_videos.js — 매일 자동 갱신되는 본업 매칭 풀 (현재 996개)
- trending_results_*.json — 매일 크롤 결과 누적 (1년 이상 영상은 자동 제외)
- wds.css — WDS Montage 디자인 시스템 (한국 Wanted Lab 오픈소스)
- com.hookpilot.daily.plist — launchd 자동 cron 등록용 (백업)

## 2. 실행 방법 (mac)

한 줄 실행:
```bash
python3 /Users/vx/ai-claude/shortform/download_server.py &
```

브라우저:
```
http://localhost:8889/simple.html
```

매일 03:00에 백엔드가 자동으로 crawler + merge 실행 → 새 영상 풀에 누적 → 사용자 다음 방문 시 자동 새로고침.

mac 재부팅 시 자동 시작하려면 (한 번만):
```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.hookpilot.downloader.plist
```

## 3. 점수 공식 (100점)

영상마다 7가지 기준 점수 합산:

- 진짜 팔린 영상인지 25점 (쇼핑 라벨·할인 코드·구매 링크·구매 멘트)
- 사장님 업종에 맞는지 20점 (8업종 매칭, K-pop·B2B 제외)
- 조회수 20점 (1만 → 10만 → 100만 → 1천만 단계별)
- 얼마나 빨리 떴는지 15점 (조회수 ÷ 업로드일)
- 신선도 10점 (일주일 안 10점, 한 달 안 8점)
- 따라 만들기 쉬운지 5점 (단순 후킹일수록 높음)
- 의료광고 안전 5점 (병의원만)

## 4. 영상 컷오프 (자동 제외)

시간대별 조회수 기준:
- 30일 이내: 3000회 미만 제외
- 30~90일: 5000회 미만 제외
- 90~180일: 10000회 미만 제외
- 180~365일: 20000회 미만 제외
- 1년 초과: 모두 제외

플러스 제외 카테고리:
- K-pop·아이돌·연예인
- 게임·웹툰·애니
- 스포츠 경기·UFC·올림픽
- 뉴스·정치·속보
- B2B·기업소개·IPO

## 5. 수집 경로 (모두 무료·무인증)

TikTok — TikWM API (한 해시태그당 5페이지 cursor, 한국·미국 분리 약 160 해시태그)
YouTube — yt-dlp ytsearch + 본업 한국 채널 100+ + Piped 미러 fallback
Instagram — DuckDuckGo HTML 검색으로 reel URL 추출 (yt-dlp Instagram extractor·instaloader 둘 다 봇 차단 확인. DDG HTML이 유일하게 작동)

## 6. 매일 자동 갱신 흐름

1. 매일 03:00 백엔드 내장 cron 자동 실행
2. crawler.py → 3 플랫폼 영상 수집 (약 6000개)
3. merge_trending.py → 본업 매칭 + 시간대별 컷오프 + dedupe → 약 1000개
4. trending_videos.js 갱신 + 1년 이상 옛 영상 자동 폐기
5. 사용자 다음 방문 시 페이지 자동 새로고침

## 7. 백업

```bash
cp -r /Users/vx/ai-claude/shortform ~/Desktop/shortform_backup_$(date +%Y-%m-%d)
```

또는 압축:
```bash
cd /Users/vx/ai-claude && zip -r ~/Desktop/shortform_backup.zip shortform/
```

## 8. 다른 AI에게 넘길 때

폴더 압축해서 GPT 등 다른 AI에게 주면 이어서 작업 가능:
- 코드 자체는 표준 Python·HTML·JS — GPT가 읽고 수정 가능
- 단 GPT는 사용자 mac에서 직접 실행 X — 사용자가 코드 받아 직접 적용

GPT한테 줄 때 추가 안내:
- 핵심 파일은 hookpilot.html, crawler.py, merge_trending.py, download_server.py 네 개
- 데이터 파일은 data.js (검증), trending_videos.js (자동), trending_results_*.json (히스토리)
- 디자인은 WDS Montage 토큰 (wds.css)
- 영상 풀 한계: 인스타는 DDG HTML 검색이 유일한 자동 경로

## 9. 영상 풀 현황 (2026-05-17 기준)

- TikTok 793개 (목표 500/일 도달)
- YouTube Shorts 121개 (목표 500/일 미달, run10에서 키워드 확장)
- Instagram Reels 82개 (DDG 큐레이션) + data.js 검증 351개 = 화면 433개
- 본업 매칭 합계 996개
- 매일 03:00 자동 갱신으로 1주일 누적 시 3플랫폼 각 500+ 도달 예상

## 10. 다음에 할 일 (작업 인계용 메모)

- run10 — DDG IG 자동 수집 적용된 새 코드로 영상 풀 더 채움
- YT Shorts 키워드 36 → 70+로 확장됨 (이미 코드에 적용)
- IG yt-dlp Instagram extractor 깨짐 + instaloader 봇 차단 = DDG HTML이 유일한 무료 자동 경로
- launchd 자동 시작 권한 차단 시 사용자가 한 줄 명령어 수동 실행 필요
