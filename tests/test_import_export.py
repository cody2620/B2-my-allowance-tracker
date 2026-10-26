import csv
import os
import unittest

from budget_app.errors import DataFileError, ValidationError
from tests.helpers import TempDirTestCase


class ExportTest(TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.ctx_ = c = self.ctx()
        self.add_tx(c, date="2026-02-28", amount=1)
        self.add_tx(c, date="2026-03-01", amount=2000, memo="점심, 맛집", tags="a,b")
        self.add_tx(c, date="2026-03-31", type="income", category="etc", amount=3000)
        self.add_tx(c, date="2026-04-01", amount=4)

    def read_csv(self, path):
        with open(path, encoding="utf-8", newline="") as f:
            return list(csv.reader(f))

    def test_requires_period(self):
        out = self.path("out.csv")
        with self.assertRaises(ValidationError) as cm:
            self.ctx_.io.export_csv(out)
        self.assertEqual(cm.exception.exit_code, 2)
        self.assertIn("--month", cm.exception.hint)
        self.assertFalse(os.path.exists(out))

    def test_month(self):
        out = self.path("out.csv")
        self.assertEqual(self.ctx_.io.export_csv(out, month="2026-03"), 2)
        rows = self.read_csv(out)
        self.assertEqual(rows[0], ["date", "type", "category", "amount", "memo", "tags"])
        self.assertEqual(rows[1], ["2026-03-01", "expense", "food", "2000", "점심, 맛집", "a,b"])
        self.assertEqual(rows[2][:4], ["2026-03-31", "income", "etc", "3000"])

    def test_range_and_open_ended(self):
        out = self.path("out.csv")
        self.assertEqual(self.ctx_.io.export_csv(out, date_from="2026-03-01", date_to="2026-03-31"), 2)
        self.assertEqual(self.ctx_.io.export_csv(out, date_from="2026-03-31"), 2)
        self.assertEqual(self.ctx_.io.export_csv(out, date_to="2026-02-28"), 1)

    def test_empty_result_still_has_header(self):
        out = self.path("sub", "out.csv")
        self.assertEqual(self.ctx_.io.export_csv(out, month="2025-01"), 0)
        self.assertEqual(len(self.read_csv(out)), 1)

    def test_from_after_to(self):
        with self.assertRaises(ValidationError):
            self.ctx_.io.export_csv(self.path("o.csv"), date_from="2026-03-10", date_to="2026-03-01")

    def test_utf8_without_bom(self):
        out = self.path("out.csv")
        self.ctx_.io.export_csv(out, month="2026-03")
        with open(out, "rb") as f:
            self.assertFalse(f.read(3) == b"\xef\xbb\xbf")


class ImportTest(TempDirTestCase):
    def write_csv(self, text, encoding="utf-8"):
        path = self.path("in.csv")
        with open(path, "w", encoding=encoding, newline="") as f:
            f.write(text)
        return path

    def test_valid_rows_applied_and_bad_rows_reported_with_line_numbers(self):
        ctx = self.ctx()
        path = self.write_csv(
            "date,type,category,amount,memo,tags\n"
            "2026-03-01,expense,food,\"1,500\",점심,\"a,b\"\n"  # 2: ok
            "2026-02-30,expense,food,1000,,\n"  # 3: 날짜
            "2026-03-02,spend,food,1000,,\n"  # 4: type
            "\n"  # 5: 빈 줄
            "2026-03-03,expense,game,1000,,\n"  # 6: 미등록 카테고리
            "2026-03-04,expense,food,0,,\n"  # 7: 금액
            "2026-03-05,income,etc,3000\n"  # 8: ok (memo/tags 열 생략)
            "2026-03-06,expense,food,abc,,\n"  # 9: 금액
        )
        result = ctx.io.import_csv(path)
        self.assertEqual((result.success, result.failed, result.skipped), (2, 5, 1))
        self.assertEqual([line for line, _ in result.errors], [3, 4, 6, 7, 9])
        reasons = dict(result.errors)
        self.assertIn("날짜", reasons[3])
        self.assertIn("type", reasons[4])
        self.assertIn("카테고리", reasons[6])
        self.assertIn("금액", reasons[7])
        txs = list(ctx.transactions.repo.iter_all())
        self.assertEqual([(t.date, t.amount, t.memo, t.tags) for t in txs],
                         [("2026-03-01", 1500, "점심", ["a", "b"]), ("2026-03-05", 3000, "", [])])
        self.assertNotIn("game", ctx.categories.list())  # 자동 생성 안 함

    def test_bom_and_ids_continue_and_sorted_merge(self):
        ctx = self.ctx()
        self.add_tx(ctx, date="2026-03-10")  # id 1
        path = self.write_csv(
            "date,type,category,amount\n2026-03-20,expense,food,5\n2026-03-01,expense,food,6\n",
            encoding="utf-8-sig",
        )
        result = ctx.io.import_csv(path)
        self.assertEqual(result.success, 2)
        txs = list(ctx.transactions.repo.iter_all())
        self.assertEqual([(t.date, t.id) for t in txs],
                         [("2026-03-01", 3), ("2026-03-10", 1), ("2026-03-20", 2)])

    def test_missing_required_header(self):
        ctx = self.ctx()
        path = self.write_csv("date,type,amount\n2026-03-01,expense,5\n")
        with self.assertRaises(DataFileError) as cm:
            ctx.io.import_csv(path)
        self.assertIn("category", cm.exception.message)

    def test_empty_file_and_missing_file(self):
        ctx = self.ctx()
        with self.assertRaises(DataFileError):
            ctx.io.import_csv(self.write_csv(""))
        with self.assertRaises(FileNotFoundError):
            ctx.io.import_csv(self.path("nope.csv"))

    def test_too_many_columns_is_failed_row(self):
        ctx = self.ctx()
        path = self.write_csv("date,type,category,amount,memo,tags\n2026-03-01,expense,food,5,a,b,c\n")
        result = ctx.io.import_csv(path)
        self.assertEqual((result.success, result.failed), (0, 1))

    def test_all_invalid_leaves_file_untouched(self):
        ctx = self.ctx()
        self.add_tx(ctx)
        before = self.read_lines("transactions.jsonl")
        ctx.io.import_csv(self.write_csv("date,type,category,amount\nbad,expense,food,5\n"))
        self.assertEqual(self.read_lines("transactions.jsonl"), before)

    def test_roundtrip_export_import(self):
        ctx = self.ctx()
        self.add_tx(ctx, date="2026-03-01", memo="a, \"b\"", tags="x,y", amount=1234)
        out = self.path("rt.csv")
        ctx.io.export_csv(out, month="2026-03")
        ctx2_dir = self.path("other")
        os.makedirs(ctx2_dir)
        from budget_app.cli import AppContext
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()):
            other = AppContext(ctx2_dir)
        self.assertEqual(other.io.import_csv(out).success, 1)
        tx = next(other.transactions.repo.iter_all())
        self.assertEqual((tx.memo, tx.tags, tx.amount), ('a, "b"', ["x", "y"], 1234))


if __name__ == "__main__":
    unittest.main()
