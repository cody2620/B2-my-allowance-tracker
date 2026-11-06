"""argparse 설정, 명령 라우팅, 대화형 입력, 출력 포맷.

이 파일의 역할: 사용자와 직접 만나는 유일한 계층. print/input 은 여기서만 쓴다.
업무 규칙은 services.py 에 맡기고, 여기서는 "무엇을 묻고, 결과를 어떻게 보여줄지"만 정한다.

실행 흐름:
    main(argv)
      → build_parser().parse_args(argv)        명령줄 → args (Namespace)
      → args.func(args)                        선택된 명령의 cmd_* 함수 호출
          → @handle_errors → @timed → cmd_*    (decorators.py)
              → _context(args)                 AppContext: 저장소·서비스 조립
              → ctx.<서비스>.<메서드>(...)      services.py 에 일을 시킴
              → print(...)                     결과 출력
      → 종료 코드(int) 반환

파일 구성 (위에서부터): AppContext → 출력 포맷 도우미 → 대화형 입력 도우미
→ cmd_* 명령 핸들러 → argparse 파서 → main
"""
from __future__ import annotations

import argparse
import os
import sys
import unicodedata
from typing import Callable, Iterable, Optional

from .decorators import handle_errors, timed
from .errors import ValidationError
from .models import (
    Transaction,
    format_won,
    parse_amount,
    parse_date,
    parse_memo,
    parse_tags,
    parse_type,
    today_str,
)
from .repository import BudgetStore, CategoryStore, TransactionRepository
from .services import (
    BudgetService,
    CategoryService,
    ImportExportService,
    SearchFilter,
    SummaryService,
    TransactionService,
)

DEFAULT_DATA_DIR = "data"  # --data-dir 를 주지 않았을 때 저장 폴더
DEFAULT_LIST_LIMIT = 20  # list/search 의 기본 출력 건수


# ---------------------------------------------------------------- 컨텍스트
class AppContext:
    """--data-dir 하나로 저장소와 서비스를 조립한다.

    명령 함수는 이 객체의 속성으로 서비스를 쓴다:
        ctx.categories    CategoryService
        ctx.transactions  TransactionService
        ctx.budgets       BudgetService
        ctx.summary       SummaryService
        ctx.io            ImportExportService (CSV)
    같은 TransactionRepository 하나를 여러 서비스가 함께 쓴다.
    만들 때 카테고리 파일이 비어 있으면 기본 카테고리를 생성하고 안내를 출력한다.
    """

    def __init__(self, data_dir: str):
        repo = TransactionRepository(data_dir)
        category_store = CategoryStore(data_dir)
        budget_store = BudgetStore(data_dir)
        self.categories = CategoryService(category_store, repo)
        self.transactions = TransactionService(repo, self.categories)
        self.budgets = BudgetService(budget_store)
        self.summary = SummaryService(repo, self.budgets)
        self.io = ImportExportService(self.transactions)
        if category_store.ensure_defaults():
            print("카테고리가 비어 있어 기본 카테고리를 생성했습니다: %s" % ", ".join(self.categories.list()))


def _context(args: argparse.Namespace) -> AppContext:
    """명령 함수마다 맨 처음 부르는 함수. args.data_dir 로 AppContext 를 만든다."""
    return AppContext(args.data_dir)


# ---------------------------------------------------------------- 출력 포맷
def _display_width(text: str) -> int:
    """터미널에서 차지하는 폭을 계산한다 (한글 등 전각 문자는 2칸, 그 외는 1칸).

    파이썬 문자열 포맷(%-Ns)은 '글자 개수' 기준이라 한글이 섞이면 표가 어긋난다.
    """
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _ljust(text: str, width: int) -> str:
    """화면 폭 기준 왼쪽 정렬 (오른쪽을 공백으로 채움). str.ljust 의 한글 대응판."""
    return text + " " * max(0, width - _display_width(text))


def _rjust(text: str, width: int) -> str:
    """화면 폭 기준 오른쪽 정렬 (왼쪽을 공백으로 채움). 금액 열에 쓴다."""
    return " " * max(0, width - _display_width(text)) + text


_TYPE_LABEL_KO = {"income": "수입", "expense": "지출"}  # 저장값은 income/expense, 화면 표시만 한글


_MEMO_COL_WIDTH = 12  # 메모 열의 화면 폭 (이보다 길면 뒤 열이 밀릴 수 있음)


