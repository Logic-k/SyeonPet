# 보안 경고 없이 배포하기 + 경량화 리서치

## 1. "보안 위협" 경고가 뜨는 이유

SyeonPet.exe가 겪을 수 있는 경고는 두 종류:

| 경고 | 원인 | 출처 |
|---|---|---|
| **SmartScreen** "Windows에서 PC를 보호했습니다" | exe가 미서명 + 다운로드 평판 없음(MOTW — 인터넷에서 받은 파일 표시) | Microsoft Defender SmartScreen |
| **백신(Defender/알약/V3 등) 오진** | "Python을 내장한 exe"가 패킹된 실행파일과 구조가 비슷 → 휴리스틱 탐지 | AV 벤더 휴리스틱 |

### 해결 방법(효과 순)

**① 코드사이닝 인증서 (정식 배포의 정답)**
- OV 인증서(~$100-300/년, 개인은 Sectigo/Certum이 가장 저렴)로 서명 → "게시자: 확인됨" 표시 + **평판이 인증서에 누적**돼 다음 버전부터 경고 감소
- 참고: 과거엔 EV 인증서가 즉시 평판을 줬지만 **2023년 이후 폐지** — 지금은 OV/EV 모두 평판 누적이 필요. 대신 EV는 윈도우11 "Smart App Control" 등 엄격 모드에서 유리
- 서명 명령: `signtool sign /fd sha256 /tr http://timestamp.digicert.com /td sha256 /f cert.pfx /p 암호 SyeonPet.exe`
- 개인 무료 대안: Certum 오픈소스 인증서(오픈소스 프로젝트면 할인/무료)

**② Microsoft에 파일 제출(무료, 평판 가속)**
- https://www.microsoft.com/en-us/wdsi/filesubmission 에서 exe를 "소프트웨어 개발자"로 제출 → 무결점 확인되면 평판에 반영
- 배포 전 VirusTotal 업로드로 어떤 백신이 오진하는지 확인 → 해당 벤더 오진 신고 포털에 제출(모든 벤더에 false-positive 신고 양식 있음)

**③ Microsoft Store / MSIX 배포**
- 스토어 배포는 MS가 서명 → SmartScreen 경고 0. 개발자 계정 $19(개인) 1회. 사이드로드용 MSIX + 자체서명은 오히려 경고 유발(체인 불신)이라 비추천

**④ 사용자 안내(지금 당장 쓸 수 있는 것)**
- 경고창에서 "추가 정보" → "실행" 클릭. README에 스크린샷과 함께 안내하는 게 일반적
- 우리 exe는 이미 버전 정보/아이콘 있음 — 아이콘·버전 정보 있는 exe가 없는 exe보다 경고 빈도 낮음

**하면 안 되는 것**
- UPX 등으로 exe 압축 → 오히려 오진 증가
- 자체 서명 인증서 → SmartScreen에서 미서명과 동일 취급
- exe를 zip 없이 bare 배포 → MOTW가 더 강하게 걸림. zip 안에 넣어 배포하는 게 약간 나음

## 2. 경량화

### 현재 구성 (작업 전 232MB → 작업 후 ~71MB)

| 항목 | 작업 전 | 작업 후 | 비고 |
|---|---|---|---|
| PySide6 | 187.6MB | ~46MB | 70+개 미사용 모듈(QtWebEngine/Qt3D/QtQuick…) 제거, Core/Gui/Widgets만 유지 |
| └ translations | 59.6MB | 0 | Qt 내장 다이얼로그 번역 — 미사용 |
| └ opengl32sw.dll | 19.7MB | 0 | 소프트웨어 GL — QWidget 래스터라 불필요 |
| └ avcodec/avformat 등 | ~30MB | 0 | 멀티미디어 코덱 — 미사용 |
| └ metatypes | 15MB | <1MB | Core/Gui/Widgets 3개만 |
| pip + PyInstaller | ~11MB | 0 | 실행에 불필요 |
| libcrypto/libssl/sqlite | ~7MB | 0 | 네트워크/DB 미사용 |
| Python 런타임 | ~19MB | ~19MB | python312.dll + zip + 필수 pyd |
| assets | 2.5MB | 2.5MB | 스프라이트 시트 |
| **합계** | **232MB** | **~71MB** | zip 기준 77MB → **~30MB 예상** |

### 추가로 더 줄이려면 (트레이드오프 있음)

- **네이티브 재작성** (Rust+C++/C# WinForms): exe 하나에 5-15MB. Qt/PySide 없이 Win32 GDI+로 스프라이트 그리기 — 완전 재작성 필요, 반나절~하루 작업
- **PyInstaller 단일파일**: DLL을 압축해 ~40-50MB 단일 exe 가능하지만 ① 오진율이 가장 높은 배포 형태 ② 실행 시 temp 폴더에 풀림(시작 느림)
- **Nuitka 컴파일**: PySide6 지원, 비슷한 용량이지만 진짜 컴파일이라 해석기 없음 — 빌드 시간 김
- **스프라이트 시트 최적화**: PNG 2048×2560 → 8bit 팔레트화 시 ~1MB 절약 가능하지만 미미

**결론**: Qt 기반 데스크탑 앱의 현실적 바닥이 ~60-70MB(압축 시 ~30MB) — 지금 상태가 거의 최적. 더 줄이려면 네이티브 재작성 뿐.

### 추천 로드맵
1. 지금: 슬림 패키지 배포 + README에 "추가 정보 → 실행" 안내
2. 사용자 늘면: OV 코드사이닝 구매 + Microsoft 제출 → 평판 누적
3. 규모 커지면: MSIX/Store 등록 검토
