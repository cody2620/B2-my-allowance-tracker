"""JSONL 파일 입출력 계층.

- transactions.jsonl 은 항상 (date, id) 오름차순으로 유지한다.
- 최신순 조회는 파일 끝에서부터 줄을 읽는 제너레이터로 구현해 전체를 메모리에 올리지 않는다.
- 파일 재작성은 임시 파일에 쓴 뒤 os.replace()로 원자적으로 교체한다.

이 파일의 역할: 데이터를 "어디에, 어떤 형식으로" 저장하는지만 책임진다.
업무 규칙(중복 카테고리 금지, 사용 중인 카테고리 삭제 금지 등)은 services.py 가 판단한다.

저장 형식 JSONL: 한 줄에 JSON 객체 하나. 예) {"id":1,"type":"expense","date":"2026-03-05",...}

구성:
- 파일 헬퍼 함수   ensure_file, atomic_write_lines, append_lines, iter_lines, iter_lines_reverse 등
- _JsonlStore      세 저장소의 공통 부모 (data_dir + 파일명 → 경로)
- TransactionRepository  data/transactions.jsonl  거래
- CategoryStore          data/categories.jsonl    카테고리 이름
- BudgetStore            data/budgets.jsonl       월 예산
"""
from __future__ import annotations

import heapq
import json
import os
import shutil
import tempfile
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from .errors import DataFileError, NotFoundError, ValidationError
from .models import DEFAULT_CATEGORIES, Budget, Transaction, normalize_category

_CHUNK_SIZE = 8192  # 파일을 거꾸로 읽을 때 한 번에 읽는 바이트 수


# ---------------------------------------------------------------- 파일 헬퍼
def ensure_file(path: str) -> None:
    """폴더와 파일이 없으면 만든다."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    if not os.path.exists(path):
        open(path, "a", encoding="utf-8").close()


def atomic_write_lines(path: str, lines: Iterable[str]) -> None:
    """임시 파일에 모두 쓴 뒤 os.replace()로 교체. 도중에 실패하면 원본은 그대로 남는다.

    파일 전체를 다시 써야 할 때(수정, 삭제, 중간에 끼워 넣기, export) 쓴다.
    lines 의 각 문자열 뒤에 줄바꿈을 붙여 쓴다. 기존 파일의 권한(mode)은 유지한다.
    """
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix="." + os.path.basename(path) + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as tmp:
            for line in lines:
                tmp.write(line)
                tmp.write("\n")
            tmp.flush()
            os.fsync(tmp.fileno())
        if os.path.exists(path):
            shutil.copymode(path, tmp_path)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def append_lines(path: str, lines: Iterable[str]) -> None:
    """파일 끝에 줄들을 덧붙인다. 파일 전체를 다시 쓰지 않아 빠르다.

    정렬 순서가 깨지지 않는 경우(새 거래가 가장 최신, 카테고리 추가)에만 쓴다.
    """
    payload = "".join(line + "\n" for line in lines).encode("utf-8")
    with open(path, "ab+") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        if size:
            f.seek(size - 1)
            if f.read(1) != b"\n":  # 직접 편집으로 끝 개행이 없는 경우 줄이 붙지 않게 보정
                payload = b"\n" + payload
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())


def iter_lines(path: str) -> Iterator[Tuple[int, str]]:
    """(줄 번호, 내용) 정방향 제너레이터. 빈 줄은 건너뛴다."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            for number, line in enumerate(f, start=1):
                text = line.strip()
                if text:
                    yield number, text
    except UnicodeDecodeError:
        raise DataFileError(
            "파일이 UTF-8 형식이 아닙니다: %s" % path,
            "파일이 손상되었거나 다른 인코딩일 수 있습니다. UTF-8로 저장하거나 --data-dir 로 다른 폴더를 지정하세요",
        )


