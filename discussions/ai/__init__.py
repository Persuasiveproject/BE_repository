"""AI 요약 (FC-DB-220-002·003·006).

흐름: 입력 선별(selection) → 프롬프트(prompt) → LLM 호출(client) → 결과 검사(validate)
→ ai_summaries 저장(service). 주기 실행은 tasks.refresh_ai_summaries가 맡는다.
"""
