# 션의 고민 동작 스프라이트

프롬프트 정리 요청을 처리하는 동안 사용할 `thinking` 상태입니다. 기존 `assets/sprite-sheet-alpha.png`와 `assets/manifest.json`은 수정하지 않았습니다. 별도 폴더의 아틀라스와 매니페스트를 읽을 때만 고민 동작을 추가합니다.

## 사용 자료와 생성 이력

- 원본 션 참조: 기존 `assets/sprite-sheet-alpha.png`의 `idle` 첫 256×256 프레임을 추출해 `assets/thinking/reference.png`로 보존했습니다.
- 모델 생성: 내장 이미지 생성 도구에 위 참조를 전달해 `assets/thinking/source-strip.png`를 생성했습니다. 도구는 모델의 정확한 이름이나 버전을 반환하지 않았습니다.
- 후처리: 오픈소스 [Sprite-Gen](https://github.com/aldegad/sprite-gen) `c39cc4e3efd9eae554a81aa1d3feb4bd9cd355eb`, Apache-2.0 소스를 별도 임시 폴더에 복제해 `prepare → extract → compose-atlas → compose-gif → inspect`를 실행했습니다. 다른 캐릭터 이미지나 프로젝트 코드는 앱에 복사하지 않았습니다. 코드 라이선스와 션 캐릭터의 사용 권한은 별개입니다.
- 실행 가능한 애니메이션: `assets/thinking/sprite-sheet-alpha.png`와 `assets/thinking/manifest.json`. 256×256 프레임 6개, 각 250ms, 반복입니다. `preview.gif`는 확인용이며 `source-strip.png`는 모델의 원본 결과입니다.
- 검증: Sprite-Gen `inspect` 결과 오류 0·경고 0, 6프레임 확인. `sprite-inspect.report.json`에서 임시 폴더의 사용자 경로만 `[local temporary run]`으로 비식별화했습니다. 시트와 캐릭터의 형태는 육안 확인했으며, 이는 실제 Windows 성능이나 캐릭터 권리 승인에 대한 증명이 아닙니다.

### 실제 생성 프롬프트

```text
Create a single horizontal sprite animation strip for the SAME exact desktop pet shown in the reference image. Preserve its recognizable design precisely: small round white bird, one large asymmetric green leaf hair, black mitten-like wings, peach beak, pink cheeks, yellow feet and a red crossbody heart-decorated pouch, matching the reference's simple soft cartoon line art and proportions. Six full-body poses of the same character in one row, evenly spaced with generous transparent gaps, each at identical scale and feet on the same baseline. Animate a subtle thoughtful loop for waiting during prompt optimization: neutral, a small head tilt, wing briefly touches its cheek, thoughtful pause, returns to neutral; make nearby frames only subtly different. Transparent background throughout, each figure fully opaque inside, no text, no lettering, no borders, no shadows, no detached objects, no additional characters or props. Clean edges suitable for automatic sprite extraction.
```

## 재현 절차

Sprite-Gen의 Python 환경과 의존성이 준비되어 있다고 가정합니다. `sprite-request.json`은 이 상태의 파라미터를 담고 있으며, `reference.png`와 `source-strip.png`는 이미 생성된 자료입니다.

```powershell
sprite-gen prepare --out-dir <임시_런_폴더> --character-id syeon --base-image assets/thinking/reference.png --request assets/thinking/sprite-request.json
# 원본 strip을 <임시_런_폴더>/raw/thinking.png에 복사
sprite-gen extract --run-dir <임시_런_폴더>
sprite-gen compose-atlas --run-dir <임시_런_폴더>
sprite-gen compose-gif --run-dir <임시_런_폴더>
sprite-gen inspect --run-dir <임시_런_폴더>
```

이 작업에서 위 명령은 `sprite-gen` 전역 설치 대신 복제한 소스를 `PYTHONPATH`에 둔 Codex 번들 Python의 `python -m sprite_gen.cli`로 실행했습니다. 생성된 아틀라스를 외부 앱에 통합할 때는 중간 `frames/`가 아니라 `compose-atlas`의 최종 시트와 매니페스트를 사용합니다.