def iter_lines_reverse(path: str, chunk_size: int = _CHUNK_SIZE) -> Iterator[Tuple[int, str]]:
    """(끝에서 n번째 줄, 내용) 역방향 제너레이터. 파일 끝에서 청크 단위로 읽는다.

    파일이 날짜 오름차순이므로 거꾸로 읽으면 최신 거래부터 나온다.
    list --limit 5 처럼 앞쪽 몇 건만 필요할 때 파일 전체를 읽지 않고 멈출 수 있다.
    """
    try:
        f = open(path, "rb")
    except FileNotFoundError:
        return
    with f:
        f.seek(0, os.SEEK_END)
        position = f.tell()
        buffer = b""
        count = 0
        while position > 0:
            size = min(chunk_size, position)
            position -= size
            f.seek(position)
            buffer = f.read(size) + buffer
            parts = buffer.split(b"\n")
            buffer = parts[0]  # 아직 줄의 앞부분이 안 읽혔을 수 있는 조각
            for raw in reversed(parts[1:]):
                if raw.strip():
                    count += 1
                    yield count, _decode_bytes(raw, path)
        if buffer.strip():
            count += 1
            yield count, _decode_bytes(buffer, path)


def _decode_bytes(raw: bytes, path: str) -> str:
    """바이트 한 줄을 UTF-8 문자열로 바꾼다. 실패하면 DataFileError (iter_lines_reverse 용)."""
    try:
        return raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        raise DataFileError(
            "파일이 UTF-8 형식이 아닙니다: %s" % path,
            "파일이 손상되었거나 다른 인코딩일 수 있습니다. UTF-8로 저장하거나 --data-dir 로 다른 폴더를 지정하세요",
        )


def _dumps(data: Dict[str, Any]) -> str:
    """dict → JSON 한 줄. 한글을 \\uXXXX 로 바꾸지 않고, 공백 없이 짧게 쓴다."""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _loads(text: str, path: str, where: str) -> Dict[str, Any]:
    """JSON 한 줄 → dict. 깨졌거나 객체가 아니면 몇 번째 줄인지(where) 알려주는 DataFileError."""
    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("JSON 객체가 아닙니다")
        return data
    except ValueError as exc:
        raise DataFileError(
            "저장 파일의 %s이(가) 손상되었습니다 (%s): %s" % (where, exc, path),
            "해당 줄을 직접 수정하거나 삭제한 뒤 다시 실행하세요",
        )


class _JsonlStore:
    """JSONL 저장소의 공통 부모. 자식 클래스는 filename 만 정하면 된다.

    생성하면 data_dir/filename 경로를 만들고, 폴더·파일이 없으면 빈 파일을 만든다.
    """

    filename = ""

    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, self.filename)
        ensure_file(self.path)


