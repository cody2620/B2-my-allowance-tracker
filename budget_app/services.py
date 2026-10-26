"""비즈니스 로직. 입력 검증은 models의 parse_* 함수를 모든 경로에서 공통으로 사용한다."""
from __future__ import annotations

import csv
import dataclasses
import io
import os
from dataclasses import dataclass, field
from itertools import islice
from typing import Any, Collection, Dict, Iterator, List, Optional, Tuple

from .errors import (
    CategoryInUseError,
    DataFileError,
    DuplicateError,
    NotFoundError,
    ValidationError,
)
from .models import (
    Budget,
    Transaction,
    month_range,
    normalize_category,
    parse_amount,
    parse_date,
    parse_memo,
    parse_month,
    parse_tags,
    parse_type,
)
from .repository import BudgetStore, CategoryStore, TransactionRepository, atomic_write_lines

CSV_REQUIRED = ("date", "type", "category", "amount")
CSV_COLUMNS = ("date", "type", "category", "amount", "memo", "tags")


# ---------------------------------------------------------------- 카테고리
class CategoryService:
    def __init__(self, store: CategoryStore, repo: TransactionRepository):
        self.store = store
        self.repo = repo

    def list(self) -> List[str]:
        return self.store.names()

    def add(self, name: str) -> str:
        normalized = normalize_category(name)
        if normalized in self.store.names():
            raise DuplicateError(
                "이미 등록된 카테고리입니다: '%s'" % normalized,
                "category list 로 등록된 카테고리를 확인하세요",
            )
        self.store.add(normalized)
        return normalized

    def require(self, name: Any, known: Optional[Collection[str]] = None) -> str:
        """정규화한 이름이 등록된 카테고리인지 확인한다. 아니면 목록과 등록 방법을 안내."""
        normalized = normalize_category(name)
        names = self.store.names() if known is None else known
        if normalized not in names:
            raise ValidationError(
                "등록되지 않은 카테고리입니다: '%s'" % normalized,
                "등록된 카테고리: %s / 새 카테고리는 먼저 'category add %s' 로 등록하세요"
                % (", ".join(names) or "(없음)", normalized),
            )
        return normalized

    def usage_count(self, name: str) -> int:
        return sum(1 for tx in self.repo.iter_all() if tx.category == name)

    def remove(self, name: str) -> str:
        normalized = normalize_category(name)
        if normalized not in self.store.names():
            raise NotFoundError(
                "등록되지 않은 카테고리입니다: '%s'" % normalized,
                "category list 로 등록된 카테고리를 확인하세요",
            )
        used = self.usage_count(normalized)
        if used:
            raise CategoryInUseError(
                "카테고리 '%s'를 사용하는 거래가 %d건 있어 삭제할 수 없습니다" % (normalized, used),
                "해당 거래를 먼저 update(--category 변경) 하거나 delete 한 뒤 다시 시도하세요 "
                "(확인: search --category %s)" % normalized,
            )
        self.store.remove(normalized)
        return normalized


# ---------------------------------------------------------------- 거래
@dataclass
class SearchFilter:
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    category: Optional[str] = None
    type: Optional[str] = None
    q: Optional[str] = None
    tag: Optional[str] = None

    @classmethod
    def from_raw(cls, date_from=None, date_to=None, category=None, type=None, q=None, tag=None) -> "SearchFilter":
        flt = cls(
            date_from=parse_date(date_from) if date_from is not None else None,
            date_to=parse_date(date_to) if date_to is not None else None,
            category=normalize_category(category) if category is not None else None,
            type=parse_type(type) if type is not None else None,
            q=q.casefold() if q else None,
            tag=tag.strip().casefold() if tag and tag.strip() else None,
        )
        if flt.date_from and flt.date_to and flt.date_from > flt.date_to:
            raise ValidationError(
                "--from(%s)이 --to(%s)보다 늦습니다" % (flt.date_from, flt.date_to),
                "시작일이 종료일보다 앞서도록 --from, --to 를 바꿔 입력하세요",
            )
        return flt

    def matches(self, tx: Transaction) -> bool:
        if self.date_from and tx.date < self.date_from:
            return False
        if self.date_to and tx.date > self.date_to:
            return False
        if self.category and tx.category != self.category:
            return False
        if self.type and tx.type != self.type:
            return False
        if self.q and self.q not in tx.memo.casefold():
            return False
        if self.tag and self.tag not in (t.casefold() for t in tx.tags):
            return False
        return True


