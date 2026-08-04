from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class LLMSettings(BaseSettings):
    chat_llm_base_url: str = Field(..., alias="CHAT_LLM_BASE_URL")
    chat_llm_model: str = Field(..., alias="CHAT_LLM_MODEL")
    chat_llm_api_key: str = Field(..., alias="CHAT_LLM_API_KEY")

    reasoning_llm_base_url: str = Field(..., alias="REASONING_LLM_BASE_URL")
    reasoning_llm_model: str = Field(..., alias="REASONING_LLM_MODEL")
    reasoning_llm_api_key: str = Field(..., alias="REASONING_LLM_API_KEY")

    # .env 파일 지원 및 대소문자 허용 설정
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True
    )
