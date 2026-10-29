# 스프라이트 재생성 절차 (sprite-gen)

런 디렉터리: `/home/ubuntu/syeon-run` (이 프로젝트에선 참조용)

```bash
# 1) sprite-gen 설치
git clone https://github.com/aldegad/sprite-gen
cd sprite-gen && python3 -m venv .venv && .venv/bin/pip install -e .

# 2) 베이스 정리 — 원본 JPG에서 마스코트만 분리 (장식 제거, 흰 몸통 불투명화)
.venv/bin/sprite-gen cutout syeon-src.jpg --out syeon-cutout.png
python isolate_mascot.py   # 실루엣 필(dilate→fill_holes→erode) → syeon-base.png

# 3) 런 준비 — 셀 256, 크로마 cyan(auto), 6 상태
.venv/bin/sprite-gen prepare --out-dir syeon-run --character-id syeon \
  --base-image syeon-base.png --request syeon-request.json

# 4) 상태별 스트립 생성 — prepare가 만든 prompts/<state>.txt 스펙대로
#    원래 경로: sprite-gen gen-set --run-dir syeon-run --provider codex|grok|openai
#    (로그인/API 키 없으면 외부 이미지 모델로 생성해 raw/<state>.png 에 둬도 동일)

# 5) 추출 + 합성 + QA
.venv/bin/sprite-gen extract --run-dir syeon-run
.venv/bin/sprite-gen compose-atlas --run-dir syeon-run
.venv/bin/sprite-gen preview --run-dir syeon-run
.venv/bin/sprite-gen inspect --run-dir syeon-run
```

산출물 `sprite-sheet-alpha.png` + `manifest.json`를 `assets/`에 복사하면 앱이 바로 사용.
상태: idle(4) walk(8) wave(5) sleep(4) drag(4) celebrate(6) jump(6) dance(6) liesleep(6) still(6, 완전 정지).
