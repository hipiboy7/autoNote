"""배치 중복 실행 방지 파일 잠금 (Windows).

문서를 재작성하는 작업(복구·되쓰기·라벨 보정)이 일일 배치와 겹치면, 배치가 방금 기록한
내용이 재작성으로 덮여 유실될 수 있다. 잠금을 **모든 문서 변경 작업이 공유**해야
그 사고를 막을 수 있다. 런북에 "배치 시간대를 피하라" 고 적어두는 것만으로는 부족하다.
"""

import atexit
import os
import sys

try:
    import msvcrt
except ImportError:  # pragma: no cover - Windows 전용 경로
    msvcrt = None

DEFAULT_LOCK_NAME = "batch.lock"
_lock_fd = None


def default_lock_path(state_file: str = "") -> str:
    state_file = state_file or os.getenv("STATE_FILE_PATH", "./var/state.json")
    directory = os.path.dirname(state_file) or "."
    return os.path.join(directory, DEFAULT_LOCK_NAME)


def acquire_batch_lock(lock_path: str) -> bool:
    """배타적 잠금을 시도한다. 이미 다른 프로세스가 보유 중이면 False."""
    global _lock_fd
    if msvcrt is None:
        # 잠금을 쓸 수 없는 환경에서는 막지 않는다 (테스트/리눅스)
        return True
    try:
        _lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT)
        msvcrt.locking(_lock_fd, msvcrt.LK_NBLCK, 1)
        os.lseek(_lock_fd, 0, os.SEEK_SET)
        os.write(_lock_fd, str(os.getpid()).encode())
        atexit.register(release_batch_lock, lock_path)
        return True
    except (IOError, OSError):
        if _lock_fd is not None:
            os.close(_lock_fd)
            _lock_fd = None
        return False


def release_batch_lock(lock_path: str) -> None:
    """잠금 해제 및 잠금 파일 정리."""
    global _lock_fd
    if _lock_fd is not None:
        try:
            os.lseek(_lock_fd, 0, os.SEEK_SET)
            if msvcrt is not None:
                msvcrt.locking(_lock_fd, msvcrt.LK_UNLCK, 1)
            os.close(_lock_fd)
        except Exception:
            pass
        _lock_fd = None
    try:
        os.remove(lock_path)
    except Exception:
        pass


def require_lock(label: str, state_file: str = "") -> bool:
    """잠금을 확보하고 결과를 알린다. 실패 시 호출자는 즉시 중단해야 한다."""
    lock_path = default_lock_path(state_file)
    if not acquire_batch_lock(lock_path):
        print(
            f"[{label}] 다른 배치/문서 작업이 실행 중입니다 (잠금: {lock_path}). "
            "겹쳐 실행하면 기록이 유실될 수 있어 중단합니다.",
            file=sys.stderr,
        )
        return False
    print(f"[{label}] 잠금 획득 완료 (PID: {os.getpid()})")
    return True
