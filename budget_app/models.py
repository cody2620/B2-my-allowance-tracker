"""데이터 모델과 입력 검증 함수.

검증 함수(parse_*)는 add/update/import/budget/search 모든 입력 경로가 공통으로 사용한다.
카테고리 이름 정규화 규칙: 앞뒤 공백을 제거하고 소문자로 변환한다 (예: ' Food ' -> 'food', 한글은 대소문자 구분이 없어 공백만 제거됨).

이 파일이 하는 일 (계층 구조의 가장 아래, errors.py 만 가져다 쓴다):
1. 데이터 모양 정의: Transaction(거래 한 건), Budget(월 예산 하나)
2. 값 검사: parse_* 함수가 값이 올바른지 확인하고, 틀리면 ValidationError 를 낸다.
3. 값 변환: "12,000" → 12000, " Food " → "food", 객체 ↔ dict 등.

판단 기준: "다른 데이터를 보지 않고 값 하나만으로 판단할 수 있는가".
'등록된 카테고리인가' 처럼 저장된 데이터를 봐야 하는 검사는 services.py 가 한다.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

from .errors import ValidationError

TYPES = ("income", "expense")  # 거래 타입으로 저장되는 값 (화면에는 수입/지출로 표시)
DEFAULT_CATEGORIES = ("식비", "교통", "주거", "기타")  # 카테고리 파일이 비어 있을 때 자동 생성

_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")  # YYYY-MM-DD 모양만 검사 (실제 날짜인지는 따로 확인)
_MONTH_RE = re.compile(r"[0-9]{4}-[0-9]{2}")  # YYYY-MM
_AMOUNT_RE = re.compile(r"-?[0-9]+|-?[0-9]{1,3}(,[0-9]{3})+")  # 12000 또는 12,000 (음수는 뒤에서 거부)
_MAX_AMOUNT_DIGITS = 15  # 너무 큰 금액 방지


# ---------------------------------------------------------------- 데이터 클래스
@dataclass
class Transaction:
    """거래 한 건. transactions.jsonl 의 한 줄이 이 객체 하나에 해당한다.

    id:       1부터 시작하는 고유 번호 (새 거래는 현재 최대 id + 1)
    type:     "income"(수입) 또는 "expense"(지출)
    date:     "YYYY-MM-DD" 문자열
    amount:   양의 정수 (원)
    category: 정규화된 카테고리 이름
    memo:     한 줄 메모 (없으면 "")
    tags:     태그 목록 (없으면 [])
    """

    id: int
    type: str
    date: str
    amount: int
    category: str
    memo: str = ""
    tags: List[str] = field(default_factory=list)

    @property
    def sort_key(self) -> Tuple[str, int]:
        """정렬 기준 (날짜, id). transactions.jsonl 은 항상 이 값의 오름차순으로 저장된다."""
        return (self.date, self.id)

    def to_dict(self) -> Dict[str, Any]:
        """저장용 dict 로 변환한다. repository 가 이것을 JSON 한 줄로 바꿔 파일에 쓴다."""
        return {
            "id": self.id,
            "type": self.type,
            "date": self.date,
            "amount": self.amount,
            "category": self.category,
            "memo": self.memo,
            "tags": list(self.tags),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Transaction":
        """저장된 레코드를 엄격하게 복원한다. 형식이 틀리면 ValidationError/KeyError/TypeError.

        to_dict 의 반대. 파일을 사람이 직접 고쳐 잘못된 값이 들어갔을 수 있으므로
        모든 필드를 parse_* 로 다시 검증한다. 오류는 repository 가 DataFileError 로 바꿔 알린다.
        """
        tx_id = data["id"]
        if isinstance(tx_id, bool) or not isinstance(tx_id, int) or tx_id <= 0:
            raise ValidationError("id가 양의 정수가 아닙니다")
        return cls(
            id=tx_id,
            type=parse_type(data["type"]),
            date=parse_date(data["date"]),
            amount=parse_amount(data["amount"]),
            category=normalize_category(data["category"]),
            memo=parse_memo(data.get("memo", "")),
            tags=parse_tags(data.get("tags", [])),
        )


@dataclass
class Budget:
    """월 예산 하나. budgets.jsonl 의 한 줄에 해당하며, 월마다 최대 하나만 있다.

    month:  "YYYY-MM"
    amount: 양의 정수 (원)
    """

    month: str
    amount: int

    def to_dict(self) -> Dict[str, Any]:
        """저장용 dict 로 변환한다."""
        return {"month": self.month, "amount": self.amount}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Budget":
        """저장된 dict 를 검증하며 Budget 으로 복원한다."""
        return cls(month=parse_month(data["month"]), amount=parse_amount(data["amount"]))


# ---------------------------------------------------------------- 검증·변환 함수
# 공통 규칙: 원문 값을 받아 올바르면 정리된 값을 반환하고, 틀리면 ValidationError(메시지, 힌트)를 낸다.

def parse_date(value: Any) -> str:
    """날짜를 검증한다. "2026-03-05" → "2026-03-05".

    1단계: YYYY-MM-DD 모양인지 (정규식)
    2단계: 달력에 있는 날짜인지 (예: 2026-02-30 거부)
    """
    text = "" if value is None else str(value).strip()
    if not _DATE_RE.fullmatch(text):
        raise ValidationError(
            "날짜 형식이 올바르지 않습니다: '%s'" % text,
            "YYYY-MM-DD 형식으로 입력하세요 (예: 2026-03-05)",
        )
    try:
        date.fromisoformat(text)
    except ValueError:
        raise ValidationError(
            "존재하지 않는 날짜입니다: '%s'" % text,
            "달력에 있는 날짜인지 확인하세요 (예: 2월은 28~29일까지)",
        )
    return text


def parse_month(value: Any) -> str:
    """월을 검증한다. "2026-03" → "2026-03". 월이 01~12 가 아니면 거부한다."""
    text = "" if value is None else str(value).strip()
    if not _MONTH_RE.fullmatch(text):
        raise ValidationError(
            "월 형식이 올바르지 않습니다: '%s'" % text,
            "YYYY-MM 형식으로 입력하세요 (예: 2026-03)",
        )
    try:
        date(int(text[:4]), int(text[5:7]), 1)
    except ValueError:
        raise ValidationError(
            "존재하지 않는 월입니다: '%s'" % text,
            "월은 01~12 사이여야 합니다 (예: 2026-03)",
        )
    return text


def parse_amount(value: Any) -> int:
    """양의 정수만 허용. 문자열의 천 단위 콤마(1,500)는 허용한다.

    "12,000" → 12000, 12000 → 12000.
    거부: 0 이하, 소수점, 문자, 15자리 초과, bool (파이썬에서 True 는 정수 1로 취급되므로 따로 막는다).
    """
    if isinstance(value, bool):
        raise ValidationError("금액은 정수여야 합니다: '%s'" % value, "예: 12000 또는 12,000")
    if isinstance(value, int):
        number = value
    else:
        text = "" if value is None else str(value).strip()
        if not _AMOUNT_RE.fullmatch(text) or len(text.replace(",", "").lstrip("-")) > _MAX_AMOUNT_DIGITS:
            raise ValidationError(
                "금액은 정수여야 합니다: '%s'" % text,
                "숫자만 입력하세요 (예: 12000 또는 12,000). 소수점·문자·너무 큰 값은 사용할 수 없습니다",
            )
        number = int(text.replace(",", ""))
    if number <= 0:
        raise ValidationError(
            "금액은 0보다 커야 합니다: %s" % number,
            "1 이상의 정수를 입력하세요",
        )
    return number


def parse_type(value: Any) -> str:
    """거래 타입을 검증한다. " Expense " → "expense". income/expense 외에는 거부.

    한글 "수입"/"지출" 입력은 cli._parse_type_ko 가 먼저 영문으로 바꾼 뒤 이 함수를 부른다.
    """
    text = "" if value is None else str(value).strip().lower()
    if text not in TYPES:
        raise ValidationError(
            "type은 income 또는 expense여야 합니다: '%s'" % ("" if value is None else str(value).strip()),
            "수입이면 income, 지출이면 expense 를 입력하세요",
        )
    return text


def normalize_category(value: Any) -> str:
    """카테고리 이름을 비교·저장용 형태로 맞춘다. " Food " → "food", " 식비 " → "식비".

    이름이 비어 있으면 거부한다. 등록된 카테고리인지는 여기서 보지 않는다
    (그 검사는 services.CategoryService.require 가 한다).
    """
    text = "" if value is None else str(value).strip().lower()
    if not text:
        raise ValidationError("카테고리 이름이 비어 있습니다", "공백이 아닌 이름을 입력하세요 (예: 식비)")
    return text


def parse_memo(value: Any) -> str:
    """메모를 한 줄로 정리한다. 줄바꿈은 공백으로 바꾼다 (JSONL 한 줄·CSV 한 행이 깨지지 않도록).

    메모는 선택 항목이라 오류를 내지 않는다. None 이면 "".
    """
    text = "" if value is None else str(value)
    return " ".join(text.split("\n")).replace("\r", "").strip()


def parse_tags(value: Union[str, Iterable[str], None]) -> List[str]:
    """쉼표 구분 문자열 또는 리스트를 태그 리스트로 변환 (공백/빈 값 제거, 대소문자 무시 중복 제거).

    "카페, 친구,카페" → ["카페", "친구"].  ["a", "A"] → ["a"] (처음 나온 표기를 유지).
    문자열은 사용자 입력·CSV 에서, 리스트는 저장 파일 복원(from_dict)에서 들어온다.
    """
    if value is None:
        return []
    items = value.split(",") if isinstance(value, str) else [str(v) for v in value]
    tags: List[str] = []
    seen = set()
    for item in items:
        tag = item.strip()
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            tags.append(tag)
    return tags


# ---------------------------------------------------------------- 작은 도우미
def month_range(month: str) -> Tuple[str, str]:
    """'YYYY-MM' -> (월 첫날, 월 마지막날) 문자열.

    "2026-02" → ("2026-02-01", "2026-02-28"). export --month 를 날짜 범위로 바꿀 때 쓴다.
    month 는 parse_month 로 이미 검증된 값이어야 한다.
    """
    year, mon = int(month[:4]), int(month[5:7])
    last = calendar.monthrange(year, mon)[1]
    return "%s-01" % month, "%s-%02d" % (month, last)


def today_str() -> str:
    """오늘 날짜 "YYYY-MM-DD". add 에서 날짜를 비우고 Enter 를 누르면 이 값이 쓰인다."""
    return date.today().isoformat()


def format_won(amount: int) -> str:
    """화면 출력용 원화 표기. 12000 → "12,000원"."""
    return "{:,}원".format(amount)
