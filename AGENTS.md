# SyeonPet 작업 노트

## 릴리스/업데이트 분업 (2026-11-03 확정)

- **업데이트 서명은 Ed25519 개인키가 있는 노트북에서만 수행한다.**
  - 이 PC(데스크탑): 코드 수정 → 테스트(`pythonw tools/run_tests.py`) →
    `tools/build_release.py` ZIP/`build_setup.py` 설치기 빌드 → 격리 스모크 → GitHub 릴리스 게시
  - 키 보유 노트북: `tools/sign_update.py`로 `update.json`/`update.sig` 생성 → 릴리스에 자산 추가
  - 서명 도구는 앱 번들 `libcrypto-3.dll`을 ctypes로 사용 (OpenSSL 3 설치 불필요, CLI는 폴백)
  - **모든 릴리스에 update.json+sig를 올려야 자동업데이트가 동작** (없으면 조용히 스킵)
  - 자동업데이트 대상: v0.1.6 이후 설치본. v0.1.0~v0.1.5는 업데이터 부재로 수동 설치 필요
- 개인키는 repo/릴리스/패키지/문서/로그에 두지 않는다.

## 검증

- 테스트: `SyeonPet\pythonw.exe tools\run_tests.py` (임베디드 Python 사용, 시스템 Python에는 PySide6 없음)
- 릴리스 스모크: `pythonw tools/smoke_release.py <zip>` — 격리 추출 후 런처 기동 검증
- 빌드 덮어쓰기 금지: 같은 파일명의 기존 산출물은 빌드가 거부 → 미게시 검증물만 삭제 후 재빌드