class TransactionService:
    def __init__(self, repo: TransactionRepository, categories: CategoryService):
        self.repo = repo
        self.categories = categories

    def normalize_fields(
        self,
        date: Any,
        type: Any,
        category: Any,
        amount: Any,
        memo: Any = "",
        tags: Any = None,
        known_categories: Optional[Collection[str]] = None,
    ) -> Dict[str, Any]:
        """add/import 공통 검증. 정규화된 필드 dict를 반환하고 잘못되면 ValidationError."""
        return {
            "date": parse_date(date),
            "type": parse_type(type),
            "category": self.categories.require(category, known_categories),
            "amount": parse_amount(amount),
            "memo": parse_memo(memo),
            "tags": parse_tags(tags),
        }

    def add(self, **raw: Any) -> Transaction:
        fields = self.normalize_fields(**raw)
        tx = Transaction(id=self.repo.max_id() + 1, **fields)
        self.repo.add(tx)
        return tx

    def get(self, tx_id: int) -> Transaction:
        return self.repo.get(tx_id)

    def update(self, tx_id: int, **changes: Optional[str]) -> Transaction:
        """지정한(None이 아닌) 필드만 수정한다."""
        provided = {k: v for k, v in changes.items() if v is not None}
        if not provided:
            raise ValidationError(
                "수정할 필드가 지정되지 않았습니다",
                "--date, --type, --category, --amount, --memo, --tags 중 하나 이상을 지정하세요",
            )
        validators = {
            "date": parse_date,
            "type": parse_type,
            "category": self.categories.require,
            "amount": parse_amount,
            "memo": parse_memo,
            "tags": parse_tags,
        }
        parsed = {key: validators[key](value) for key, value in provided.items()}
        updated = dataclasses.replace(self.repo.get(tx_id), **parsed)
        self.repo.replace(updated)
        return updated

    def delete(self, tx_id: int) -> None:
        self.repo.delete(tx_id)

    def latest(self, limit: int) -> Iterator[Transaction]:
        return islice(self.repo.iter_latest(), limit)

    def search(self, flt: SearchFilter, limit: int) -> Iterator[Transaction]:
        return islice(self._search(flt), limit)

    def _search(self, flt: SearchFilter) -> Iterator[Transaction]:
        for tx in self.repo.iter_latest():
            if flt.date_from and tx.date < flt.date_from:
                break  # 최신순이므로 이후는 모두 더 오래된 거래
            if flt.matches(tx):
                yield tx


# ---------------------------------------------------------------- 예산 / 요약
class BudgetService:
    def __init__(self, store: BudgetStore):
        self.store = store

    def set(self, month: Any, amount: Any) -> Tuple[Budget, Optional[Budget]]:
        budget = Budget(month=parse_month(month), amount=parse_amount(amount))
        return budget, self.store.set(budget)

    def show(self, month: Any) -> Optional[Budget]:
        return self.store.get(parse_month(month))


@dataclass
class MonthlySummary:
    month: str
    count: int = 0
    income: int = 0
    expense: int = 0
    top: List[Tuple[str, int]] = field(default_factory=list)
    budget: Optional[int] = None

    @property
    def balance(self) -> int:
        return self.income - self.expense

    @property
    def usage_pct(self) -> Optional[float]:
        if self.budget is None:
            return None
        return self.expense * 100.0 / self.budget

    @property
    def over_amount(self) -> int:
        if self.budget is None:
            return 0
        return max(0, self.expense - self.budget)


class SummaryService:
    def __init__(self, repo: TransactionRepository, budgets: BudgetService):
        self.repo = repo
        self.budgets = budgets

    def monthly(self, month: Any, top_n: int = 3) -> MonthlySummary:
        month = parse_month(month)
        summary = MonthlySummary(month=month)
        by_category: Dict[str, int] = {}
        for tx in self.repo.iter_latest():  # 한 번 순회하며 집계
            tx_month = tx.date[:7]
            if tx_month > month:
                continue
            if tx_month < month:
                break
            summary.count += 1
            if tx.type == "income":
                summary.income += tx.amount
            else:
                summary.expense += tx.amount
                by_category[tx.category] = by_category.get(tx.category, 0) + tx.amount
        ranked = sorted(by_category.items(), key=lambda item: (-item[1], item[0]))
        summary.top = ranked[:top_n]
        budget = self.budgets.show(month)
        summary.budget = budget.amount if budget else None
        return summary


