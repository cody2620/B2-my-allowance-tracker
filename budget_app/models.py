"""데이터 모델과 입력 검증 함수.

검증 함수(parse_*)는 add/update/import/budget/search 모든 입력 경로가 공통으로 사용한다.
카테고리 이름 정규화 규칙: 앞뒤 공백을 제거하고 소문자로 변환한다 (예: ' Food ' -> 'food').
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

from .errors import ValidationError

TYPES = ("income", "expense")
DEFAULT_CATEGORIES = ("food", "transport", "rent", "etc")

_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_MONTH_RE = re.compile(r"[0-9]{4}-[0-9]{2}")
_AMOUNT_RE = re.compile(r"-?[0-9]+|-?[0-9]{1,3}(,[0-9]{3})+")
_MAX_AMOUNT_DIGITS = 15


@dataclass
class Transaction:
    id: int
    type: str
    date: str
    amount: int
    category: str
    memo: str = ""
    tags: List[str] = field(default_factory=list)

    @property
    def sort_key(self) -> Tuple[str, int]:
        return (self.date, self.id)

    def to_dict(self) -> Dict[str, Any]:
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
        """저장된 레코드를 엄격하게 복원한다. 형식이 틀리면 ValidationError/KeyError/TypeError."""
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
    month: str
    amount: int

    def to_dict(self) -> Dict[str, Any]:
        return {"month": self.month, "amount": self.amount}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Budget":
        return cls(month=parse_month(data["month"]), amount=parse_amount(data["amount"]))


def parse_date(value: Any) -> str:
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
    """양의 정수만 허용. 문자열의 천 단위 콤마(1,500)는 허용한다."""
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
    text = "" if value is None else str(value).strip().lower()
    if text not in TYPES:
        raise ValidationError(
            "type은 income 또는 expense여야 합니다: '%s'" % ("" if value is None else str(value).strip()),
            "수입이면 income, 지출이면 expense 를 입력하세요",
        )
    return text


def normalize_category(value: Any) -> str:
    text = "" if value is None else str(value).strip().lower()
    if not text:
        raise ValidationError("카테고리 이름이 비어 있습니다", "공백이 아닌 이름을 입력하세요 (예: food)")
    return text


def parse_memo(value: Any) -> str:
    text = "" if value is None else str(value)
    return " ".join(text.split("\n")).replace("\r", "").strip()


def parse_tags(value: Union[str, Iterable[str], None]) -> List[str]:
    """쉼표 구분 문자열 또는 리스트를 태그 리스트로 변환 (공백/빈 값 제거, 대소문자 무시 중복 제거)."""
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


def month_range(month: str) -> Tuple[str, str]:
    """'YYYY-MM' -> (월 첫날, 월 마지막날) 문자열."""
    year, mon = int(month[:4]), int(month[5:7])
    last = calendar.monthrange(year, mon)[1]
    return "%s-01" % month, "%s-%02d" % (month, last)


def today_str() -> str:
    return date.today().isoformat()


def format_won(amount: int) -> str:
    return "{:,}원".format(amount)
