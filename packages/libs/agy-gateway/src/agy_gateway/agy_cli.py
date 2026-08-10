"""agy CLI 를 통해 워커를 실행하는 WorkerRunner 구현.

검증된 호출 규약 (2026-08-05 실측):
    agy --model <id> [--effort low|medium|high] --output-format json
        --json-schema <스키마 문자열 또는 경로> --print <프롬프트>
        --dangerously-skip-permissions --disable-slash-commands

    → stdout 에 단일 JSON 엔벨로프:
      {conversation_id, status, response, duration_seconds, num_turns,
       structured_output, json_schema, usage}

주의사항 (실측 근거):
  * `--json-schema` 는 `--output-format json|stream-json` 과 함께만 쓸 수 있다.
  * `structured_output` 이 스키마를 만족하는 dict 로 온다. `response` 에는 모델이
    산문을 덧붙이는 경우가 있으므로 `structured_output` 을 우선 사용한다.
  * 스키마에 enum 을 넣으면 실제로 강제된다. 자유 문자열이면 존재하지 않는 값
    (예: 테마 '금지')이 생성되므로 분류 필드는 반드시 enum 으로 제한한다.
  * 호출 1건당 input 약 20.7k 토큰이 고정 오버헤드다. 건별 호출 대신 배치로 묶어야
    한다 (배치 조립은 프로그램 책임).
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .interface import (
    WorkerResult,
    WorkerRunner,
    WorkerSpec,
    WorkerUnavailableError,
)

DEFAULT_AGY_PATH = r"C:\Users\SYSADMIN\AppData\Local\agy\bin\agy.exe"
# 스키마가 이보다 길면 명령행 대신 임시 파일로 전달한다
SCHEMA_INLINE_LIMIT = 4000
MAX_ERROR_CHARS = 400


def _truncate(text: str, limit: int = MAX_ERROR_CHARS) -> str:
    """오류 메시지에 거대한 프롬프트가 실려 로그를 덮는 것을 막는다."""
    text = (text or "").strip().replace("\n", " ")
    if len(text) <= limit:
        return text
    return f"{text[:limit]}… (총 {len(text)}자, 이하 생략)"


class AgyCliRunner(WorkerRunner):
    def __init__(
        self,
        agy_path: Optional[str] = None,
        usage_log_path: Optional[str] = None,
        max_attempts: int = 2,
    ):
        self.agy_path = agy_path or os.getenv("ANTIGRAVITY_AGENTAPI_EXE", DEFAULT_AGY_PATH)
        self.usage_log_path = usage_log_path or os.getenv(
            "AGY_USAGE_LOG", os.path.join("var", "agy_usage.jsonl")
        )
        self.max_attempts = max(1, max_attempts)

    # ------------------------------------------------------------------ 실행

    def run(
        self,
        spec: WorkerSpec,
        prompt: str,
        schema: Optional[dict] = None,
    ) -> WorkerResult:
        if len(prompt) > spec.max_prompt_chars:
            raise WorkerUnavailableError(
                f"[{spec.name}] 프롬프트가 {len(prompt)}자로 한계 "
                f"{spec.max_prompt_chars}자를 초과했습니다. 배치 크기를 줄이십시오."
            )

        last_error: Optional[str] = None
        for attempt in range(1, self.max_attempts + 1):
            schema_path: Optional[str] = None
            try:
                cmd, schema_path = self._build_command(spec, prompt, schema)
                started = time.monotonic()
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="ignore",
                    timeout=spec.timeout_seconds,
                    startupinfo=self._startupinfo(),
                )
                elapsed = time.monotonic() - started

                if proc.returncode != 0:
                    last_error = (
                        f"exit={proc.returncode} stderr={_truncate(proc.stderr)}"
                    )
                    print(
                        f"[AgyCliRunner] {spec.name}/{spec.model} 실행 실패 "
                        f"(시도 {attempt}/{self.max_attempts}): {last_error}",
                        file=sys.stderr,
                    )
                    continue

                result = self._parse_envelope(spec, proc.stdout, schema, elapsed)
                self._log_usage(result)
                return result

            except subprocess.TimeoutExpired:
                last_error = f"{spec.timeout_seconds}초 타임아웃"
                print(
                    f"[AgyCliRunner] {spec.name}/{spec.model} 타임아웃 "
                    f"(시도 {attempt}/{self.max_attempts})",
                    file=sys.stderr,
                )
            except WorkerUnavailableError as e:
                last_error = _truncate(str(e))
                print(
                    f"[AgyCliRunner] {spec.name}/{spec.model} 응답 문제 "
                    f"(시도 {attempt}/{self.max_attempts}): {last_error}",
                    file=sys.stderr,
                )
            except Exception as e:  # noqa: BLE001 - 마지막 방어선
                last_error = f"{type(e).__name__}: {_truncate(str(e))}"
                print(
                    f"[AgyCliRunner] {spec.name}/{spec.model} 예외 "
                    f"(시도 {attempt}/{self.max_attempts}): {last_error}",
                    file=sys.stderr,
                )
            finally:
                if schema_path:
                    try:
                        os.unlink(schema_path)
                    except OSError:
                        pass

        raise WorkerUnavailableError(
            f"[{spec.name}] 워커 실행이 {self.max_attempts}회 모두 실패했습니다. "
            f"마지막 원인: {last_error}. 기록을 건너뜁니다."
        )

    # -------------------------------------------------------------- 내부 구현

    @staticmethod
    def _startupinfo():
        if os.name != "nt":
            return None
        info = subprocess.STARTUPINFO()
        info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        return info

    def _build_command(
        self,
        spec: WorkerSpec,
        prompt: str,
        schema: Optional[dict],
    ) -> tuple[list, Optional[str]]:
        cmd = [self.agy_path, "--model", spec.model]
        if spec.effort:
            cmd += ["--effort", spec.effort]

        schema_path: Optional[str] = None
        if schema is not None:
            serialized = json.dumps(schema, ensure_ascii=False)
            # --json-schema 는 --output-format json 과 함께만 허용된다
            cmd += ["--output-format", "json"]
            if len(serialized) > SCHEMA_INLINE_LIMIT:
                fd, schema_path = tempfile.mkstemp(suffix=".schema.json", text=True)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(serialized)
                cmd += ["--json-schema", schema_path]
            else:
                cmd += ["--json-schema", serialized]
        else:
            cmd += ["--output-format", "json"]

        cmd += [
            "--print", prompt,
            "--dangerously-skip-permissions",
            "--disable-slash-commands",
        ]
        return cmd, schema_path

    def _parse_envelope(
        self,
        spec: WorkerSpec,
        stdout: str,
        schema: Optional[dict],
        elapsed: float,
    ) -> WorkerResult:
        raw = (stdout or "").strip()
        if not raw:
            raise WorkerUnavailableError("agy 가 빈 출력을 반환했습니다.")

        envelope = self._load_envelope(raw)
        status = str(envelope.get("status", "")).upper()
        if status and status != "SUCCESS":
            raise WorkerUnavailableError(
                f"agy status={status} response={_truncate(str(envelope.get('response')))}"
            )

        response_text = str(envelope.get("response") or "")
        structured: Any = envelope.get("structured_output")

        if schema is not None:
            structured = self._coerce_structured(structured, response_text)
            if structured is None:
                raise WorkerUnavailableError(
                    "구조화 출력을 얻지 못했습니다. "
                    f"response={_truncate(response_text)}"
                )
        elif structured is None:
            structured = response_text

        return WorkerResult(
            structured=structured,
            raw_response=response_text,
            usage=dict(envelope.get("usage") or {}),
            duration_seconds=float(envelope.get("duration_seconds") or elapsed),
            num_turns=int(envelope.get("num_turns") or 0),
            conversation_id=str(envelope.get("conversation_id") or ""),
            model=spec.model,
            worker=spec.name,
        )

    @staticmethod
    def _load_envelope(raw: str) -> Dict[str, Any]:
        try:
            envelope = json.loads(raw)
        except json.JSONDecodeError:
            # 안내 문구가 섞여 나오는 경우를 대비해 마지막 JSON 객체를 찾는다
            start = raw.find("{")
            end = raw.rfind("}")
            if start == -1 or end <= start:
                raise WorkerUnavailableError(
                    f"agy 출력을 JSON 으로 해석할 수 없습니다: {_truncate(raw)}"
                )
            try:
                envelope = json.loads(raw[start : end + 1])
            except json.JSONDecodeError as e:
                raise WorkerUnavailableError(
                    f"agy 엔벨로프 파싱 실패: {e}. 출력={_truncate(raw)}"
                ) from e
        if not isinstance(envelope, dict):
            raise WorkerUnavailableError(
                f"agy 엔벨로프가 객체가 아닙니다: {_truncate(raw)}"
            )
        return envelope

    @staticmethod
    def _coerce_structured(structured: Any, response_text: str) -> Optional[Any]:
        """structured_output 을 우선 사용하고, 없으면 response 에서 JSON 을 회수한다."""
        if isinstance(structured, (dict, list)):
            return structured
        if isinstance(structured, str) and structured.strip():
            try:
                return json.loads(structured)
            except json.JSONDecodeError:
                pass

        # 모델이 산문을 덧붙인 경우: 본문에서 가장 바깥 JSON 객체/배열을 추출
        text = response_text or ""
        for opener, closer in (("{", "}"), ("[", "]")):
            start = text.find(opener)
            end = text.rfind(closer)
            if start != -1 and end > start:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    continue
        return None

    def _log_usage(self, result: WorkerResult) -> None:
        """토큰 사용량을 JSONL 로 남긴다 (최적화 효과 측정 근거)."""
        if not self.usage_log_path:
            return
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "worker": result.worker,
            "model": result.model,
            "num_turns": result.num_turns,
            "duration_seconds": round(result.duration_seconds, 2),
            **{k: v for k, v in result.usage.items()},
        }
        try:
            directory = os.path.dirname(self.usage_log_path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(self.usage_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as e:
            print(f"[AgyCliRunner] 사용량 기록 실패: {e}", file=sys.stderr)
