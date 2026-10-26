"""JSONL 파일 입출력 계층.

- transactions.jsonl 은 항상 (date, id) 오름차순으로 유지한다.
- 최신순 조회는 파일 끝에서부터 줄을 읽는 제너레이터로 구현해 전체를 메모리에 올리지 않는다.
- 파일 재작성은 임시 파일에 쓴 뒤 os.replace()로 원자적으로 교체한다.
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

_CHUNK_SIZE = 8192


# ---------------------------------------------------------------- 파일 헬퍼
def ensure_file(path: str) -> None:
    """폴더와 파일이 없으면 만든다."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    if not os.path.exists(path):
        open(path, "a", encoding="utf-8").close()


def atomic_write_lines(path: str, lines: Iterable[str]) -> None:
    """임시 파일에 모두 쓴 뒤 os.replace()로 교체. 도중에 실패하면 원본은 그대로 남는다."""
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
    """(끝에서 n번째 줄, 내용) 역방향 제너레이터. 파일 끝에서 청크 단위로 읽는다."""
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
    try:
        return raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        raise DataFileError(
            "파일이 UTF-8 형식이 아닙니다: %s" % path,
            "파일이 손상되었거나 다른 인코딩일 수 있습니다. UTF-8로 저장하거나 --data-dir 로 다른 폴더를 지정하세요",
        )


def _dumps(data: Dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _loads(text: str, path: str, where: str) -> Dict[str, Any]:
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
    filename = ""

    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, self.filename)
        ensure_file(self.path)


# ---------------------------------------------------------------- 거래
class TransactionRepository(_JsonlStore):
    filename = "transactions.jsonl"

    @staticmethod
    def _encode(tx: Transaction) -> str:
        return _dumps(tx.to_dict())

    def _decode(self, text: str, where: str) -> Transaction:
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
        for tx in self.iter_latest():
            return tx.sort_key
        return None

    def max_id(self) -> int:
        return max((tx.id for tx in self.iter_all()), default=0)

    def get(self, tx_id: int) -> Transaction:
        for tx in self.iter_all():
            if tx.id == tx_id:
                return tx
        raise _not_found(tx_id)

    def add(self, tx: Transaction) -> None:
        self.add_many([tx])

    def add_many(self, txs: Iterable[Transaction]) -> None:
        """새 거래들을 정렬 순서를 유지하며 반영한다 (끝에 붙일 수 있으면 append, 아니면 병합 후 교체)."""
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
        self._rewrite(tx_id, None)

    def _rewrite(self, tx_id: int, replacement: Optional[Transaction]) -> None:
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
    return NotFoundError(
        "존재하지 않는 id입니다: %s" % tx_id,
        "list 또는 search 명령으로 id를 확인하세요",
    )


# ---------------------------------------------------------------- 카테고리
class CategoryStore(_JsonlStore):
    filename = "categories.jsonl"

    def names(self) -> List[str]:
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
        return normalize_category(name) in self.names()

    def ensure_defaults(self) -> bool:
        """비어 있으면 기본 카테고리를 만들고 True를 반환한다."""
        if self.names():
            return False
        atomic_write_lines(self.path, (_dumps({"name": n}) for n in DEFAULT_CATEGORIES))
        return True

    def add(self, name: str) -> None:
        append_lines(self.path, [_dumps({"name": normalize_category(name)})])

    def remove(self, name: str) -> None:
        target = normalize_category(name)
        atomic_write_lines(self.path, (_dumps({"name": n}) for n in self.names() if n != target))


# ---------------------------------------------------------------- 예산
class BudgetStore(_JsonlStore):
    filename = "budgets.jsonl"

    def all(self) -> Dict[str, Budget]:
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
        return self.all().get(month)

    def set(self, budget: Budget) -> Optional[Budget]:
        """저장하고 덮어쓴 이전 예산(없으면 None)을 반환한다."""
        budgets = self.all()
        previous = budgets.get(budget.month)
        budgets[budget.month] = budget
        atomic_write_lines(self.path, (_dumps(budgets[m].to_dict()) for m in sorted(budgets)))
        return previous