def format_tx(tx: Transaction) -> str:
    """거래 하나를 표의 한 줄 문자열로 만든다. list/search/update/delete 출력에 공통으로 쓴다.

    예: "   7  2026-03-05  지출     식비            12,000원  점심          #회사"
    열 순서: ID, 날짜, 유형(한글), 카테고리, 금액, 메모(없으면 -), 태그(없으면 -)
    """
    tags = " ".join("#" + t for t in tx.tags) if tx.tags else "-"
    line = "%4d  %s  %s  %s  %s  %s  %s" % (
        tx.id,
        tx.date,
        _ljust(_TYPE_LABEL_KO.get(tx.type, tx.type), 7),
        _ljust(tx.category, 10),
        _rjust(format_won(tx.amount), 14),
        _ljust(tx.memo if tx.memo else "-", _MEMO_COL_WIDTH),
        tags,
    )
    return line


def _print_transactions(items: Iterable[Transaction]) -> int:
    """스트리밍으로 출력하고 건수를 반환. 없으면 '데이터 없음'."""
    count = 0
    for tx in items:
        if count == 0:
            print("%4s  %s  %s  %s  %s  %s  %s" % (
                "ID", _ljust("날짜", 10), _ljust("유형", 7), _ljust("카테고리", 10), _rjust("금액", 14),
                _ljust("메모", _MEMO_COL_WIDTH), "태그",
            ))
        print(format_tx(tx))
        count += 1
    if count == 0:
        print("데이터 없음")
    return count


# ---------------------------------------------------------------- 대화형 입력
def _ask(prompt: str, parse: Callable[[str], object]):
    """검증에 실패하면 안내 후 해당 항목만 다시 묻는다.

    prompt: 화면에 보여줄 질문
    parse:  입력 문자열을 검증·변환하는 함수 (예: parse_amount). ValidationError 를 내면 재질문.
    반환:   parse 가 돌려준 값 (예: "12,000" → 12000)
    cmd_add 가 항목마다 한 번씩 부른다.
    """
    while True:
        raw = input(prompt)
        try:
            return parse(raw)
        except ValidationError as exc:
            print("  ✗ %s" % exc.message, file=sys.stderr)
            if exc.hint:
                print("    → %s" % exc.hint, file=sys.stderr)


def _parse_date_or_today(raw: str) -> str:
    """add 의 날짜 입력용: 빈 입력이면 오늘 날짜, 아니면 parse_date 로 검증."""
    return today_str() if not raw.strip() else parse_date(raw)


_TYPE_KO = {"수입": "income", "지출": "expense"}  # 대화형 입력(한글) → 저장값(영문)


def _parse_type_ko(raw: str) -> str:
    """대화형 입력은 한글(수입/지출)만 받고, 내부 저장값(income/expense)으로 바꿔준다."""
    text = raw.strip()
    if text not in _TYPE_KO:
        raise ValidationError(
            "타입은 수입 또는 지출이어야 합니다: '%s'" % text,
            "수입이면 수입, 지출이면 지출 을 입력하세요",
        )
    return parse_type(_TYPE_KO[text])


# ---------------------------------------------------------------- 명령 핸들러
# 모든 cmd_* 함수의 공통 모양:
#   1. ctx = _context(args)          서비스 준비
#   2. ctx.<서비스>.<메서드>(...)     실제 일은 services 에 맡김
#   3. print(...)                    결과 출력
#   4. return 0                      성공 (오류는 예외로 던지면 @handle_errors 가 처리)

@handle_errors
@timed
def cmd_add(args: argparse.Namespace) -> int:
    """`add`: 거래 한 건을 대화형으로 입력받아 저장한다.

    날짜 → 타입 → 카테고리 → 금액 → 메모 → 태그 순으로 묻고, 각 항목은 입력 즉시 검증한다.
    모두 통과하면 TransactionService.add 로 저장하고 새 id 를 알려준다.
    """
    ctx = _context(args)
    names = ctx.categories.list()
    print("새 거래를 입력합니다. (중단: Ctrl+C)")
    date = _ask("날짜 [YYYY-MM-DD, 빈 입력=오늘]: ", _parse_date_or_today)
    type_ = _ask("타입 [수입/지출]: ", _parse_type_ko)
    category = _ask("카테고리 [%s]: " % ", ".join(names), ctx.categories.require)
    amount = _ask("금액: ", parse_amount)
    memo = _ask("메모 (선택, Enter=건너뛰기): ", parse_memo)
    tags = _ask("태그 (선택, 쉼표로 구분): ", parse_tags)
    tx = ctx.transactions.add(date=date, type=type_, category=category, amount=amount, memo=memo, tags=tags)
    print("저장되었습니다. id=%d" % tx.id)
    return 0


