"""analyzer 설정.

LLM 실행은 agy 워커(`agy_gateway`)로 옮겨갔다. 모델·추론강도·배치크기는
`packages/app/src/autonote/worker_roster.py` 가 단일 출처이며 `AGY_WORKER_<ROLE>_*` 환경변수로
주입한다. 따라서 과거의 `LLMSettings`(CHAT_LLM_* / REASONING_LLM_* 6개 키를 필수로
요구)는 제거했다. 그 키가 없어도 배치가 동작해야 한다.

OpenAI 호환 백엔드가 필요해지면 `packages/libs/llm-gateway` 를 다시 배선하고
여기에 전용 Settings 를 추가할 것 (gemini.md 그라운드 룰 7: 값은 환경변수로).
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AnalyzerSettings(BaseSettings):
    """원문 길이 예산 등 분석 동작 파라미터."""

    max_source_chars: int = Field(2400, alias="ANALYZER_MAX_SOURCE_CHARS")
    max_retries: int = Field(1, alias="ANALYZER_MAX_RETRIES")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )
