"""비즈니스 로직. 입력 검증은 models의 parse_* 함수를 모든 경로에서 공통으로 사용한다.

이 파일의 역할: "이 가계부의 규칙"을 담당한다. 화면(cli)과 파일(repository) 사이에 있다.
- 위(cli.py)에서 받은 원문 값을 models.parse_* 로 검증·변환하고
- 저장된 데이터를 봐야 하는 규칙(등록된 카테고리인가, 사용 중인가 등)을 확인한 뒤
- repository 에 읽기·쓰기를 시킨다.
print/input 은 쓰지 않는다. 결과는 반환값으로, 문제는 errors.py 의 예외로 알린다.

구성 (cli.AppContext 가 한 번에 조립한다):
- CategoryService      카테고리 목록·추가·삭제, 등록 여부 확인(require)
- SearchFilter         search 명령의 조건 묶음
- TransactionService   거래 추가·조회·수정·삭제·검색
- BudgetService        월 예산 설정·조회
- MonthlySummary       summary 결과 데이터
- SummaryService       월별 수입·지출·TOP N·예산 사용률 계산
- ImportExportService  CSV 가져오기/내보내기
"""
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

CSV_REQUIRED = ("date", "type", "category", "amount")  # import 할 CSV 헤더에 반드시 있어야 하는 열
CSV_COLUMNS = ("date", "type", "category", "amount", "memo", "tags")  # export 가 쓰는 열 순서


# ---------------------------------------------------------------- 카테고리
class CategoryService:
    """카테고리 관련 규칙.

    store: 카테고리 이름 저장소
    repo:  거래 저장소 (삭제하려는 카테고리를 쓰는 거래가 있는지 세기 위해 필요)
    """

    def __init__(self, store: CategoryStore, repo: TransactionRepository):
        self.store = store
        self.repo = repo

    def list(self) -> List[str]:
        """등록된 카테고리 이름 목록."""
        return self.store.names()

    def add(self, name: str) -> str:
        """카테고리를 추가하고 정규화된 이름을 반환한다. 이미 있으면 DuplicateError."""
        normalized = normalize_category(name)
        if normalized in self.store.names():
            raise DuplicateError(
                "이미 등록된 카테고리입니다: '%s'" % normalized,
                "category list 로 등록된 카테고리를 확인하세요",
            )
        self.store.add(normalized)
        return normalized

    def require(self, name: Any, known: Optional[Collection[str]] = None) -> str:
        """정규화한 이름이 등록된 카테고리인지 확인한다. 아니면 목록과 등록 방법을 안내.

        반환: 정규화된 이름. 아니면 ValidationError.
        known: 등록 목록을 미리 넘기면 파일을 다시 읽지 않는다 (import 에서 행마다 읽지 않으려고 사용).
        add 의 대화형 입력, update, normalize_fields 에서 쓰인다.
        """
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
        """이 카테고리를 쓰는 거래 수. 거래 파일 전체를 훑는다."""
        return sum(1 for tx in self.repo.iter_all() if tx.category == name)

    def remove(self, name: str) -> str:
        """카테고리를 삭제하고 정규화된 이름을 반환한다.

        없으면 NotFoundError, 쓰는 거래가 1건이라도 있으면 CategoryInUseError (거래가 고아가 되지 않도록).
        """
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
    """search 명령의 검색 조건 묶음. None 인 조건은 검사하지 않고, 나머지는 모두 만족해야(AND) 한다.

    date_from / date_to: 기간 (양 끝 포함)
    category / type:     정확히 일치
    q:                   메모에 포함된 키워드 (대소문자 무시)
    tag:                 태그 중 하나와 일치 (대소문자 무시)
    """

    date_from: Optional[str] = None
    date_to: Optional[str] = None
    category: Optional[str] = None
    type: Optional[str] = None
    q: Optional[str] = None
    tag: Optional[str] = None

    @classmethod
    def from_raw(cls, date_from=None, date_to=None, category=None, type=None, q=None, tag=None) -> "SearchFilter":
        """명령줄에서 받은 원문 옵션을 검증·정규화해 SearchFilter 를 만든다.

        q·tag 는 미리 소문자화(casefold)해 두어 matches 에서 매번 바꾸지 않는다.
        --from 이 --to 보다 늦으면 ValidationError.
        """
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
        """거래 하나가 모든 조건을 만족하면 True. 하나라도 어긋나면 바로 False."""
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
    """거래 추가·조회·수정·삭제·검색 규칙.

    repo:       거래 저장소
    categories: 카테고리가 등록돼 있는지 확인할 때 사용
    """

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
        """add/import 공통 검증. 정규화된 필드 dict를 반환하고 잘못되면 ValidationError.

        반환 예: {"date": "2026-03-05", "type": "expense", "category": "식비",
                  "amount": 12000, "memo": "점심", "tags": ["회사"]}
        이 dict 에 id 만 붙이면 Transaction 을 만들 수 있다.
        """
        return {
            "date": parse_date(date),
            "type": parse_type(type),
            "category": self.categories.require(category, known_categories),
            "amount": parse_amount(amount),
            "memo": parse_memo(memo),
            "tags": parse_tags(tags),
        }

    def add(self, **raw: Any) -> Transaction:
        """거래 한 건을 검증해 새 id(현재 최대 id + 1)를 붙이고 저장한다. 저장된 Transaction 을 반환.

        raw: date, type, category, amount, memo, tags 키워드 인자 (cmd_add 가 넘김)
        """
        fields = self.normalize_fields(**raw)
        tx = Transaction(id=self.repo.max_id() + 1, **fields)
        self.repo.add(tx)
        return tx

    def get(self, tx_id: int) -> Transaction:
        """id 로 거래 하나를 가져온다. 없으면 NotFoundError."""
        return self.repo.get(tx_id)

    def update(self, tx_id: int, **changes: Optional[str]) -> Transaction:
        """지정한(None이 아닌) 필드만 수정한다.

        changes: date, type, category, amount, memo, tags. 명령줄에서 주지 않은 옵션은 None 으로 온다.
        예: update(7, amount="5,000", 나머지 None) → 7번 거래의 금액만 5000 으로 바꾼다.
        하나도 지정하지 않으면 ValidationError, 없는 id 는 NotFoundError.
        """
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
        """id 에 해당하는 거래를 삭제한다. 없으면 NotFoundError."""
        self.repo.delete(tx_id)

    def latest(self, limit: int) -> Iterator[Transaction]:
        """최신 거래부터 최대 limit 건 (list 명령). limit 건을 채우면 파일 읽기를 멈춘다."""
        return islice(self.repo.iter_latest(), limit)

    def search(self, flt: SearchFilter, limit: int) -> Iterator[Transaction]:
        """조건에 맞는 거래를 최신순으로 최대 limit 건 (search 명령)."""
        return islice(self._search(flt), limit)

    def _search(self, flt: SearchFilter) -> Iterator[Transaction]:
        """최신순으로 훑으며 조건에 맞는 거래만 내보낸다. date_from 보다 오래된 거래가 나오면 멈춘다."""
        for tx in self.repo.iter_latest():
            if flt.date_from and tx.date < flt.date_from:
                break  # 최신순이므로 이후는 모두 더 오래된 거래
            if flt.matches(tx):
                yield tx


# ---------------------------------------------------------------- 예산 / 요약
class BudgetService:
    """월 예산 설정·조회."""

    def __init__(self, store: BudgetStore):
        self.store = store

    def set(self, month: Any, amount: Any) -> Tuple[Budget, Optional[Budget]]:
        """월 예산을 저장한다 (같은 월은 덮어씀).

        반환: (새로 저장한 예산, 덮어쓰기 전의 예산 또는 None). cli 가 "기존 예산을 덮어썼습니다" 안내에 쓴다.
        """
        budget = Budget(month=parse_month(month), amount=parse_amount(amount))
        return budget, self.store.set(budget)

    def show(self, month: Any) -> Optional[Budget]:
        """해당 월의 예산. 설정하지 않았으면 None."""
        return self.store.get(parse_month(month))


