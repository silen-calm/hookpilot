# 사장님 SNS 계정 안전 가이드 — 절대 차단 안 되게

사장님이 같은 크롬으로 SNS 로그인 + hookpilot 사용 시 위험 분석 + 안전 조치.

## 진짜 위험 — 사장님이 본 게 SNS에 추적될 수 있음

같은 크롬 브라우저로:
- 사장님 IG/TT/YT 로그인 → cookies 저장
- hookpilot에서 영상 1000개+ 보면 → iframe / video 태그가 그 cookies 자동 첨부
- 결과: SNS가 사장님 계정으로 "비정상 행동 (1000+ 영상 1초 만에)" 감지 → 봇 의심 → 차단

## 즉시 적용된 안전 조치 (commit 작업중)

### 1. iframe cookies 차단
- **TT iframe**: `referrerpolicy="no-referrer"` + `sandbox="allow-scripts allow-same-origin"` → TT cookies 자동 첨부 차단
- **YT iframe**: youtube-nocookie.com (cookieless 변형, 이미 적용)
- **IG**: iframe 자체 제거 (백엔드 streaming proxy로 변경, cookies X)

### 2. 백엔드 외부 hit 사장님 IP 분리
- crawler (영상 수집): GitHub Actions Azure IP — 사장님 Mac IP 0 노출
- IG video_url 미리 추출: Actions runner — 사장님 Mac 0 hit

### 3. 사장님 Mac이 여전히 hit하는 경우
- 사장님이 모달 열어서 IG 영상 재생 시 — 백엔드 ig-stream proxy → IG CDN
- 다운로드 시 — yt-dlp → TT/IG/YT
- 썸네일 — 백엔드 thumb proxy → 각 CDN
- 메타 — 백엔드 meta → 각 CDN

이 경우 백엔드가 anonymous User-Agent로 hit. 사장님 SNS cookies 0.

## 사장님 추가 안전 행동 (강력 권장)

### A. 시크릿 모드에서 hookpilot 사용 (제일 안전)
1. Chrome 메뉴 → "새 시크릿 창" (Cmd+Shift+N)
2. http://localhost:9000/simple.html 접속
3. 사장님 SNS cookies와 분리됨 → 1000 영상 봐도 SNS 추적 X

### B. 또는 별도 브라우저 (Firefox, Safari)
- hookpilot 전용 브라우저 따로 사용
- SNS는 크롬에서, hookpilot은 Firefox에서

### C. 만약 같은 크롬 일반 모드로 사용해야 하면
- TT/IG/YT 다 **로그아웃** 후 hookpilot 사용
- 사용 끝나면 다시 로그인
- 또는 SNS 로그인 분리 프로필 (Chrome → 새 프로필)

## 만약 차단됐다고 느끼면 즉시 행동

1. hookpilot 사용 잠시 중단 (24-48시간)
2. SNS 로그아웃 후 다시 로그인
3. 사장님 IP가 차단됐을 가능성 → VPN 사용 또는 모바일 데이터로 SNS 사용
4. 차단 풀린 후 시크릿 모드로만 hookpilot 사용

## 데이터로 위험 측정

| 위험 요소 | 현재 상태 | 차단 위험 |
|---|---|---|
| 사장님 SNS cookies 자동 첨부 | iframe sandbox 적용 | 낮음 |
| 사장님 Mac IP가 SNS hit | 백엔드 proxy 시 발생 | 중간 (시크릿 모드 권장) |
| 영상 1000개/day 추적 | crawler는 Actions IP | 낮음 |
| 사장님 동일 크롬 사용 | 시크릿 모드 권장 | 사장님 행동에 따라 |

## 결론

**code 자체는 최대한 안전하게 fix했어요. 하지만 사장님이 같은 크롬 일반 모드로 SNS 로그인 + hookpilot 동시 사용은 위험 0%가 아닙니다.**

**가장 확실한 안전:**
1. 시크릿 모드에서 hookpilot 사용
2. 또는 Firefox/Safari 따로 사용
3. 동일한 크롬 일반 모드 사용은 SNS 로그아웃 후
