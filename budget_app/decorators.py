"""명령 핸들러 공통 데코레이터.

핸들러는 `handler(args) -> int(종료 코드)` 형태이며 `@handle_errors` 가 가장 바깥에 온다.

cli.py 의 모든 cmd_* 함수가 아래처럼 쓴다:

    @handle_errors      # 바깥: 예외를 잡아 메시지 출력 + 종료 코드 반환
    @timed              # 안쪽: --verbose 일 때 실행 시간 출력
    def cmd_add(args): ...

실행 순서: handle_errors.wrapper → timed.wrapper → cmd_add → (반환) timed → handle_errors.
덕분에 명령 함수 안에는 try/except 를 따로 쓰지 않아도 된다.
"""
from __future__ import annotations

import functools
import os
import sys
import time
from typing import Callable

from .errors import BudgetError

Handler = Callable[..., int]  # 명령 함수의 타입: 인자를 받아 종료 코드(int)를 반환


def report_error(message: str, hint: str = "") -> None:
    """'원인 + 해결 힌트'를 stderr에 출력한다.

    출력 형식:
        오류: {message}
        힌트: {hint}      ← hint 가 있을 때만
    """
    print("오류: %s" % message, file=sys.stderr)
    if hint:
        print("힌트: %s" % hint, file=sys.stderr)


def _describe_os_error(exc: OSError):
    """파이썬의 파일 관련 예외(OSError)를 사용자가 이해할 수 있는 (메시지, 힌트) 쌍으로 바꾼다.

    예: FileNotFoundError → ("파일 또는 폴더를 찾을 수 없습니다 (경로)", "경로가 맞는지 확인하세요")
    """
    target = " (%s)" % exc.filename if getattr(exc, "filename", None) else ""
    if isinstance(exc, FileNotFoundError):
        return "파일 또는 폴더를 찾을 수 없습니다%s" % target, "경로가 맞는지 확인하세요"
    if isinstance(exc, PermissionError):
        return (
            "접근 권한이 없습니다%s" % target,
            "파일/폴더 권한을 확인하거나 --data-dir 로 쓰기 가능한 폴더를 지정하세요",
        )
    if isinstance(exc, (IsADirectoryError, NotADirectoryError, FileExistsError)):
        return "경로의 종류가 올바르지 않습니다%s" % target, "파일과 폴더 경로를 구분해서 지정했는지 확인하세요"
    return "파일 입출력 오류: %s%s" % (exc.strerror or exc, target), "디스크 공간과 경로를 확인한 뒤 다시 시도하세요"


def handle_errors(func: Handler) -> Handler:
    """예외를 잡아 stderr에 안내하고 종료 코드를 반환한다. 스택트레이스는 출력하지 않는다.

    잡는 예외와 종료 코드:
        BudgetError (앱이 직접 낸 오류)  → 메시지·힌트 출력, 예외의 exit_code
        KeyboardInterrupt (Ctrl+C)      → "중단되었습니다", 130
        BrokenPipeError (`| head` 등)   → 조용히 0
        EOFError (입력이 끝남)          → 대화형 입력 불가 안내, 2
        OSError (파일 입출력)           → _describe_os_error 로 안내, 1
        ValueError / 그 밖의 예외       → 일반 안내, 1
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs) -> int:
        try:
            return func(*args, **kwargs)
        except BudgetError as exc:
            report_error(exc.message, exc.hint)
            return exc.exit_code
        except KeyboardInterrupt:
            print("\n중단되었습니다", file=sys.stderr)
            return 130
        except BrokenPipeError:  # `... | head` 처럼 출력 쪽이 먼저 닫힌 경우
            try:
                os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
            except (OSError, ValueError, AttributeError):
                pass
            return 0
        except EOFError:
            report_error(
                "대화형 입력을 받을 수 없습니다 (입력이 끝났습니다)",
                "터미널에서 직접 실행하세요. delete 는 --yes 옵션으로 확인 질문을 생략할 수 있습니다",
            )
            return 2
        except OSError as exc:
            message, hint = _describe_os_error(exc)
            report_error(message, hint)
            return 1
        except ValueError as exc:  # json.JSONDecodeError, UnicodeError 포함
            report_error("데이터를 처리할 수 없습니다: %s" % exc, "저장 파일의 내용이나 입력값을 확인하세요")
            return 1
        except Exception as exc:  # 어떤 경우에도 스택트레이스를 노출하지 않는다
            report_error(
                "예기치 못한 오류가 발생했습니다: %s: %s" % (type(exc).__name__, exc),
                "입력값과 --data-dir 경로를 확인하고 다시 시도하세요",
            )
            return 1

    return wrapper


def timed(func: Handler) -> Handler:
    """--verbose 일 때만 실행 시간을 stderr에 출력한다. 첫 인자는 argparse Namespace.

    예외가 나도 finally 에서 시간을 출력한 뒤, 예외는 그대로 바깥(handle_errors)으로 넘긴다.
    """

    @functools.wraps(func)
    def wrapper(args, *rest, **kwargs):
        start = time.perf_counter()
        try:
            return func(args, *rest, **kwargs)
        finally:
            if getattr(args, "verbose", False):
                print(
                    "[verbose] %s 실행 시간: %.3f초" % (func.__name__, time.perf_counter() - start),
                    file=sys.stderr,
                )

    return wrapper