# ---------------------------------------------------------------- import / export
@dataclass
class ImportResult:
    success: int = 0
    failed: int = 0
    skipped: int = 0
    errors: List[Tuple[int, str]] = field(default_factory=list)


class ImportExportService:
    def __init__(self, transactions: TransactionService):
        self.transactions = transactions
        self.repo = transactions.repo

    def import_csv(self, path: str) -> ImportResult:
        result = ImportResult()
        known = set(self.transactions.categories.list())
        valid: List[Dict[str, Any]] = []
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            try:
                header = next(reader)
            except StopIteration:
                raise DataFileError(
                    "CSV 파일이 비어 있습니다: %s" % path,
                    "헤더(%s)를 첫 줄에 포함해 작성하세요" % ",".join(CSV_COLUMNS),
                )
            except csv.Error as exc:
                raise DataFileError("CSV를 읽을 수 없습니다: %s (%s)" % (path, exc), "UTF-8 CSV 파일인지 확인하세요")
            names = [h.strip().lower() for h in header]
            missing = [c for c in CSV_REQUIRED if c not in names]
            if missing:
                raise DataFileError(
                    "CSV 헤더에 필수 열이 없습니다: %s" % ", ".join(missing),
                    "첫 줄을 '%s' 형식으로 맞추세요 (memo, tags는 선택)" % ",".join(CSV_COLUMNS),
                )
            index = {}
            for i, name in enumerate(names):
                index.setdefault(name, i)

            while True:
                previous_line = reader.line_num
                try:
                    row = next(reader)
                except StopIteration:
                    break
                except csv.Error as exc:
                    result.failed += 1
                    result.errors.append((reader.line_num, "CSV 형식 오류: %s" % exc))
                    if reader.line_num == previous_line:
                        break
                    continue
                line_no = previous_line + 1
                if not any(cell.strip() for cell in row):
                    result.skipped += 1
                    continue
                if len(row) > len(names):
                    result.failed += 1
                    result.errors.append(
                        (line_no, "열이 헤더보다 많습니다 (쉼표가 포함된 값은 큰따옴표로 감싸세요)")
                    )
                    continue

                def cell(column: str) -> str:
                    pos = index.get(column)
                    return row[pos] if pos is not None and pos < len(row) else ""

                try:
                    valid.append(
                        self.transactions.normalize_fields(
                            date=cell("date"),
                            type=cell("type"),
                            category=cell("category"),
                            amount=cell("amount"),
                            memo=cell("memo"),
                            tags=cell("tags"),
                            known_categories=known,
                        )
                    )
                except ValidationError as exc:
                    result.failed += 1
                    result.errors.append((line_no, exc.message))

        if valid:
            next_id = self.repo.max_id() + 1
            self.repo.add_many(Transaction(id=next_id + i, **fields) for i, fields in enumerate(valid))
            result.success = len(valid)
        return result

    def export_csv(
        self,
        path: str,
        month: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> int:
        if month is None and date_from is None and date_to is None:
            raise ValidationError(
                "내보낼 기간 조건이 없습니다",
                "--month YYYY-MM 또는 --from YYYY-MM-DD --to YYYY-MM-DD 중 하나 이상을 지정하세요",
            )
        low, high = "", "9999-99-99"
        if month is not None:
            first, last = month_range(parse_month(month))
            low, high = max(low, first), min(high, last)
        if date_from is not None:
            low = max(low, parse_date(date_from))
        if date_to is not None:
            high = min(high, parse_date(date_to))
        if low > high:
            raise ValidationError(
                "기간이 올바르지 않습니다 (시작 %s > 종료 %s)" % (low, high),
                "시작일이 종료일보다 앞서도록 지정하세요",
            )

        count = 0

        def lines() -> Iterator[str]:
            nonlocal count
            for row in _csv_lines([CSV_COLUMNS]):
                yield row
            for tx in self.repo.iter_all():
                if tx.date < low:
                    continue
                if tx.date > high:
                    break
                count += 1
                yield from _csv_lines([(tx.date, tx.type, tx.category, tx.amount, tx.memo, ",".join(tx.tags))])

        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
        atomic_write_lines(path, lines())
        return count


def _csv_lines(rows) -> Iterator[str]:
    """csv 모듈로 인용 처리한 한 줄 문자열들을 만든다 (줄바꿈은 쓰기 쪽에서 붙인다)."""
    for row in rows:
        buffer = io.StringIO()
        csv.writer(buffer, lineterminator="").writerow(row)
        yield buffer.getvalue()
