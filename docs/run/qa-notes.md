# qa-notes — syeon run

Generated rows: internal image-gen tool (gpt-image-2.5-flare), base = cleaned
mascot isolation of the original 부산교육청 마스코트 션 artwork.
Chroma key: cyan #00FFFF (auto-selected — farthest from subject palette).

| state | frames | verdict | notes |
|---|---|---|---|
| idle | 4 | pass | subtle breathing + one blink frame; feet planted |
| walk | 8 | experimental→pass (pet use) | cyclic locomotion is sprite-gen's experimental tier; readable alternating foot contacts + body bob; for a mascot desktop pet the waddle reads correctly at 168px |
| wave | 5 | pass | clear raise→wave→return-to-idle arc |
| sleep | 4 | pass | sitting, head droop, attached Zz bubble kept intact by extraction |
| drag | 4 | pass | dangling mid-air swing reads well for grab state |
| celebrate | 6 | pass | crouch→spring→airborne→land arc; one minor hand-shape variance |

Extract: 6/6 rows produced exact declared frame counts, zero errors/warnings.
inspect: ok, no errors/warnings.
