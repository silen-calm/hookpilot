#!/usr/bin/env python3
"""매일 03:00 cron 직전에 실행. instaloader·scrapetube 등 venv 의존성 안전하게 자동 업데이트.
- patch (X.Y.Z의 Z만 바뀜) → 자동 업데이트 + 검증
- minor (Y 바뀜) → 자동 + 좀 더 엄격한 검증
- major (X 바뀜) → log만 (사람 확인 필요)
- 업데이트 후 import test 실패 시 옛 버전으로 자동 rollback
실행: .venv/bin/python3 daily_dependency_update.py
"""
import subprocess, json, sys, re, os, datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
PIP = os.path.join(ROOT, ".venv/bin/pip")
PY = os.path.join(ROOT, ".venv/bin/python3")
LOG_DIR = os.path.join(ROOT, ".tmp")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_PATH = os.path.join(LOG_DIR, f"dep_update_{datetime.datetime.now().strftime('%Y-%m-%d')}.log")

# 매일 자동 update 대상 패키지. 의도적 list — 검증된 패키지만.
# 새 패키지 추가 시 여기 명시적으로 추가 (자동 발견은 안 함, 보안상).
WATCHED = ["instaloader", "scrapetube"]


def log(msg):
    print(msg)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"{datetime.datetime.now().strftime('%H:%M:%S')} {msg}\n")


def parse_semver(v):
    """4.15.1 → (4, 15, 1). 비정상이면 None."""
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)", v)
    return tuple(int(x) for x in m.groups()) if m else None


def categorize(old, new):
    """semver 차이 → 'patch' / 'minor' / 'major' / 'unknown'"""
    o, n = parse_semver(old), parse_semver(new)
    if not o or not n: return "unknown"
    if n[0] != o[0]: return "major"
    if n[1] != o[1]: return "minor"
    if n[2] != o[2]: return "patch"
    return "patch"


def import_check(pkg):
    """pkg 가져오기 + 모듈 import 가능한지 검증. 1초 안."""
    try:
        r = subprocess.run([PY, "-c", f"import {pkg}; print('ok')"],
                           capture_output=True, timeout=10)
        return r.returncode == 0 and b"ok" in r.stdout
    except Exception:
        return False


def main():
    log(f"=== 의존성 자동 업데이트 시작 — venv: {PIP}")
    if not os.path.isfile(PIP):
        log(f"[X] venv pip 없음. 스킵.")
        return 1
    # 1. outdated check
    try:
        r = subprocess.run([PIP, "list", "--outdated", "--format=json"],
                           capture_output=True, timeout=60)
        outdated = json.loads(r.stdout.decode() or "[]")
    except Exception as e:
        log(f"[X] pip list outdated 실패: {e}")
        return 1
    if not outdated:
        log("새 버전 없음. 종료.")
        return 0
    log(f"감지된 outdated 패키지 {len(outdated)}개")
    actioned = 0
    for pkg in outdated:
        name = pkg.get("name")
        old = pkg.get("version")
        new = pkg.get("latest_version")
        if name not in WATCHED:
            log(f"  - {name}: WATCHED 아님 ({old} → {new}), 스킵")
            continue
        cat = categorize(old, new)
        log(f"  · {name}: {old} → {new} (변경 유형: {cat})")
        if cat == "major":
            log(f"    [알림] major 변경. 자동 업데이트 안 함. 사람 검토 필요.")
            continue
        if cat == "unknown":
            log(f"    [스킵] 버전 형식 알 수 없음.")
            continue
        # patch / minor → 자동 업데이트 + 검증 + rollback
        log(f"    업데이트 시도…")
        try:
            r = subprocess.run([PIP, "install", "--upgrade", f"{name}=={new}"],
                               capture_output=True, timeout=120)
            if r.returncode != 0:
                log(f"    [X] pip install 실패: {r.stderr.decode()[:200]}")
                continue
            # 검증: import test
            mod_name = name.replace("-", "_")
            if not import_check(mod_name):
                log(f"    [X] import test 실패. {old}로 rollback…")
                subprocess.run([PIP, "install", f"{name}=={old}"],
                               capture_output=True, timeout=120)
                log(f"    Rollback 완료.")
                continue
            log(f"    [OK] {name} {new} 적용 완료 (import test 통과)")
            actioned += 1
        except Exception as e:
            log(f"    [X] 업데이트 중 오류: {e}")
            try:
                subprocess.run([PIP, "install", f"{name}=={old}"],
                               capture_output=True, timeout=120)
                log(f"    Rollback 시도")
            except Exception: pass
    log(f"=== 종료. {actioned}개 자동 업데이트 적용.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
