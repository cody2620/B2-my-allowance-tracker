"""사용자 정의 예외. 모든 예외는 '원인(message) + 해결 힌트(hint)'를 가진다.

종료 코드 규칙: 사용자 입력 오류 2, 데이터/파일 오류 1.

사용 방식:
- models / services / repository 는 문제가 생기면 아래 예외를 raise 만 한다 (직접 출력하지 않음).
- decorators.handle_errors 가 명령 함수 바깥에서 BudgetError 를 잡아
  "오류: {message}" / "힌트: {hint}" 를 stderr 에 출력하고 exit_code 를 종료 코드로 돌려준다.
- 그래서 새 오류를 추가할 때는 이 파일의 예외를 쓰기만 하면 출력·종료 코드가 자동으로 처리된다.
"""
from __future__ import annotations

from typing import Optional


class BudgetError(Exception):
    """이 앱의 모든 예외의 부모 클래스.

    message: 무엇이 잘못됐는지 (예: "날짜 형식이 올바르지 않습니다: '2026-13-01'")
    hint:    어떻게 고치면 되는지 (예: "YYYY-MM-DD 형식으로 입력하세요")
    exit_code: 프로그램 종료 코드. 기본값은 자식 클래스마다 정해져 있고, 생성할 때 덮어쓸 수 있다.
    """

    exit_code = 1

    def __init__(self, message: str, hint: str = "", exit_code: Optional[int] = None):
        super().__init__(message)
        self.message = message
        self.hint = hint
        if exit_code is not None:
            self.exit_code = exit_code


class ValidationError(BudgetError):
    """날짜/금액/type/category 등 입력값 오류.

    주로 models.parse_* 와 services 의 검증에서 발생한다.
    cli._ask 는 이 예외를 잡아 해당 항목만 다시 묻는다 (대화형 입력일 때).
    """

    exit_code = 2


class DuplicateError(BudgetError):
    """이미 있는 것을 또 만들려고 할 때. 예: 같은 이름의 카테고리 추가 (CategoryService.add)."""

    exit_code = 2


class NotFoundError(BudgetError):
    """찾는 대상이 없을 때. 예: 없는 거래 id 수정·삭제, 없는 카테고리 삭제."""

    exit_code = 1


class CategoryInUseError(BudgetError):
    """거래가 사용 중인 카테고리를 삭제하려 할 때 (CategoryService.remove)."""

    exit_code = 1


class DataFileError(BudgetError):
    """저장 파일/CSV 파일의 내용이나 형식 오류.

    예: JSONL 줄이 깨짐, UTF-8 이 아님, CSV 헤더에 필수 열이 없음.
    주로 repository 의 읽기 함수와 services.ImportExportService.import_csv 에서 발생한다.
    """

    exit_code = 1