@handle_errors
@timed
def cmd_list(args: argparse.Namespace) -> int:
    """`list [--limit N]`: 최신 거래부터 N건(기본 20)을 표로 출력한다."""
    ctx = _context(args)
    _print_transactions(ctx.transactions.latest(args.limit))
    return 0


@handle_errors
@timed
def cmd_search(args: argparse.Namespace) -> int:
    """`search [--from --to --category --type --q --tag --limit]`: 조건을 모두 만족하는 거래를 최신순 출력.

    옵션들을 SearchFilter 로 묶어 TransactionService.search 에 넘긴다.
    """
    ctx = _context(args)
    flt = SearchFilter.from_raw(
        date_from=args.date_from,
        date_to=args.date_to,
        category=args.category,
        type=args.type,
        q=args.q,
        tag=args.tag,
    )
    _print_transactions(ctx.transactions.search(flt, args.limit))
    return 0


@handle_errors
@timed
def cmd_summary(args: argparse.Namespace) -> int:
    """`summary --month YYYY-MM [--top N]`: 한 달의 수입·지출·잔액, 지출 TOP N, 예산 사용률 출력.

    계산은 SummaryService.monthly 가 하고, 여기서는 MonthlySummary 를 보기 좋게 출력만 한다.
    """
    ctx = _context(args)
    summary = ctx.summary.monthly(args.month, args.top)
    print("=== %s 요약 ===" % summary.month)
    if summary.count == 0:
        print("데이터 없음: %s 에 등록된 거래가 없습니다." % summary.month)
        return 0
    print("총수입: %s" % format_won(summary.income))
    print("총지출: %s" % format_won(summary.expense))
    print("잔액:   %s" % format_won(summary.balance))
    print("카테고리별 지출 TOP %d:" % args.top)
    if summary.top:
        for rank, (category, amount) in enumerate(summary.top, start=1):
            print("  %d. %-10s %s" % (rank, category, format_won(amount)))
    else:
        print("  지출 내역 없음")
    if summary.budget is None:
        print("예산: 설정되지 않음 (budget set --month %s --amount N 으로 설정)" % summary.month)
    else:
        print("예산: %s / 사용률 %.1f%%" % (format_won(summary.budget), summary.usage_pct))
        if summary.over_amount > 0:
            print("⚠ 예산 초과: %s" % format_won(summary.over_amount))
    return 0


@handle_errors
@timed
def cmd_budget_set(args: argparse.Namespace) -> int:
    """`budget set --month YYYY-MM --amount N`: 월 예산 저장. 기존 값을 덮어썼으면 그 금액도 알려준다."""
    ctx = _context(args)
    budget, previous = ctx.budgets.set(args.month, args.amount)
    print("예산이 저장되었습니다: %s %s" % (budget.month, format_won(budget.amount)))
    if previous is not None:
        print("(기존 예산 %s 을(를) 덮어썼습니다)" % format_won(previous.amount))
    return 0


@handle_errors
@timed
def cmd_budget_show(args: argparse.Namespace) -> int:
    """`budget show --month YYYY-MM`: 월 예산 출력. 없으면 설정 방법을 안내한다 (오류 아님)."""
    ctx = _context(args)
    budget = ctx.budgets.show(args.month)
    if budget is None:
        print("%s 예산이 설정되지 않았습니다 (budget set --month %s --amount N)" % (args.month, args.month))
    else:
        print("%s 예산: %s" % (budget.month, format_won(budget.amount)))
    return 0


@handle_errors
@timed
def cmd_category_add(args: argparse.Namespace) -> int:
    """`category add NAME`: 카테고리 추가. 출력되는 이름은 정규화된 이름이다 (예: " Food " → food)."""
    ctx = _context(args)
    print("카테고리가 추가되었습니다: %s" % ctx.categories.add(args.name))
    return 0


