import os
import re
import subprocess
import sys
from typing import Type, TypeVar

from openai import OpenAI

from .interface import LLMGateway

T = TypeVar('T')

# agy CLI 폴백 호출 제한 시간 (초). LLM 응답에는 수십 초가 걸릴 수 있으므로
# 하드코딩하지 않고 환경변수로 주입한다 (gemini.md 그라운드 룰 7).
DEFAULT_AGY_TIMEOUT_SECONDS = 120


class LLMUnavailableError(RuntimeError):
    """모든 실제 LLM 경로가 실패했음을 알린다.

    이 예외가 발생하면 호출자는 **어떤 내용도 문서에 기록하지 않고** 중단해야 한다.
    state.json 이 갱신되지 않으므로 다음 배치에서 해당 메시지가 자동 재시도된다.
    원문에 근거하지 않은 내용을 대신 생성해 기록하는 것은 AGENTS.md 절대 규칙 위반이다.
    """


class OpenAILikeGateway(LLMGateway):
    def _agy_timeout(self) -> int:
        raw = os.getenv("ANTIGRAVITY_TIMEOUT_SECONDS")
        if not raw:
            return DEFAULT_AGY_TIMEOUT_SECONDS
        try:
            return max(1, int(raw))
        except ValueError:
            return DEFAULT_AGY_TIMEOUT_SECONDS

    def _call_agy_fallback(self, model: str, messages: list) -> str:
        """agy CLI를 통해 동일 프롬프트를 실제 LLM에 재요청한다.

        실패 시 LLMUnavailableError 를 던진다. 내용을 임의로 생성하지 않는다.
        """
        prompt_parts = []
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "system":
                prompt_parts.append(f"System Instructions:\n{content}")
            elif role == "user":
                prompt_parts.append(f"User Request:\n{content}")
            elif role == "assistant":
                prompt_parts.append(f"Assistant Response:\n{content}")

        prompt = "\n\n".join(prompt_parts)
        agy_path = os.getenv(
            "ANTIGRAVITY_AGENTAPI_EXE",
            r"C:\Users\SYSADMIN\AppData\Local\agy\bin\agy.exe",
        )

        cmd = [
            agy_path,
            "--model", model,
            "--print", prompt,
            "--dangerously-skip-permissions",
        ]

        try:
            startupinfo = None
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                startupinfo=startupinfo,
                timeout=self._agy_timeout(),
                check=True,
            )
            out = proc.stdout.strip()
            if out:
                return out
            raise LLMUnavailableError("agy 폴백이 빈 응답을 반환했습니다.")
        except LLMUnavailableError:
            raise
        except Exception as e:
            raise LLMUnavailableError(f"agy 폴백 호출 실패: {e}") from e

    def request_chat(
        self,
        base_url: str,
        api_key: str,
        model: str,
        messages: list,
        temperature: float = 0.2
    ) -> str:
        try:
            client = OpenAI(base_url=base_url, api_key=api_key)
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            print(
                f"[OpenAILikeGateway] OpenAI API 호출 실패: {e}. agy 폴백을 시도합니다...",
                file=sys.stderr,
            )

        # 실제 LLM 경로만 폴백으로 인정한다. 규칙 기반 대체 텍스트 생성은 하지 않는다.
        try:
            return self._call_agy_fallback(model, messages)
        except LLMUnavailableError as e:
            raise LLMUnavailableError(
                f"모든 LLM 경로가 실패했습니다. 기록을 건너뜁니다. 원인: {e}"
            ) from e

    def request_structured_chat(
        self,
        base_url: str,
        api_key: str,
        model: str,
        messages: list,
        response_format: Type[T],
        temperature: float = 0.2
    ) -> T:
        parsed_result = None
        has_error = False
        try:
            client = OpenAI(base_url=base_url, api_key=api_key)
            response = client.beta.chat.completions.parse(
                model=model,
                messages=messages,
                response_format=response_format,
                temperature=temperature
            )
            parsed_result = response.choices[0].message.parsed
        except Exception as e:
            print(
                f"[OpenAILikeGateway] OpenAI beta.chat.completions.parse 실패: {e}. "
                "agy 폴백을 시도합니다...",
                file=sys.stderr,
            )
            has_error = True

        if has_error:
            messages_copy = messages.copy()
            messages_copy.append({
                "role": "user",
                "content": "반드시 JSON 형식으로만 답변하고 마크다운 백틱(```json)이나 다른 설명은 모두 제외하십시오."
            })
            raw_response = self._call_agy_fallback(model, messages_copy)
            return self._parse_structured_response(raw_response, response_format)

        if parsed_result is None:
            raise ValueError("LLM responded with null or could not be parsed into the expected schema.")
        return parsed_result

    def _parse_structured_response(self, text: str, response_format: Type[T]) -> T:
        json_match = re.search(r'(\{.*\}|\[.*\])', text, re.DOTALL)
        if json_match:
            json_str = json_match.group(0)
        else:
            json_str = text

        try:
            if hasattr(response_format, "model_validate_json"):
                return response_format.model_validate_json(json_str)
            else:
                return response_format.parse_raw(json_str)
        except Exception as e:
            print(
                f"[OpenAILikeGateway] Failed to parse structured JSON: {e}\n"
                f"Raw output was:\n{text}",
                file=sys.stderr,
            )
            raise ValueError(f"Could not parse structured chat response: {e}") from e
