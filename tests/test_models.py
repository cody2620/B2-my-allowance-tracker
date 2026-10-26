import unittest

from budget_app.errors import ValidationError
from budget_app.models import (
    Transaction,
    month_range,
    normalize_category,
    parse_amount,
    parse_date,
    parse_month,
    parse_tags,
    parse_type,
)


class ParseDateTest(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(parse_date("2026-03-05"), "2026-03-05")
        self.assertEqual(parse_date(" 2024-02-29 "), "2024-02-29")

    def test_invalid(self):
        for bad in ["2026-02-30", "2026-13-01", "2026/03/05", "2026-3-5", "abc", "", None, "2025-02-29"]:
            with self.subTest(bad=bad):
                with self.assertRaises(ValidationError) as cm:
                    parse_date(bad)
                self.assertTrue(cm.exception.hint)
                self.assertEqual(cm.exception.exit_code, 2)


class ParseMonthTest(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(parse_month("2026-03"), "2026-03")

    def test_invalid(self):
        for bad in ["2026-13", "2026-00", "2026-3", "202603", "2026-03-01", "", None]:
            with self.subTest(bad=bad):
                with self.assertRaises(ValidationError):
                    parse_month(bad)

    def test_month_range(self):
        self.assertEqual(month_range("2024-02"), ("2024-02-01", "2024-02-29"))
        self.assertEqual(month_range("2026-12"), ("2026-12-01", "2026-12-31"))


class ParseAmountTest(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(parse_amount("1500"), 1500)
        self.assertEqual(parse_amount("1,500"), 1500)
        self.assertEqual(parse_amount(" 1,234,567 "), 1234567)
        self.assertEqual(parse_amount(30), 30)

    def test_invalid(self):
        for bad in ["0", "-5", "abc", "1.5", "1,50", "1,5000", "", None, 0, -1, True, 1.5, "9" * 30]:
            with self.subTest(bad=bad):
                with self.assertRaises(ValidationError):
                    parse_amount(bad)


class ParseTypeCategoryTagsTest(unittest.TestCase):
    def test_type(self):
        self.assertEqual(parse_type("Income"), "income")
        self.assertEqual(parse_type(" expense "), "expense")
        for bad in ["", "spend", None]:
            with self.assertRaises(ValidationError):
                parse_type(bad)

    def test_category_normalization(self):
        self.assertEqual(normalize_category("  Food "), "food")
        with self.assertRaises(ValidationError):
            normalize_category("   ")

    def test_tags(self):
        self.assertEqual(parse_tags(" a, b ,,A,c"), ["a", "b", "c"])
        self.assertEqual(parse_tags(""), [])
        self.assertEqual(parse_tags(None), [])
        self.assertEqual(parse_tags(["x", " y "]), ["x", "y"])


class TransactionModelTest(unittest.TestCase):
    def test_roundtrip(self):
        tx = Transaction(id=1, type="expense", date="2026-03-01", amount=1000, category="food", memo="점심", tags=["a"])
        self.assertEqual(Transaction.from_dict(tx.to_dict()), tx)

    def test_defaults(self):
        tx = Transaction(id=1, type="income", date="2026-03-01", amount=1, category="etc")
        self.assertEqual((tx.memo, tx.tags), ("", []))

    def test_from_dict_rejects_bad_record(self):
        with self.assertRaises(ValidationError):
            Transaction.from_dict({"id": 1, "type": "x", "date": "2026-03-01", "amount": 1, "category": "a"})
        with self.assertRaises(KeyError):
            Transaction.from_dict({"id": 1})


if __name__ == "__main__":
    unittest.main()
