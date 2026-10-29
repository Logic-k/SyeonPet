# 션 데스크탑 펫 (Syeon Desktop Pet)

부산교육청 마스코트 **션**이 바탕화면을 돌아다니는 Windows 데스크탑 펫입니다.
신규 사용자 온보딩 모드(화면을 걸어 다니며 단계별 안내)를 지원합니다.

![walk](docs/walk.gif)
![celebrate](docs/celebrate.gif)

## 기능

- **배회(Wander)**: 화면 하단 작업 가능 영역 위를 왔다 갔다 걸어다닙니다.
- **온보딩 투어**: 첫 실행 시 자동 시작(설정 가능). 션이 화면 앵커 포인트를
  걸어다니며 말풍선으로 단계별 안내를 보여줍니다. `다음/◀/건너뛰기` 버튼 지원.
- **드래그 & 물리**: 션을 끌어 이동 → 놓으면 중력 낙하 + 바운스 후 착지.
- **클릭 반응**: 한 번 클릭 → 손 인사(wave), 더블 클릭 → 축하(celebrate).
- **잠(Sleep)**: 오래 가만히 있으면 졸기 시작. 클릭하면 깨어납니다.
- **픽셀 단위 클릭 통과**: 창의 빈 영역은 OS 차원에서 클릭이 그대로 통과
  (`setMask` + 프레임 알파 마스크). 트레이의 "클릭 통과 모드"를 켜면
  션 본체도 통과합니다.
- **점프 & 댄스 & 누워자기**: 더블 클릭 → 축하 또는 춤(랜덤). 배회 중 가끔 점프. 오래 자면 옆으로 누워 깊이 잠듭니다(클릭 시 깨어남).
- **트레이 메뉴**: 온보딩 재시작, 돌아다니기, 재우기, 누워서 재우기, 완전히 가만히 재우기, 축하하기, 춤추기, 점프하기, 차분 모드(천천히), 프레임 블렌딩(크로스페이드)으로 애니메이션 부드럽게 재생, 트레이 "더블클릭 동작" 메뉴와 config.json "click_actions"으로 클릭/더블클릭 동작 변경 가능, 말풍선 표시, 클릭 통과,
  Windows 시작 자동 실행, 종료.
- **멀티모니터**: pet이 위치한 모니터의 작업 영역을 바닥으로 인식합니다.

## Windows에서 실행

### 방법 1 — 소스에서 실행 (Python 필요)
```bat
run.bat
```

### 방법 2 — exe 빌드 (배포용)
```bat
build.bat
```
`dist\SyeonPet.exe`가 생성됩니다. 단일 파일이므로 아무 데나 복사해서 실행하면 됩니다.
트레이 메뉴의 "Windows 시작 시 자동 실행"을 켜면 레지스트리
`HKCU\...\Run\SyeonPet`에 등록됩니다.

## 설정 (`config.json`)

| 키 | 설명 |
|---|---|
| `display_size` | 션 표시 크기(px). 기본 168 |
| `walk_speed` | 걷기 속도(px/s). 기본 190 |
| `start_onboarding_on_launch` | 실행 시 온보딩 자동 시작 여부 |
| `ambient_chatter` | 가끔 말풍선으로 한마디 하는지 여부 |
| `ambient_lines` | 상시 대사 목록 |
| `onboarding_steps` | 온보딩 단계 목록 — `title`, `text`, `anchor`(`left`/`center`/`right`). 이 파일만 편집하면 안내 내용을 자유롭게 바꿀 수 있습니다 |

## 스프라이트 재생성

스프라이트는 [aldegad/sprite-gen](https://github.com/aldegad/sprite-gen)
컴포넌트-로우 파이프라인으로 만들었습니다 (`assets/sprite-sheet-alpha.png`
+ `assets/manifest.json`). 상태별 스트립 원본과 프롬프트는 `qa/`·`prompts/`
가 아닌 생성 런 디렉터리에 있습니다. 재생성 절차는 `docs/SPRITES.md` 참조.

## 구조

```
syeon-pet/
  main.py         진입점: QApplication, 트레이 메뉴, 상시 대사, 자동실행 레지스트리
  pet.py          PetWidget — 프레임 렌더링, setMask 클릭 통과, 드래그+중력 물리
  behavior.py     상태 머신 — 배회/대기/수면/낙하/one-shot 액션
  onboarding.py   온보딩 투어 — 앵커 이동 + 단계별 말풍선
  bubble.py       말풍선 위젯 (펫 창의 자식 — 단일 창 설계)
  config.json     모든 사용자 설정/문구
  assets/         sprite-gen 산출물 (시트 + manifest + 베이스)
  docs/           QA GIF, 리서치, 스프라이트 재생성 문서
```
