"""agy CLI 를 통해 워커를 실행하는 WorkerRunner 구현.

검증된 호출 규약 (2026-08-05 실측):
    agy --model <id> [--effort low|medium|high] --output-format json
        --json-schema <스키마 문자열 또는 경로> --print <프롬프트>
        --dangerously-skip-permissions --disable-slash-commands

    → stdout 에 단일 JSON 엔벨로프:
      {conversation_id, status, error, response, duration_seconds, num_turns,
       structured_output, json_schema, usage}

계약 변경 (agy 1.1.14, 2026-08-18 실측):
  * 봉투에 `error` 키가 추가됐다.
  * 모델이 첫 시도에서 스키마를 어기면 agy 가 내부적으로 재시도해 바로잡는데,
    **그렇게 성공한 경우에도 status 는 ERROR** 로 남고 error 에 첫 위반이 기록된다.
    즉 status=ERROR 와 structured_output 유효는 동시에 성립한다.
    status 만 보고 버리면 멀쩡한 결과를 잃는다 (2026-08-15~18 배치 나흘 실패).

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


def _failure_detail(returncode: int, stdout: str, stderr: str) -> str:
    """0 이 아닌 종료 코드의 원인을 사람이 읽을 수 있게 만든다.

    **agy 는 실패 원인을 stderr 가 아니라 stdout 의 봉투에 적는다.** 예전에는
    stderr 만 기록해서 로그에 `exit=1 stderr=` 만 남았고, 원인이 보이지 않았다.
    주간리뷰가 3주간 실패하는 동안 진짜 원인(`timeout waiting for response`)이
    내내 stdout 에 있었는데 우리가 버리고 있었다 (2026-08-26 확인).

    봉투를 읽을 수 있으면 error/status 를 앞세우고, 아니면 stdout 원문을 싣는다.
    """
    parts = [f"exit={returncode}"]

    envelope = None
    raw = (stdout or "").strip()
    if raw:
        try:
            candidate = json.loads(raw)
            if isinstance(candidate, dict):
                envelope = candidate
        except json.JSONDecodeError:
            envelope = None

    if envelope is not None:
        status = str(envelope.get("status") or "").strip()
        error = str(envelope.get("error") or "").strip()
        if status:
            parts.append(f"status={status}")
        if error:
            parts.append(f"error={_truncate(error)}")
        if not status and not error:
            parts.append(f"stdout={_truncate(raw)}")
    elif raw:
        parts.append(f"stdout={_truncate(raw)}")

    err = (stderr or "").strip()
    parts.append(f"stderr={_truncate(err)}" if err else "stderr=(없음)")
    return " ".join(parts)


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
                    last_error = _failure_detail(proc.returncode, proc.stdout, proc.stderr)
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
        error_text = str(envelope.get("error") or "").strip()
        response_text = str(envelope.get("response") or "")
        structured: Any = envelope.get("structured_output")

        if schema is not None:
            # agy 1.1.14 부터 봉투에 error 키가 생겼고, 모델이 첫 시도에서 스키마를
            # 어기면 agy 가 **내부 재시도로 바로잡은 뒤에도** status=ERROR 와 첫 위반
            # 내용을 남긴다 (실측: "invalid arguments: at '/reports/0': missing
            # properties 'main_claim', 'risks', 'critical_question'").
            # structured_output 은 정상인데 status 만 보고 버리면 멀쩡한 결과를 잃는다.
            # 2026-08-15~18 배치가 정확히 이것 때문에 나흘간 실패했다.
            #
            # 따라서 스키마를 요구한 호출은 **structured_output 의 유효성**을 판정
            # 기준으로 삼는다. 안전망은 호출자에 있다 — analyzer 가 pydantic 으로
            # 검증하고 원문과 근거를 대조하므로, 엉터리 출력은 그 단계에서 걸린다.
            structured = self._coerce_structured(structured, response_text)
            if structured is None:
                raise WorkerUnavailableError(
                    f"구조화 출력을 얻지 못했습니다. status={status} "
                    f"error={_truncate(error_text)} response={_truncate(response_text)}"
                )
            if status and status != "SUCCESS":
                print(
                    f"[AgyCliRunner] {spec.name}/{spec.model} status={status} 이지만 "
                    "구조화 출력은 확보했습니다 (agy 내부 재시도). "
                    f"error={_truncate(error_text, 200)}",
                    file=sys.stderr,
                )
        else:
            # 스키마가 없으면 판정할 근거가 response 뿐이므로 status 를 그대로 신뢰한다.
            if status and status != "SUCCESS":
                raise WorkerUnavailableError(
                    f"agy status={status} error={_truncate(error_text)} "
                    f"response={_truncate(response_text)}"
                )
            if structured is None:
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
