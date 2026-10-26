import unittest

from budget_app.errors import CategoryInUseError, DuplicateError, NotFoundError, ValidationError
from budget_app.services import SearchFilter
from tests.helpers import TempDirTestCase


class AddValidationTest(TempDirTestCase):
    def test_rejects_bad_fields(self):
        ctx = self.ctx()
        base = dict(date="2026-03-01", type="expense", category="food", amount=1000)
        for field, bad in [
            ("date", "2026-02-30"),
            ("amount", "0"),
            ("amount", "-3"),
            ("amount", "12.5"),
            ("type", "spend"),
            ("category", "unknown"),
        ]:
            with self.subTest(field=field, bad=bad):
                with self.assertRaises(ValidationError):
                    ctx.transactions.add(**dict(base, **{field: bad}))
        self.assertEqual(list(ctx.transactions.repo.iter_all()), [])

    def test_unregistered_category_hint_lists_categories_and_category_add(self):
        ctx = self.ctx()
        with self.assertRaises(ValidationError) as cm:
            self.add_tx(ctx, category="game")
        self.assertIn("food", cm.exception.hint)
        self.assertIn("category add game", cm.exception.hint)

    def test_normalizes_category_amount_and_tags(self):
        ctx = self.ctx()
        tx = self.add_tx(ctx, category=" FOOD ", amount="1,500", tags="a, b", memo="점심")
        self.assertEqual((tx.category, tx.amount, tx.tags), ("food", 1500, ["a", "b"]))


class ListOrderTest(TempDirTestCase):
    def test_add_then_latest_order(self):
        ctx = self.ctx()
        self.add_tx(ctx, date="2026-03-05")  # id 1
        self.add_tx(ctx, date="2026-03-01")  # id 2 (과거 날짜 → 병합 삽입)
        self.add_tx(ctx, date="2026-03-05")  # id 3 (같은 날짜 → id 내림차순)
        self.add_tx(ctx, date="2026-03-09")  # id 4
        ids = [t.id for t in ctx.transactions.latest(10)]
        self.assertEqual(ids, [4, 3, 1, 2])
        self.assertEqual([t.id for t in ctx.transactions.latest(2)], [4, 3])

    def test_ids_increase_and_are_not_reused(self):
        ctx = self.ctx()
        a, b, c = (self.add_tx(ctx) for _ in range(3))
        self.assertEqual((a.id, b.id, c.id), (1, 2, 3))
        ctx.transactions.delete(3)
        self.assertEqual(self.add_tx(ctx).id, 3)  # 현재 최대 id + 1 규칙
        ctx.transactions.delete(1)
        self.assertEqual(self.add_tx(ctx).id, 4)  # 중간 id 는 재사용하지 않음

    def test_latest_is_lazy_generator(self):
        ctx = self.ctx()
        self.add_tx(ctx)
        self.assertTrue(hasattr(ctx.transactions.latest(5), "__next__"))


