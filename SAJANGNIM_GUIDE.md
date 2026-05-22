# hookpilot 사장님 운영 가이드 (Claude 없이)

이 문서는 사장님이 Claude Code 구독 없이도 hookpilot 서비스를 직접 운영할 수 있게 작성됐어요. 즐겨찾기 해두시고 필요할 때 보세요.

## 1. 서비스 진짜 어떻게 돌아가나요?

3개 자동 시스템이 동시에 작동:

A. GitHub Actions (Azure 데이터센터에서 자동 실행)
   - 2시간마다 자동으로 새 영상 수집 (12회/일)
   - 사장님 Mac이 꺼져 있어도 작동
   - Public repo 무제한 (Stripe·카드 0)

B. Mac launchd (사장님 컴퓨터에서 자동 실행)
   - 백엔드 download_server.py 자동 시작
   - 죽으면 자동 재시작 (KeepAlive: true)
   - 5분마다 git pull로 GitHub Actions 결과 자동 동기화

C. 브라우저 autoPoll v2 (사장님 화면 자동 갱신)
   - 60초마다 새 영상 자동 확인
   - 변경 감지 시 사장님 작업 state 보존하면서 자동 reload

→ **사장님이 손 안 대도 매일 새 영상이 풀에 쌓이고 화면에 반영됩니다.**

## 2. 자주 발생하는 문제 + 사장님 직접 fix

### 백엔드 (download_server.py) 죽었을 때
브라우저에서 http://localhost:9000/simple.html 안 열리면:
```bash
# Mac 터미널에서
launchctl kickstart -k gui/$(id -u)/com.hookpilot.downloader
```
1~2초 뒤 다시 열리면 OK.

### GitHub Actions cron 안 돌 때
https://github.com/silen-calm/hookpilot/actions 에서 확인:
- 빨간 X = 실패
- 노란 점 = 진행 중
- 초록 체크 = 성공

수동 실행: "Daily Shortform Crawl" → "Run workflow"

### 풀 갱신 안 될 때
Mac 터미널에서:
```bash
cd /Users/vx/ai-claude/shortform
git pull
python3 merge_trending.py
```

### 사장님 화면이 옛 데이터 보임
브라우저에서 Cmd+Shift+R 한 번. 이후로는 60초 내 자동 갱신.

## 3. 회귀 자동 검증

매 GitHub Actions cron 후 regression_tests.py 자동 실행. 41개 테스트 통과 여부 로그에 남음. 사장님은 actions 페이지에서 확인 가능.

## 4. 모든 fix 이력 (git log)

```bash
cd /Users/vx/ai-claude/shortform
git log --oneline
```

문제 생기면 해당 commit으로 revert:
```bash
git revert <commit-hash>
git push
```

## 5. 진짜 위급 — 모든 게 다 깨졌을 때

```bash
cd /Users/vx/ai-claude/shortform
# 1) 백엔드 강제 재시작
launchctl kickstart -k gui/$(id -u)/com.hookpilot.downloader

# 2) GitHub 마지막 정상 상태로 (사장님 로컬 변경 무시)
git fetch origin
git reset --hard origin/main

# 3) 풀 재계산
python3 merge_trending.py
```

## 6. 사장님 안전 — 절대 차단 안 되는 보장

지금 코드 사용 중:
- TikTok: TikWM third-party API (사장님 TT 계정과 무관)
- YouTube: yt-dlp anonymous (사장님 Google 계정과 무관)
- Instagram: Reddit·DDG 검색만 (사장님 IG 절대 로그인 X)
- Facebook/Threads: 절대 안 건드림

이 정책 바꾸면 안 됨. 사장님 SNS 계정 안전 우선.

## 7. 새로 영상 카테고리 추가하고 싶을 때

`merge_trending.py` 의 `CATEGORIES` 사전에 키워드 추가:
```python
"beauty": ["키워드1", "키워드2", ...]
```
저장 후 commit + push → 다음 cron부터 적용.

## 8. 풀 백업

git이 풀 백업 역할. 모든 trending_videos.js 변경 기록이 https://github.com/silen-calm/hookpilot/commits/main 에.

특정 시점으로 복원:
```bash
git checkout <commit-hash> -- trending_videos.js
git commit -m "풀 복원"
git push
```

## 9. 연락처 / 도움 받기

- 코드 issue: https://github.com/silen-calm/hookpilot/issues
- 로그 위치: /tmp/hookpilot_downloader.log, /tmp/hookpilot_downloader.err
- 백엔드 진단: http://localhost:9000/api/probe (JSON 응답 = 정상)
