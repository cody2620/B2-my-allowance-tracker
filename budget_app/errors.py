"""사용자 정의 예외. 모든 예외는 '원인(message) + 해결 힌트(hint)'를 가진다.

종료 코드 규칙: 사용자 입력 오류 2, 데이터/파일 오류 1.
"""
from __future__ import annotations

from typing import Optional


class BudgetError(Exception):
    exit_code = 1

    def __init__(self, message: str, hint: str = "", exit_code: Optional[int] = None):
        super().__init__(message)
        self.message = message
        self.hint = hint
        if exit_code is not None:
            self.exit_code = exit_code


class ValidationError(BudgetError):
    """날짜/금액/type/category 등 입력값 오류."""

    exit_code = 2


class DuplicateError(BudgetError):
    exit_code = 2


class NotFoundError(BudgetError):
    exit_code = 1


class CategoryInUseError(BudgetError):
    exit_code = 1


class DataFileError(BudgetError):
    """저장 파일/CSV 파일의 내용이나 형식 오류."""

    exit_code = 1