# ---------------------------------------------------------------- 거래
class TransactionRepository(_JsonlStore):
    """거래 저장소 (data/transactions.jsonl).

    파일은 항상 (date, id) 오름차순. 그래서
    - 정방향으로 읽으면 오래된 순 (iter_all)
    - 역방향으로 읽으면 최신순 (iter_latest)
    읽기는 모두 제너레이터라서 거래가 많아도 한 건씩 처리한다.
    """

    filename = "transactions.jsonl"

    @staticmethod
    def _encode(tx: Transaction) -> str:
        """Transaction → 저장할 JSON 한 줄."""
        return _dumps(tx.to_dict())

    def _decode(self, text: str, where: str) -> Transaction:
        """저장된 JSON 한 줄 → Transaction. 내용이 잘못됐으면 위치(where)를 담은 DataFileError."""
        data = _loads(text, self.path, where)
        try:
            return Transaction.from_dict(data)
        except (ValidationError, KeyError, TypeError, ValueError) as exc:
            detail = exc.message if isinstance(exc, ValidationError) else "필드 오류 %r" % (exc,)
            raise DataFileError(
                "저장 파일의 %s이(가) 올바르지 않습니다 (%s): %s" % (where, detail, self.path),
                "해당 줄을 직접 수정하거나 삭제한 뒤 다시 실행하세요",
            )

    def _iter_forward(self) -> Iterator[Tuple[Transaction, str]]:
        """(Transaction, 원본 JSON 줄) 을 오래된 순으로. 파일을 다시 쓸 때 원본 줄을 그대로 재사용한다."""
        for number, text in iter_lines(self.path):
            yield self._decode(text, "%d번째 줄" % number), text

    def iter_all(self) -> Iterator[Transaction]:
        """(date, id) 오름차순."""
        for tx, _ in self._iter_forward():
            yield tx

    def iter_latest(self) -> Iterator[Transaction]:
        """최신순(date 내림차순, id 내림차순). 파일 끝에서부터 읽는다."""
        for number, text in iter_lines_reverse(self.path):
            yield self._decode(text, "끝에서 %d번째 줄" % number)

    def last_key(self) -> Optional[Tuple[str, int]]:
        """파일 맨 끝(가장 최신) 거래의 (date, id). 파일이 비어 있으면 None.

        add_many 가 '새 거래를 끝에 붙여도 정렬이 유지되는가'를 판단할 때 쓴다.
        """
        for tx in self.iter_latest():
            return tx.sort_key
        return None

    def max_id(self) -> int:
        """지금까지 쓰인 가장 큰 id (없으면 0). 새 거래의 id 는 이 값 + 1 이 된다.

        파일이 날짜순이라 마지막 줄의 id 가 최대라는 보장이 없어서 전체를 훑는다.
        """
        return max((tx.id for tx in self.iter_all()), default=0)

    def get(self, tx_id: int) -> Transaction:
        """id 로 거래 하나를 찾는다. 없으면 NotFoundError."""
        for tx in self.iter_all():
            if tx.id == tx_id:
                return tx
        raise _not_found(tx_id)

    def add(self, tx: Transaction) -> None:
        """거래 하나를 저장한다 (add_many 에 위임). tx.id 는 호출하는 쪽에서 정해서 넘긴다."""
        self.add_many([tx])

    def add_many(self, txs: Iterable[Transaction]) -> None:
        """새 거래들을 정렬 순서를 유지하며 반영한다 (끝에 붙일 수 있으면 append, 아니면 병합 후 교체).

        - 빠른 길: 새 거래가 모두 기존 마지막 거래보다 늦거나 같음 → append_lines 로 끝에 붙임
        - 느린 길: 과거 날짜가 섞여 있음 → 기존 줄과 새 줄을 heapq.merge 로 정렬 병합해
                   atomic_write_lines 로 파일 전체를 다시 씀
        add(거래 하나)와 import(여러 건)가 함께 쓴다.
        """
        new = sorted(txs, key=lambda t: t.sort_key)
        if not new:
            return
        last = self.last_key()
        if last is None or new[0].sort_key >= last:
            append_lines(self.path, (self._encode(t) for t in new))
            return
        existing = ((tx.sort_key, raw) for tx, raw in self._iter_forward())
        incoming = ((t.sort_key, self._encode(t)) for t in new)
        merged = heapq.merge(existing, incoming, key=lambda pair: pair[0])
        atomic_write_lines(self.path, (raw for _, raw in merged))

    def replace(self, tx: Transaction) -> None:
        """같은 id의 거래를 tx로 바꾼다 (date가 바뀌어도 정렬 유지)."""
        self._rewrite(tx.id, tx)

    def delete(self, tx_id: int) -> None:
        """id 에 해당하는 거래를 지운다. 없으면 NotFoundError."""
        self._rewrite(tx_id, None)

    def _rewrite(self, tx_id: int, replacement: Optional[Transaction]) -> None:
        """replace 와 delete 의 공통 구현: 파일 전체를 다시 쓰면서 tx_id 줄을 빼거나 바꾼다.

        replacement 가 None 이면 삭제, 아니면 그 거래를 (date, id) 순서에 맞는 자리에 넣는다.
        tx_id 가 파일에 없으면 NotFoundError 를 내고, 이 경우 원본 파일은 바뀌지 않는다.
        """
        found = False

        def existing() -> Iterator[Tuple[Tuple[str, int], str]]:
            nonlocal found
            for tx, raw in self._iter_forward():
                if tx.id == tx_id:
                    found = True
                    continue
                yield tx.sort_key, raw
            if not found:
                raise _not_found(tx_id)

        streams = [existing()]
        if replacement is not None:
            streams.append(iter([(replacement.sort_key, self._encode(replacement))]))
        merged = heapq.merge(*streams, key=lambda pair: pair[0])
        atomic_write_lines(self.path, (raw for _, raw in merged))