@handle_errors
@timed
def cmd_category_list(args: argparse.Namespace) -> int:
    """`category list`: 등록된 카테고리를 한 줄에 하나씩 출력."""
    ctx = _context(args)
    for name in ctx.categories.list():
        print(name)
    return 0


@handle_errors
@timed
def cmd_category_remove(args: argparse.Namespace) -> int:
    """`category remove NAME`: 카테고리 삭제. 그 카테고리를 쓰는 거래가 있으면 거부된다."""
    ctx = _context(args)
    print("카테고리가 삭제되었습니다: %s" % ctx.categories.remove(args.name))
    return 0


@handle_errors
@timed
def cmd_update(args: argparse.Namespace) -> int:
    """`update --id N [--date --type --category --amount --memo --tags]`: 준 옵션의 필드만 수정.

    주지 않은 옵션은 None 으로 넘어가 서비스가 무시한다. 수정 결과를 한 줄로 보여준다.
    """
    ctx = _context(args)
    tx = ctx.transactions.update(
        args.id,
        date=args.date,
        type=args.type,
        category=args.category,
        amount=args.amount,
        memo=args.memo,
        tags=args.tags,
    )
    print("수정되었습니다. id=%d" % tx.id)
    print(format_tx(tx))
    return 0


@handle_errors
@timed
def cmd_delete(args: argparse.Namespace) -> int:
    """`delete --id N [--yes]`: 거래 삭제. --yes 가 없으면 삭제할 거래를 보여주고 y/N 으로 확인한다."""
    ctx = _context(args)
    tx = ctx.transactions.get(args.id)  # 없는 id 는 확인 질문 전에 오류 처리
    if not args.yes:
        answer = input("다음 거래를 삭제할까요?\n  %s\n[y/N]: " % format_tx(tx))
        if answer.strip().lower() not in ("y", "yes"):
            print("삭제를 취소했습니다.")
            return 0
    ctx.transactions.delete(args.id)
    print("삭제되었습니다. id=%d" % args.id)
    return 0


@handle_errors
@timed
def cmd_import(args: argparse.Namespace) -> int:
    """`import --from CSV`: CSV 의 거래를 가져온다.

    실패한 행은 줄 번호와 사유를 stderr 에 출력하고, 유효한 행만 저장된다.
    실패가 1건이라도 있으면 종료 코드 1 (스크립트에서 실패를 감지할 수 있도록).
    """
    ctx = _context(args)
    result = ctx.io.import_csv(args.csv_path)
    for line_no, reason in result.errors:
        print("줄 %d: %s" % (line_no, reason), file=sys.stderr)
    print(
        "가져오기 완료 (%s): 성공 %d건 / 실패 %d건 / 건너뜀 %d건"
        % (args.csv_path, result.success, result.failed, result.skipped)
    )
    if result.failed:
        print(
            "힌트: 실패한 행은 반영되지 않았습니다(유효한 행만 반영). CSV를 수정하거나 "
            "'category add' 로 카테고리를 등록한 뒤 해당 행만 다시 import 하세요",
            file=sys.stderr,
        )
        return 1
    return 0


@handle_errors
@timed
def cmd_export(args: argparse.Namespace) -> int:
    """`export --out CSV [--month | --from --to]`: 기간 안의 거래를 CSV 파일로 저장한다."""
    ctx = _context(args)
    count = ctx.io.export_csv(args.out, month=args.month, date_from=args.date_from, date_to=args.date_to)
    print("CSV를 생성했습니다: %s" % os.path.abspath(args.out))
    print("내보내기 완료: 성공 %d건 / 실패 0건 / 건너뜀 0건" % count)
    return 0


# ---------------------------------------------------------------- 파서
class _Parser(argparse.ArgumentParser):
    """옵션 오류 메시지를 한글 '오류: … / 힌트: …' 형식으로 바꾼 ArgumentParser. 종료 코드 2."""

    def error(self, message: str):
        self.print_usage(sys.stderr)
        self.exit(2, "오류: %s\n힌트: '%s --help' 로 사용법을 확인하세요\n" % (message, self.prog))


def _positive_int(text: str) -> int:
    """argparse 의 type= 에 쓰는 변환 함수. "5" → 5, 0 이하·정수 아님은 옵션 오류로 처리된다.

    --limit, --top, --id 에 쓰인다.
    """
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("양의 정수여야 합니다: '%s'" % text)
    if value <= 0:
        raise argparse.ArgumentTypeError("1 이상의 정수여야 합니다: '%s'" % text)
    return value


