# 토론 중 AI: 쟁점 정리와 작성 코치

요약(API 43)과 같은 Gemini 호출·비용 한도(`AI_SUMMARY_MONTHLY_BUDGET_USD`)를 함께 쓴다. 두 기능 모두 승패나 우열을 말하지 않는다.

## 1. 쟁점 정리 — `GET /v1/debates/:debateId/issues` (비회원)

끝장토론에서 찬반이 무엇을 두고 갈리는지 쟁점별로 묶는다. 한쪽만 이야기한 쟁점은 `awaiting`에 아직 답하지 않은 쪽을 담는다.

```json
{"data": {"issues": [
  {"title": "학습 효과",
   "pro": {"text": "…라는 의견이 있다.", "sources": [{"postId": "12", "rootPostId": null}]},
   "con": {"text": "…라는 의견이 있다.", "sources": [{"postId": "18", "rootPostId": "12"}]},
   "awaiting": null},
  {"title": "비용 부담", "pro": {"text": "…", "sources": [...]}, "con": null, "awaiting": "CON"}
], "createdAt": "2026-11-25T03:00:00Z"}}
```

- 만드는 시점: Celery beat `refresh_ai_summaries` 안에서 진행 중인 끝장토론을 30분마다 확인한다. 공개 글이 8개 이상이고 입력 글 구성이 바뀌었을 때만 Gemini를 부른다. 종료된 끝장토론은 종료 직후 한 번 더 확인한다. 간단토론은 만들지 않는다.
- 입력: 찬성·반대 원 의견 공감 상위 12개씩, 의견마다 반박 2개. 반박은 쓴 사람의 입장을 함께 넘긴다.
- 검사(`discussions/ai/issues.py: clean_issues`): 근거 id는 입력에 있고 쓴 사람의 입장이 그 쪽과 같아야 남는다. 판정 표현이 있으면 결과 전체를 버린다. 근거가 하나도 없는 쪽은 null, 양쪽 다 없으면 쟁점을 뺀다.
- 읽을 때 숨김·삭제된 근거는 빼고, 그 결과 근거가 없어진 쪽은 null로 내린다.
- 저장: `ai_issue_maps`(debate, issues JSON, model, created_at). 마이그레이션 `discussions/0004_ai_issue_maps`.
- 서버가 Gemini에 닿지 못하는 환경(dev)에서는 PC에서 만든 결과를 `discussions.ai.issues.save_issue_map(debate_id, raw_json)`으로 넣는다. 서버와 같은 검사를 거친다.

## 2. 작성 코치 — `POST /v1/debates/:debateId/posts/coach` (참여자)

본문 `{"content": "...", "parentId": "12"?}`. 글을 저장하지 않는다.

```json
{"data": {"verdict": "SUGGEST", "reasons": ["ATTACK"], "suggestion": "바꿔 쓴 문장"}}
```

- `verdict`: `OK`(그대로 올려도 됨), `SUGGEST`(바꿔 쓴 제안 있음), `UNAVAILABLE`(코치를 쓸 수 없음 — 화면은 그대로 올린다).
- `reasons`: `ATTACK`(사람을 깎아내림), `MOCKING`(비꼼·조롱), `GENERALIZING`(반대편 전체 낙인), `HOSTILE`(날 선 말투).
- 강한 주장, 상대 주장 비판, 맞춤법·논리는 지적하지 않는다. 제안은 입장·주장·근거를 바꾸지 않는다.
- 쓸 수 없는 제안(빈 문장, 초안과 같음, 지나치게 김, 판정 표현을 새로 넣음)은 `OK`로 바꾼다.
- 제안은 다시 쓴 글이 아니라 고친 글이어야 한다. 제안 글의 글자 쌍 중 초안·반박 대상에 있던 비율이 0.5 미만이거나(내용 덧붙임), 반말·존댓말이 바뀌면 보여 주지 않는다. 2026-11-25 Gemini 시험에서 덧붙여 다시 쓴 제안은 0.12~0.38, 문제 표현만 고친 제안은 0.58~0.78이었다.
- 코치는 글쓰기를 막지 않는다. 꺼져 있음, 키 없음, 1분 6회 초과, 비용 한도, 6초 안에 답 없음, 오류는 모두 `UNAVAILABLE`(200)이다. 금지어 차단은 지금처럼 글 등록 API가 맡는다.
- 설정: `AI_COACH_ENABLED`(기본 false), `AI_COACH_TIMEOUT_SECONDS`(기본 6). 서버에서 `generativelanguage.googleapis.com`으로 나가는 HTTPS가 열려 있어야 한다.

## 근거

- 작성 코치: Argyle et al., PNAS 2023 — 바꿔 쓰기 제안은 3분의 2가 채택됐고, 대화 상대가 느낀 대화 품질이 올라갔으며 입장은 바뀌지 않았다. Katsaros et al., ICWSM 2022 — 올리기 전 다시 생각하기 안내로 공격적인 글이 6% 줄었다.
- 쟁점 정리: Tessler et al., Science 2024 — AI가 쓴 정리문을 사람 중재자 것보다 선호했고, 다수 입장과 소수 의견을 함께 담은 정리가 지지를 받았다.
