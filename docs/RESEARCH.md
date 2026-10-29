# 데스크탑 펫(Windows) 리서치 — "최고의 데스크탑 펫" 만들기

## 1. 기존 제품 지형도

| 제품 | 스택 | 강점 | 약점 |
|---|---|---|---|
| **Shimeji-ee** | Java | 창을 타고 오르기, 번식, 방대한 캐릭터 팩 생태계 | 무겁고 Java 필요, 최신 Windows에서 까다로움 |
| **Desktop Goose** | Unity | "혼란" 컨셉 — 커서 낚아채기, 밈 배달, config 튜닝 | 일부러 업무를 방해하는 게 컨셉이라 실무용으론 부적합 |
| **eSheep** | Win32 | 가볍고 향수 있는 고전 | 기능 최소, 커스터마이즈 불가 |
| **OpenPets** | 다양 | 1,200+ 캐릭터, 방해 없는 개성 | 자체 캐릭터 제작 파이프라인이 핵심 자산 |
| **Pelia** | Rust 네이티브 | per-pixel 알파 + OS 수준 클릭 통과, Win95 말풍선, 멀티모니터 커서 추적 | 최근의 모범 사례 집약체 |
| **UIU desktop-pet** | PyQt6 + Ollama | 알파 마스킹 클릭 통과, 트레이, LLM 말풍선, 드래그 스냅백 | Python 의존 |
| **BelPet** | Godot 4 C# | 게임 엔진으로 펫 구현, 마우스 패스스루 영역, 중력 | 엔진 오버헤드 |

## 2. 윈도우 기술 선택지 비교

| 스택 | 창 방식 | exe 크기 | 특징 |
|---|---|---|---|
| **PySide6/PyQt** | `Frameless` + `WA_TranslucentBackground` + `Tool` + `WindowStaysOnTopHint`, `setMask`/`WS_EX_TRANSPARENT`로 클릭 통과 | PyInstaller ~50MB | 가장 빠른 개발, 커뮤니티 펫 대부분이 이 방식, Python으로 AI/로직 붙이기 쉬움 |
| 네이티브 Win32 (C++/Rust) | `WS_EX_LAYERED` + `UpdateLayeredWindow` (per-pixel 알파) | 수 MB | 최고 성능/최소 용량, 최고 난이도 |
| C# WinForms/WPF | Layered window | 20-60MB | Windows 전용 최적, .NET 편의성 |
| Electron | `transparent: true` frameless BrowserWindow + `setIgnoreMouseEvents` | 150MB+ | 웹 기술 편리하지만 무거움 — 펫엔 과함 |
| Godot 4 | 투명 borderless + mouse passthrough 영역 | ~80MB | 게임 로직엔 좋지만 배터리/용량 아쉬움 |
| Tauri | transparent 윈도우 | ~10MB | 가볍지만 Rust 빌드 필요, 펫 문서 적음 |

**결론: PySide6 + PyInstaller가 최적의 밸런스** — 본 프로젝트 채택.
진짜 최상급(수 MB, per-pixel layer)을 원하면 Rust/Win32 루트로 리라이트 가능하지만,
파이프라인(sprite-gen → manifest → Qt)은 그대로 재사용됩니다.

## 3. "최고의 데스크탑 펫" 체크리스트 (리서치 + 본 구현 반영 여부)

필수 UX:
- ✅ 프레임리스 + 투명 + 항상 위 + 태스크바 아이콘 없음 (Qt.Tool)
- ✅ **투명 픽셀에서 클릭 통과** — setMask로 프레임 알파 영역만 히트. Petel의 핵심 "never gets in your way"
- ✅ 드래그 가능 + 놓으면 중력으로 낙하, 작업표시줄 위 "바닥"에 착지(availableGeometry)
- ✅ 작업표시줄 위를 바닥으로 인식 (screen.availableGeometry().bottom())
- ✅ 멀티모니터 — pet이 어느 화면에 있든 그 화면의 바닥 사용
- ✅ 트레이 메뉴 — 모든 제어는 여기서 (펫 본체엔 UI 최소화)
- ✅ 저렴한 CPU — 프레임 타이머만, 스프라이트 애니메이션 3~10fps
- ✅ 방향에 따라 스프라이트 좌우 반전
- ✅ 랜덤 배회 + 대기 + 수면 등 "살아있는" 자율 행동
- ✅ 말풍선 (프로그램 창 안에 단일 창으로 그림 — 별도 창은 WM 호환성 이슈 있음)
- ✅ Windows 자동 실행 (HKCU Run 레지스트리, 트레이 토글)
- ✅ PyInstaller onefile exe 배포

있으면 좋은 고급 기능 (후속 확장 여지):
- 창 상단 타고 오르기 (Shimeji 시그니처) — EnumWindows로 창 외곽선을 감지해 그 위를 걷기. 구현 복잡도 높음
- 커서 추적/반응, 시스템 이벤트 반응 (타이핑 → Bongo Cat 스타일)
- 스케줄/알림 연동 (회의 5분 전 알려주기) — 온보딩 프레임워크 재사용 가능
- 로컬 LLM 대화 (UIU 사례)
- DPI 인식 (Qt6는 기본 지원)
- 사운드 (honk 대체로 치지직 없음 — 예의 바른 펫은 조용해야)
- 여러 펫 인스턴스/번식
- 테마/의상 스킨 (sprite-gen recolor로 팔레트 스왑 아틀라스 생성 → 런타임 교체 가능)

## 4. 온보딩 펫으로서의 설계 노트

"온보딩하면서 돌아다니는 느낌"의 핵심:
- 펫이 **물리적으로 화면 곳곳을 걸어 이동**하며 각 단계를 설명 → 시선이 펫을 따라다니게 됨 (anchor: left/center/right)
- 각 단계는 짧게, `다음/이전/건너뛰기` 제공, 마지막에 celebrate로 긍정적 마무리
- config.json만 편집하면 콘텐츠 교체 가능 — 실제 온보딩 문구로 교체 추천
- 재시작해도 트레이에서 언제든 다시 실행 가능

## 5. 스프라이트 제작 파이프라인 (aldegad/sprite-gen)

"한 장의 그림 → 게임용 애틀라스" 컴포넌트-로우 파이프라인:
1. `prepare` — 셀 크기/크로마 키/상태 정의 → 상태별 레이아웃 가이드 + 프롬프트 생성
2. 행별 이미지 생성 (codex/grok/openai 프로바이더; 본 세션은 내장 이미지 생성으로 대체 — 동일 계약의 `raw/<state>.png` 스트립 산출)
3. `extract` — 크로마 키 제거(soft-alpha unmix로 안티앨리어스 보존) + 연결 요소로 포즈 분리 + 셀 리핏
4. `compose-atlas` — `sprite-sheet-alpha.png` + `manifest.json`(frame_layout 절대 좌표, fps, loop)
5. QA — 상태별 GIF/contact sheet, `inspect` 자동 검증

이 프로젝트가 manifest를 직접 소비하므로 스프라이트를 다시 굽기만 하면 앱 코드 수정 없이 교체 가능.

## 출처
- sprite-gen: github.com/aldegad/sprite-gen
- 펫 제품 조사: pets-therapy.com/best-desktop-pets-apps, openpets.dev, sp00nz.itch.io/pelia, github.com/UIU-Developers-Hub/desktop-pet, samperson.itch.io/desktop-goose