def _common_parser() -> argparse.ArgumentParser:
    """모든 (서브)명령에서 쓸 수 있는 전역 옵션. 기본값을 SUPPRESS 해 앞쪽에서 준 값을 덮어쓰지 않는다."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--data-dir",
        default=argparse.SUPPRESS,
        metavar="PATH",
        help="저장 폴더 (기본: ./%s, 없으면 자동 생성)" % DEFAULT_DATA_DIR,
    )
    common.add_argument(
        "--verbose", action="store_true", default=argparse.SUPPRESS, help="실행 시간 등 상세 정보를 stderr에 출력"
    )
    return common


def build_parser() -> argparse.ArgumentParser:
    """모든 명령과 옵션을 정의한 파서를 만든다.

    각 서브명령에 set_defaults(func=cmd_*) 로 실행할 함수를 붙여 둔다.
    그래서 parse_args 결과 args.func 가 곧 실행할 명령 함수가 되고, main 은 args.func(args) 만 부르면 된다.
    새 명령을 추가하려면: cmd_* 함수를 만들고 여기서 add_command 로 등록하면 된다.
    """
    # parents= 는 Action 객체를 공유하므로, set_defaults 가 서브파서에 번지지 않게 파서마다 새로 만든다.
    parser = _Parser(
        prog="python -m budget_app",
        description="표준 라이브러리만 쓰는 CLI 가계부. 거래·카테고리·월 예산을 JSONL 파일로 관리합니다.",
        parents=[_common_parser()],
        allow_abbrev=False,
    )
    parser.set_defaults(data_dir=DEFAULT_DATA_DIR, verbose=False)
    commands = parser.add_subparsers(dest="command", required=True, metavar="<command>", title="명령")

    def add_command(group, name: str, help_text: str, handler, **kwargs) -> argparse.ArgumentParser:
        """서브명령 하나를 등록하고 실행 함수(handler)를 연결한다. 반환된 파서에 옵션을 추가한다."""
        sub = group.add_parser(
            name,
            help=help_text,
            description=help_text,
            parents=[_common_parser()],
            allow_abbrev=False,
            formatter_class=argparse.RawDescriptionHelpFormatter,
            **kwargs
        )
        sub.set_defaults(func=handler)
        return sub

    add_command(commands, "add", "대화형으로 거래를 입력합니다 (날짜·타입·카테고리·금액·메모·태그)", cmd_add)

    p = add_command(commands, "list", "최신순으로 거래를 출력합니다", cmd_list)
    p.add_argument("--limit", type=_positive_int, default=DEFAULT_LIST_LIMIT, metavar="N",
                   help="출력할 최대 건수 (기본: %d)" % DEFAULT_LIST_LIMIT)

    p = add_command(commands, "search", "조건(AND)에 맞는 거래를 최신순으로 검색합니다", cmd_search)
    p.add_argument("--from", dest="date_from", metavar="YYYY-MM-DD", help="시작일(포함)")
    p.add_argument("--to", dest="date_to", metavar="YYYY-MM-DD", help="종료일(포함)")
    p.add_argument("--category", metavar="C", help="카테고리")
    p.add_argument("--type", metavar="T", help="income 또는 expense")
    p.add_argument("--q", metavar="KEYWORD", help="메모에 포함된 키워드 (대소문자 무시)")
    p.add_argument("--tag", metavar="TAG", help="태그")
    p.add_argument("--limit", type=_positive_int, default=DEFAULT_LIST_LIMIT, metavar="N",
                   help="출력할 최대 건수 (기본: %d)" % DEFAULT_LIST_LIMIT)

    p = add_command(commands, "summary", "월별 수입·지출·잔액, 카테고리별 지출 TOP N, 예산 사용률", cmd_summary)
    p.add_argument("--month", required=True, metavar="YYYY-MM", help="집계할 월")
    p.add_argument("--top", type=_positive_int, default=3, metavar="N", help="지출 TOP N (기본: 3)")

    budget = add_command(commands, "budget", "월 예산을 설정/조회합니다", lambda a: 2)
    budget_cmds = budget.add_subparsers(dest="budget_command", required=True, metavar="<subcommand>", title="서브명령")
    p = add_command(budget_cmds, "set", "월 예산을 설정합니다 (같은 월은 덮어씀)", cmd_budget_set)
    p.add_argument("--month", required=True, metavar="YYYY-MM", help="예산 월")
    p.add_argument("--amount", required=True, metavar="N", help="예산 금액 (양의 정수, 1,500 형식 허용)")
    p = add_command(budget_cmds, "show", "월 예산을 조회합니다", cmd_budget_show)
    p.add_argument("--month", required=True, metavar="YYYY-MM", help="조회할 월")

    category = add_command(commands, "category", "카테고리를 관리합니다", lambda a: 2)
    category_cmds = category.add_subparsers(
        dest="category_command", required=True, metavar="<subcommand>", title="서브명령"
    )
    p = add_command(
        category_cmds,
        "add",
        "카테고리를 추가합니다 (이름은 앞뒤 공백 제거 후 소문자로 정규화: ' Food ' -> 'food', 한글은 공백만 제거)",
        cmd_category_add,
    )
    p.add_argument("name", help="카테고리 이름")
    add_command(category_cmds, "list", "등록된 카테고리를 출력합니다", cmd_category_list)
    p = add_command(
        category_cmds,
        "remove",
        "카테고리를 삭제합니다 (사용 중인 거래가 있으면 삭제할 수 없음)",
        cmd_category_remove,
    )
    p.add_argument("name", help="카테고리 이름 (정규화 규칙은 add 와 동일)")

    p = add_command(commands, "update", "지정한 필드만 수정합니다 (하나 이상 필요)", cmd_update)
    p.add_argument("--id", type=_positive_int, required=True, metavar="ID", help="수정할 거래 id")
    p.add_argument("--date", metavar="YYYY-MM-DD", help="새 날짜")
    p.add_argument("--type", metavar="T", help="income 또는 expense")
    p.add_argument("--category", metavar="C", help="새 카테고리 (등록된 것만)")
    p.add_argument("--amount", metavar="N", help="새 금액 (양의 정수)")
    p.add_argument("--memo", metavar="TEXT", help="새 메모 (빈 문자열이면 지움)")
    p.add_argument("--tags", metavar="a,b", help="새 태그, 쉼표 구분 (빈 문자열이면 지움)")

    p = add_command(commands, "delete", "거래를 삭제합니다", cmd_delete)
    p.add_argument("--id", type=_positive_int, required=True, metavar="ID", help="삭제할 거래 id")
    p.add_argument("--yes", action="store_true", help="확인 질문(y/N) 없이 삭제")

    p = add_command(
        commands,
        "import",
        "CSV에서 거래를 가져옵니다",
        cmd_import,
        epilog="CSV 스키마 (UTF-8, 헤더 필수): date,type,category,amount[,memo][,tags]\n"
        "tags 는 쉼표 구분 문자열이며 쉼표가 있으면 큰따옴표로 감쌉니다. 등록되지 않은 카테고리 행은 오류 처리됩니다.",
    )
    p.add_argument("--from", dest="csv_path", required=True, metavar="CSV", help="가져올 CSV 파일")

    p = add_command(
        commands,
        "export",
        "기간 내 거래를 CSV로 내보냅니다 (--month 또는 --from/--to 중 하나 이상 필수)",
        cmd_export,
    )
    p.add_argument("--out", required=True, metavar="CSV", help="생성할 CSV 파일 경로")
    p.add_argument("--month", metavar="YYYY-MM", help="내보낼 월")
    p.add_argument("--from", dest="date_from", metavar="YYYY-MM-DD", help="시작일(포함)")
    p.add_argument("--to", dest="date_to", metavar="YYYY-MM-DD", help="종료일(포함)")
    return parser


def main(argv: Optional[list] = None) -> int:
    """프로그램 진입점. 명령줄을 해석해 해당 명령 함수를 실행하고 종료 코드를 반환한다.

    argv 를 생략하면 실제 명령줄(sys.argv[1:])을 쓴다. 테스트에서는 리스트를 직접 넘긴다.
    종료 코드: 0 성공, 1 데이터/파일 오류, 2 입력·옵션 오류, 130 Ctrl+C
    """
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # --help(0) 또는 인자 오류(2)
        if exc.code is None:
            return 0
        return exc.code if isinstance(exc.code, int) else 2
    return args.func(args)
