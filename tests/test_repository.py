import json
import os
import unittest
from unittest import mock

from budget_app.errors import DataFileError, NotFoundError
from budget_app.models import Transaction
from budget_app.repository import (
    BudgetStore,
    CategoryStore,
    TransactionRepository,
    atomic_write_lines,
    iter_lines_reverse,
)
from tests.helpers import TempDirTestCase


def make_tx(tx_id, date, **kw):
    fields = dict(type="expense", amount=100, category="food", memo="", tags=[])
    fields.update(kw)
    return Transaction(id=tx_id, date=date, **fields)


class ReverseReaderTest(TempDirTestCase):
    def test_reverse_lines_across_chunks(self):
        path = self.path("lines.txt")
        lines = ["줄-%03d-%s" % (i, "가" * (i % 7)) for i in range(200)]
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        # 아주 작은 청크로 읽어 멀티바이트/줄 경계를 가로지르게 한다
        got = [text for _, text in iter_lines_reverse(path, chunk_size=7)]
        self.assertEqual(got, list(reversed(lines)))

    def test_no_trailing_newline_and_blank_lines(self):
        path = self.path("lines.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("a\n\nb\n\nc")
        self.assertEqual([t for _, t in iter_lines_reverse(path, chunk_size=2)], ["c", "b", "a"])

    def test_missing_or_empty_file(self):
        self.assertEqual(list(iter_lines_reverse(self.path("none.txt"))), [])
        open(self.path("empty.txt"), "w").close()
        self.assertEqual(list(iter_lines_reverse(self.path("empty.txt"))), [])


class TransactionRepositoryTest(TempDirTestCase):
    def repo(self):
        return TransactionRepository(self.data_dir)

    def test_creates_dir_and_files(self):
        self.repo()
        BudgetStore(self.data_dir)
        CategoryStore(self.data_dir)
        for name in ("transactions.jsonl", "categories.jsonl", "budgets.jsonl"):
            self.assertTrue(os.path.isfile(os.path.join(self.data_dir, name)))

    def test_append_when_not_older(self):
        repo = self.repo()
        repo.add(make_tx(1, "2026-03-01"))
        repo.add(make_tx(2, "2026-03-01"))
        repo.add(make_tx(3, "2026-03-05"))
        self.assertEqual([t.id for t in repo.iter_all()], [1, 2, 3])
        self.assertEqual([t.id for t in repo.iter_latest()], [3, 2, 1])

    def test_merge_insert_keeps_sorted(self):
        repo = self.repo()
        repo.add(make_tx(1, "2026-03-01"))
        repo.add(make_tx(2, "2026-03-10"))
        repo.add(make_tx(3, "2026-03-05"))  # 중간에 끼워 넣기
        repo.add(make_tx(4, "2026-02-01"))  # 맨 앞
        keys = [t.sort_key for t in repo.iter_all()]
        self.assertEqual(keys, sorted(keys))
        self.assertEqual([t.id for t in repo.iter_all()], [4, 1, 3, 2])

    def test_add_many_single_merge(self):
        repo = self.repo()
        repo.add(make_tx(1, "2026-03-01"))
        repo.add(make_tx(2, "2026-03-20"))
        repo.add_many([make_tx(5, "2026-03-10"), make_tx(3, "2026-03-25"), make_tx(4, "2026-03-10")])
        self.assertEqual([t.id for t in repo.iter_all()], [1, 4, 5, 2, 3])

    def test_replace_moves_when_date_changes(self):
        repo = self.repo()
        for i, d in enumerate(["2026-03-01", "2026-03-02", "2026-03-03"], start=1):
            repo.add(make_tx(i, d))
        repo.replace(make_tx(1, "2026-03-09", amount=777))
        self.assertEqual([t.id for t in repo.iter_all()], [2, 3, 1])
        self.assertEqual(repo.get(1).amount, 777)

    def test_delete_and_not_found(self):
        repo = self.repo()
        repo.add(make_tx(1, "2026-03-01"))
        repo.delete(1)
        self.assertEqual(list(repo.iter_all()), [])
        with self.assertRaises(NotFoundError):
            repo.delete(1)
        with self.assertRaises(NotFoundError):
            repo.replace(make_tx(9, "2026-03-01"))
        with self.assertRaises(NotFoundError):
            repo.get(9)

    def test_max_id_never_reuses_when_not_max_deleted(self):
        repo = self.repo()
        for i in (1, 2, 3):
            repo.add(make_tx(i, "2026-03-01"))
        repo.delete(2)
        self.assertEqual(repo.max_id(), 3)

    def test_corrupt_line_raises_data_file_error(self):
        repo = self.repo()
        repo.add(make_tx(1, "2026-03-01"))
        with open(repo.path, "a", encoding="utf-8") as f:
            f.write("{not json}\n")
        with self.assertRaises(DataFileError):
            list(repo.iter_latest())
        with self.assertRaises(DataFileError):
            list(repo.iter_all())

    def test_missing_trailing_newline_does_not_merge_lines(self):
        repo = self.repo()
        repo.add(make_tx(1, "2026-03-01"))
        with open(repo.path, "rb+") as f:
            f.seek(-1, os.SEEK_END)
            f.truncate()  # 끝 개행 제거
        repo.add(make_tx(2, "2026-03-02"))
        self.assertEqual([t.id for t in repo.iter_all()], [1, 2])


class AtomicWriteTest(TempDirTestCase):
    def test_failure_keeps_original_and_no_temp_left(self):
        repo = TransactionRepository(self.data_dir)
        repo.add(make_tx(1, "2026-03-01"))
        repo.add(make_tx(2, "2026-03-02"))
        before = self.read_lines("transactions.jsonl")

        with mock.patch("budget_app.repository.os.replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                repo.delete(1)
        self.assertEqual(self.read_lines("transactions.jsonl"), before)
        self.assertEqual(os.listdir(self.data_dir), ["transactions.jsonl"])  # 임시 파일 잔존 없음

    def test_failure_while_streaming_keeps_original(self):
        path = self.path("f.jsonl")
        atomic_write_lines(path, ["a", "b"])

        def broken():
            yield "x"
            raise RuntimeError("중간 실패")

        with self.assertRaises(RuntimeError):
            atomic_write_lines(path, broken())
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "a\nb\n")
        self.assertEqual(os.listdir(self.tmp), ["f.jsonl"])

    def test_file_is_valid_jsonl_after_rewrites(self):
        repo = TransactionRepository(self.data_dir)
        for i, d in enumerate(["2026-03-05", "2026-03-01", "2026-03-03", "2026-03-02"], start=1):
            repo.add(make_tx(i, d))
        repo.replace(make_tx(2, "2026-03-04", amount=5))
        repo.delete(3)
        rows = [json.loads(line) for line in self.read_lines("transactions.jsonl")]
        self.assertEqual([r["id"] for r in rows], [4, 2, 1])
        self.assertEqual([(r["date"], r["id"]) for r in rows], sorted((r["date"], r["id"]) for r in rows))


class SmallStoresTest(TempDirTestCase):
    def test_category_defaults_and_crud(self):
        store = CategoryStore(self.data_dir)
        self.assertTrue(store.ensure_defaults())
        self.assertEqual(store.names(), ["food", "transport", "rent", "etc"])
        self.assertFalse(store.ensure_defaults())
        store.add(" Game ")
        self.assertTrue(store.exists("GAME"))
        store.remove("game")
        self.assertFalse(store.exists("game"))

    def test_budget_overwrite(self):
        store = BudgetStore(self.data_dir)
        from budget_app.models import Budget

        self.assertIsNone(store.set(Budget("2026-03", 100)))
        previous = store.set(Budget("2026-03", 200))
        self.assertEqual(previous.amount, 100)
        self.assertEqual(store.get("2026-03").amount, 200)
        self.assertEqual(len(self.read_lines("budgets.jsonl")), 1)


if __name__ == "__main__":
    unittest.main()