class SearchTest(TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.ctx_ = self.ctx()
        c = self.ctx_
        self.add_tx(c, date="2026-01-10", category="food", amount=1000, memo="Lunch Pizza", tags="meal")  # 1
        self.add_tx(c, date="2026-02-10", type="income", category="etc", amount=5000, memo="salary", tags="work")  # 2
        self.add_tx(c, date="2026-03-10", category="transport", amount=300, memo="bus", tags="Commute,work")  # 3
        self.add_tx(c, date="2026-03-20", category="food", amount=2000, memo="pizza night", tags="meal")  # 4

    def search(self, limit=20, **kw):
        flt = SearchFilter.from_raw(**kw)
        return [t.id for t in self.ctx_.transactions.search(flt, limit)]

    def test_no_filter_is_latest_first(self):
        self.assertEqual(self.search(), [4, 3, 2, 1])

    def test_date_range_inclusive(self):
        self.assertEqual(self.search(date_from="2026-02-10", date_to="2026-03-10"), [3, 2])
        self.assertEqual(self.search(date_from="2026-03-11"), [4])
        self.assertEqual(self.search(date_to="2026-01-31"), [1])

    def test_category_type_q_tag(self):
        self.assertEqual(self.search(category="FOOD"), [4, 1])
        self.assertEqual(self.search(type="income"), [2])
        self.assertEqual(self.search(q="PIZZA"), [4, 1])  # 대소문자 무시 부분일치
        self.assertEqual(self.search(q="izz"), [4, 1])
        self.assertEqual(self.search(tag="work"), [3, 2])
        self.assertEqual(self.search(tag="commute"), [3])

    def test_conditions_are_anded(self):
        self.assertEqual(self.search(category="food", q="pizza", date_from="2026-03-01"), [4])
        self.assertEqual(self.search(category="food", tag="work"), [])

    def test_limit(self):
        self.assertEqual(self.search(limit=2), [4, 3])

    def test_from_after_to_is_error(self):
        with self.assertRaises(ValidationError) as cm:
            SearchFilter.from_raw(date_from="2026-03-10", date_to="2026-03-01")
        self.assertEqual(cm.exception.exit_code, 2)

    def test_bad_filter_values(self):
        with self.assertRaises(ValidationError):
            SearchFilter.from_raw(date_from="2026-02-30")
        with self.assertRaises(ValidationError):
            SearchFilter.from_raw(type="x")


class SummaryTest(TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.ctx_ = c = self.ctx()
        self.add_tx(c, date="2026-02-28", amount=999999)  # 다른 달은 제외
        self.add_tx(c, date="2026-03-01", type="income", category="etc", amount=3000000)
        self.add_tx(c, date="2026-03-02", category="rent", amount=800000)
        self.add_tx(c, date="2026-03-03", category="food", amount=30000)
        self.add_tx(c, date="2026-03-04", category="food", amount=20000)
        self.add_tx(c, date="2026-03-05", category="transport", amount=10000)
        self.add_tx(c, date="2026-03-06", category="etc", amount=5000)
        self.add_tx(c, date="2026-04-01", amount=777777)  # 다른 달은 제외

    def test_totals_and_top(self):
        s = self.ctx_.summary.monthly("2026-03", 3)
        self.assertEqual((s.income, s.expense, s.balance, s.count), (3000000, 865000, 2135000, 6))
        self.assertEqual(s.top, [("rent", 800000), ("food", 50000), ("transport", 10000)])
        self.assertIsNone(s.budget)
        self.assertIsNone(s.usage_pct)

    def test_top_n(self):
        self.assertEqual(len(self.ctx_.summary.monthly("2026-03", 1).top), 1)
        self.assertEqual(len(self.ctx_.summary.monthly("2026-03", 10).top), 4)

    def test_budget_usage_and_over(self):
        self.ctx_.budgets.set("2026-03", 800000)
        s = self.ctx_.summary.monthly("2026-03")
        self.assertAlmostEqual(s.usage_pct, 108.125)
        self.assertEqual(s.over_amount, 65000)

    def test_budget_not_over(self):
        self.ctx_.budgets.set("2026-03", "1,000,000")
        s = self.ctx_.summary.monthly("2026-03")
        self.assertEqual(s.over_amount, 0)
        self.assertAlmostEqual(s.usage_pct, 86.5)

    def test_empty_month(self):
        s = self.ctx_.summary.monthly("2026-05")
        self.assertEqual(s.count, 0)

    def test_bad_month(self):
        with self.assertRaises(ValidationError):
            self.ctx_.summary.monthly("2026-13")


class CategoryServiceTest(TempDirTestCase):
    def test_defaults_created(self):
        self.assertEqual(self.ctx().categories.list(), ["food", "transport", "rent", "etc"])

    def test_add_duplicate_and_remove_missing(self):
        ctx = self.ctx()
        self.assertEqual(ctx.categories.add(" Game "), "game")
        with self.assertRaises(DuplicateError):
            ctx.categories.add("GAME")
        with self.assertRaises(DuplicateError):
            ctx.categories.add("food")
        with self.assertRaises(NotFoundError):
            ctx.categories.remove("nothing")

    def test_remove_blocked_when_in_use(self):
        ctx = self.ctx()
        self.add_tx(ctx, category="food")
        self.add_tx(ctx, category="food")
        with self.assertRaises(CategoryInUseError) as cm:
            ctx.categories.remove("food")
        self.assertIn("2건", cm.exception.message)
        self.assertIn("update", cm.exception.hint)
        self.assertIn("delete", cm.exception.hint)
        self.assertIn("food", ctx.categories.list())

    def test_remove_allowed_when_unused(self):
        ctx = self.ctx()
        ctx.categories.remove("rent")
        self.assertNotIn("rent", ctx.categories.list())


class UpdateDeleteServiceTest(TempDirTestCase):
    def test_update_only_given_fields(self):
        ctx = self.ctx()
        tx = self.add_tx(ctx, date="2026-03-01", amount=1000, memo="orig", tags="a")
        updated = ctx.transactions.update(tx.id, amount="2,500", category="Rent")
        self.assertEqual((updated.amount, updated.category), (2500, "rent"))
        self.assertEqual((updated.date, updated.memo, updated.tags), ("2026-03-01", "orig", ["a"]))

    def test_update_clear_memo_and_tags_with_empty_string(self):
        ctx = self.ctx()
        tx = self.add_tx(ctx, memo="x", tags="a,b")
        updated = ctx.transactions.update(tx.id, memo="", tags="")
        self.assertEqual((updated.memo, updated.tags), ("", []))

    def test_update_date_reorders(self):
        ctx = self.ctx()
        a = self.add_tx(ctx, date="2026-03-01")
        self.add_tx(ctx, date="2026-03-02")
        ctx.transactions.update(a.id, date="2026-03-09")
        self.assertEqual([t.id for t in ctx.transactions.latest(5)], [1, 2])

    def test_update_without_fields_is_error(self):
        ctx = self.ctx()
        tx = self.add_tx(ctx)
        with self.assertRaises(ValidationError):
            ctx.transactions.update(tx.id)

    def test_update_invalid_value_leaves_data(self):
        ctx = self.ctx()
        tx = self.add_tx(ctx, amount=1000)
        with self.assertRaises(ValidationError):
            ctx.transactions.update(tx.id, amount="0")
        with self.assertRaises(ValidationError):
            ctx.transactions.update(tx.id, category="nope")
        self.assertEqual(ctx.transactions.get(tx.id).amount, 1000)

    def test_missing_id(self):
        ctx = self.ctx()
        with self.assertRaises(NotFoundError) as cm:
            ctx.transactions.update(99, amount="5")
        self.assertIn("존재하지 않는 id", cm.exception.message)
        with self.assertRaises(NotFoundError):
            ctx.transactions.delete(99)


if __name__ == "__main__":
    unittest.main()