def _not_found(tx_id: int) -> NotFoundError:
    """'존재하지 않는 id' 예외를 만든다 (get, _rewrite 가 같은 문구를 쓰도록)."""
    return NotFoundError(
        "존재하지 않는 id입니다: %s" % tx_id,
        "list 또는 search 명령으로 id를 확인하세요",
    )


# ---------------------------------------------------------------- 카테고리
class CategoryStore(_JsonlStore):
    """카테고리 저장소 (data/categories.jsonl). 한 줄에 {"name": "식비"} 하나.

    읽고 쓰기만 한다. 중복 금지·사용 중 삭제 금지 같은 규칙은 services.CategoryService 가 지킨다.
    """

    filename = "categories.jsonl"

    def names(self) -> List[str]:
        """등록된 카테고리 이름 목록 (파일에 적힌 순서, 정규화된 이름)."""
        result: List[str] = []
        for number, text in iter_lines(self.path):
            data = _loads(text, self.path, "%d번째 줄" % number)
            try:
                result.append(normalize_category(data["name"]))
            except (KeyError, ValidationError):
                raise DataFileError(
                    "저장 파일의 %d번째 줄에 카테고리 이름이 없습니다: %s" % (number, self.path),
                    "해당 줄을 직접 수정하거나 삭제한 뒤 다시 실행하세요",
                )
        return result

    def exists(self, name: str) -> bool:
        """이름이 등록돼 있는지 (정규화한 뒤 비교)."""
        return normalize_category(name) in self.names()

    def ensure_defaults(self) -> bool:
        """비어 있으면 기본 카테고리를 만들고 True를 반환한다."""
        if self.names():
            return False
        atomic_write_lines(self.path, (_dumps({"name": n}) for n in DEFAULT_CATEGORIES))
        return True

    def add(self, name: str) -> None:
        """파일 끝에 카테고리 한 줄을 추가한다 (중복 검사는 하지 않음)."""
        append_lines(self.path, [_dumps({"name": normalize_category(name)})])

    def remove(self, name: str) -> None:
        """해당 이름을 뺀 나머지로 파일을 다시 쓴다."""
        target = normalize_category(name)
        atomic_write_lines(self.path, (_dumps({"name": n}) for n in self.names() if n != target))


# ---------------------------------------------------------------- 예산
class BudgetStore(_JsonlStore):
    """월 예산 저장소 (data/budgets.jsonl). 한 줄에 {"month": "2026-03", "amount": 500000} 하나.

    월마다 최대 한 줄이며, 파일은 월 오름차순으로 유지한다.
    """

    filename = "budgets.jsonl"

    def all(self) -> Dict[str, Budget]:
        """모든 예산을 {월: Budget} dict 로 읽는다. 같은 월이 여러 줄이면 뒤쪽 줄이 이긴다."""
        result: Dict[str, Budget] = {}
        for number, text in iter_lines(self.path):
            data = _loads(text, self.path, "%d번째 줄" % number)
            try:
                budget = Budget.from_dict(data)
            except (KeyError, TypeError, ValidationError):
                raise DataFileError(
                    "저장 파일의 %d번째 줄이 올바른 예산 형식이 아닙니다: %s" % (number, self.path),
                    "해당 줄을 직접 수정하거나 삭제한 뒤 다시 실행하세요",
                )
            result[budget.month] = budget
        return result

    def get(self, month: str) -> Optional[Budget]:
        """해당 월의 예산. 없으면 None."""
        return self.all().get(month)

    def set(self, budget: Budget) -> Optional[Budget]:
        """저장하고 덮어쓴 이전 예산(없으면 None)을 반환한다."""
        budgets = self.all()
        previous = budgets.get(budget.month)
        budgets[budget.month] = budget
        atomic_write_lines(self.path, (_dumps(budgets[m].to_dict()) for m in sorted(budgets)))
        return previous