@dataclass
class MonthlySummary:
    """summary 명령의 결과. SummaryService.monthly 가 채우고 cli.cmd_summary 가 출력한다.

    count:   그 달의 거래 수
    income / expense: 총수입 / 총지출
    top:     지출이 큰 카테고리 [(카테고리, 금액), ...] (최대 top_n 개)
    budget:  그 달의 예산 금액 (없으면 None)
    balance / usage_pct / over_amount 는 위 값으로 계산하는 속성이다.
    """

    month: str
    count: int = 0
    income: int = 0
    expense: int = 0
    top: List[Tuple[str, int]] = field(default_factory=list)
    budget: Optional[int] = None

    @property
    def balance(self) -> int:
        """잔액 = 수입 - 지출."""
        return self.income - self.expense

    @property
    def usage_pct(self) -> Optional[float]:
        """예산 사용률(%) = 지출 / 예산 × 100. 예산이 없으면 None."""
        if self.budget is None:
            return None
        return self.expense * 100.0 / self.budget

    @property
    def over_amount(self) -> int:
        """예산을 넘은 금액. 넘지 않았거나 예산이 없으면 0."""
        if self.budget is None:
            return 0
        return max(0, self.expense - self.budget)


class SummaryService:
    """월별 요약 계산. 거래(repo)와 예산(budgets)을 함께 본다."""

    def __init__(self, repo: TransactionRepository, budgets: BudgetService):
        self.repo = repo
        self.budgets = budgets

    def monthly(self, month: Any, top_n: int = 3) -> MonthlySummary:
        """한 달의 수입·지출·카테고리별 지출 TOP N·예산을 계산한다.

        거래를 최신순으로 한 번만 훑는다. 해당 월보다 최신이면 건너뛰고,
        더 오래된 거래가 나오면 그 뒤는 모두 더 오래됐으므로 멈춘다.
        TOP N 은 금액 내림차순, 같으면 카테고리 이름순.
        """
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
    """import 결과 집계. success: 저장된 행, failed: 오류 행, skipped: 빈 행, errors: [(줄 번호, 사유)]."""

    success: int = 0
    failed: int = 0
    skipped: int = 0
    errors: List[Tuple[int, str]] = field(default_factory=list)


class ImportExportService:
    """CSV 가져오기(import)와 내보내기(export).

    검증은 TransactionService.normalize_fields 를 그대로 써서 add 와 같은 규칙이 적용된다.
    """

    def __init__(self, transactions: TransactionService):
        self.transactions = transactions
        self.repo = transactions.repo

    def import_csv(self, path: str) -> ImportResult:
        """CSV 파일의 거래를 가져온다. 유효한 행만 저장하고 결과(ImportResult)를 반환한다.

        진행 순서:
        1. 헤더 확인: 비었거나 필수 열(date,type,category,amount)이 없으면 DataFileError (아무것도 저장 안 함)
        2. 행마다 검증: 빈 행은 skipped, 오류 행은 failed + errors 에 (줄 번호, 사유) 기록
        3. 모든 행을 본 뒤 유효한 행들에 연속된 id 를 붙여 add_many 로 한 번에 저장
        열 순서는 상관없다 (헤더 이름으로 위치를 찾음).
        """
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
                    """현재 행에서 열 이름에 해당하는 값. 열이 없거나 행이 짧으면 ""."""
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
        """기간 안의 거래를 CSV 파일로 쓰고, 내보낸 건수를 반환한다.

        기간: --month, --from, --to 중 하나 이상 필요. 여러 개를 주면 모두 겹치는 구간(low~high)만.
        파일은 오래된 순(날짜 오름차순)이며 atomic_write_lines 로 써서 중간에 실패해도 깨진 파일이 남지 않는다.
        """
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
            """헤더 한 줄 + 기간 안 거래의 CSV 줄들. 쓰는 동안 count 를 센다."""
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
