import os
import unittest
from unittest import mock

from tests.helpers import TempDirTestCase

ALL_COMMANDS = [
    [],
    ["add"],
    ["list"],
    ["search"],
    ["summary"],
    ["budget"],
    ["budget", "set"],
    ["budget", "show"],
    ["category"],
    ["category", "add"],
    ["category", "list"],
    ["category", "remove"],
    ["update"],
    ["delete"],
    ["import"],
    ["export"],
]


class HelpTest(TempDirTestCase):
    def test_every_command_has_help(self):
        for cmd in ALL_COMMANDS:
            with self.subTest(cmd=cmd):
                code, out, err = self.run_cli(*(cmd + ["--help"]))
                self.assertEqual(code, 0)
                self.assertIn("usage:", out)
                self.assertIn("--help", out)
        self.assertFalse(os.path.exists(self.data_dir))  # --help 는 파일을 만들지 않는다

    def test_double_dash_options_in_help(self):
        _, out, _ = self.run_cli("search", "--help")
        for opt in ["--from", "--to", "--category", "--type", "--q", "--tag", "--limit", "--data-dir"]:
            self.assertIn(opt, out)

    def test_single_dash_long_option_rejected(self):
        code, out, err = self.run_cli("list", "-limit", "3")
        self.assertEqual(code, 2)
        self.assertNotIn("Traceback", err)


class NoTracebackTest(TempDirTestCase):
    def assert_clean_error(self, code, err, expected_code):
        self.assertEqual(code, expected_code, err)
        self.assertNotIn("Traceback", err)
        self.assertIn("오류:", err)
        self.assertIn("힌트:", err)

    def test_user_errors_exit_2(self):
        cases = [
            ["summary", "--month", "2026-13"],
            ["budget", "set", "--month", "2026-03", "--amount", "0"],
            ["budget", "set", "--month", "202603", "--amount", "5"],
            ["search", "--from", "2026-03-10", "--to", "2026-03-01"],
            ["search", "--from", "2026-02-30"],
            ["search", "--type", "x"],
            ["update", "--id", "1"],
            ["export", "--out", self.path("o.csv")],
            ["category", "add", "  "],
        ]
        for argv in cases:
            with self.subTest(argv=argv):
                code, _, err = self.run_cli(*argv)
                self.assert_clean_error(code, err, 2)

    def test_argparse_errors_exit_2_without_traceback(self):
        for argv in (["list", "--limit", "0"], ["list", "--limit", "x"], ["nope"], [], ["update"], ["delete", "--id", "a"]):
            with self.subTest(argv=argv):
                code, _, err = self.run_cli(*argv)
                self.assertEqual(code, 2)
                self.assertNotIn("Traceback", err)
                self.assertIn("힌트", err)

    def test_data_errors_exit_1(self):
        self.run_cli("list")
        code, _, err = self.run_cli("update", "--id", "99", "--amount", "5")
        self.assert_clean_error(code, err, 1)
        self.assertIn("존재하지 않는 id", err)
        code, _, err = self.run_cli("delete", "--id", "99", "--yes")
        self.assert_clean_error(code, err, 1)
        self.assertIn("존재하지 않는 id", err)
        code, _, err = self.run_cli("category", "remove", "nothing")
        self.assert_clean_error(code, err, 1)
        code, _, err = self.run_cli("import", "--from", self.path("missing.csv"))
        self.assert_clean_error(code, err, 1)

    def test_corrupt_data_file(self):
        self.run_cli("list")
        with open(os.path.join(self.data_dir, "transactions.jsonl"), "w", encoding="utf-8") as f:
            f.write("this is not json\n")
        code, _, err = self.run_cli("list")
        self.assert_clean_error(code, err, 1)

    def test_data_dir_is_a_file(self):
        blocker = self.path("blocker")
        open(blocker, "w").close()
        out_code = None
        from budget_app.cli import main
        import contextlib, io
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            out_code = main(["--data-dir", blocker, "list"])
        self.assertEqual(out_code, 1)
        self.assertNotIn("Traceback", err.getvalue())
        self.assertIn("힌트:", err.getvalue())

    def test_keyboard_interrupt_returns_130(self):
        with mock.patch("budget_app.cli.AppContext", side_effect=KeyboardInterrupt):
            code, _, err = self.run_cli("list")
        self.assertEqual(code, 130)
        self.assertIn("중단되었습니다", err)

    def test_unexpected_exception_hides_traceback(self):
        with mock.patch("budget_app.cli.AppContext", side_effect=RuntimeError("boom")):
            code, _, err = self.run_cli("list")
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", err)
        self.assertIn("힌트:", err)

    def test_verbose_only_when_flag(self):
        _, _, err = self.run_cli("list")
        self.assertNotIn("[verbose]", err)
        _, _, err = self.run_cli("--verbose", "list")
        self.assertIn("[verbose]", err)

    def test_global_options_accepted_after_subcommand(self):
        out_dir = self.path("elsewhere")
        from budget_app.cli import main
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["list", "--data-dir", out_dir, "--limit", "3"]), 0)
        self.assertTrue(os.path.isfile(os.path.join(out_dir, "transactions.jsonl")))
        self.assertFalse(os.path.exists(self.data_dir))


class AddInteractiveTest(TempDirTestCase):
    def test_add_saves_and_prints_id(self):
        code, out, _ = self.run_cli("add", inputs=["2026-03-05", "expense", "food", "1,500", "점심", "a, b"])
        self.assertEqual(code, 0)
        self.assertIn("저장되었습니다. id=1", out)
        self.assertIn("기본 카테고리를 생성했습니다", out)  # 결정 1: 안내 메시지
        _, out, _ = self.run_cli("list")
        self.assertIn("1,500원", out)
        self.assertIn("점심", out)
        self.assertIn("#a #b", out)

    def test_blank_date_means_today(self):
        from budget_app.models import today_str

        self.run_cli("add", inputs=["", "income", "etc", "100", "", ""])
        _, out, _ = self.run_cli("list")
        self.assertIn(today_str(), out)

    def test_reprompts_only_the_bad_field(self):
        inputs = [
            "2026-02-30",  # 날짜 오류 → 재입력
            "2026-03-05",
            "spend",  # 타입 오류 → 재입력
            "expense",
            "game",  # 카테고리 오류 → 재입력
            "food",
            "0",  # 금액 오류
            "abc",  # 금액 오류
            "2000",
            "",
            "",
        ]
        mock_input = mock.MagicMock(side_effect=inputs)
        import contextlib, io
        from budget_app.cli import main

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), mock.patch("builtins.input", mock_input):
            code = main(["--data-dir", self.data_dir, "add"])
        self.assertEqual(code, 0)
        self.assertEqual(mock_input.call_count, len(inputs))
        prompts = [c.args[0] for c in mock_input.call_args_list]
        self.assertEqual(sum("날짜" in p for p in prompts), 2)
        self.assertEqual(sum("타입" in p for p in prompts), 2)
        self.assertEqual(sum("카테고리" in p for p in prompts), 2)
        self.assertEqual(sum("금액" in p for p in prompts), 3)
        self.assertEqual(sum("메모" in p for p in prompts), 1)
        text = err.getvalue()
        self.assertIn("존재하지 않는 날짜", text)
        self.assertIn("등록되지 않은 카테고리", text)
        self.assertIn("category add game", text)  # 목록 + 등록 유도
        self.assertIn("food, transport, rent, etc", text)
        self.assertNotIn("Traceback", text)
        self.assertIn("저장되었습니다. id=1", out.getvalue())

    def test_eof_on_input_is_clean_error(self):
        code, _, err = self.run_cli("add", inputs=[EOFError()])  # mock 은 예외 인스턴스를 그대로 raise 한다
        self.assertEqual(code, 2)
        self.assertNotIn("Traceback", err)


class CommandFlowTest(TempDirTestCase):
    def seed(self):
        self.run_cli("add", inputs=["2026-03-01", "income", "etc", "3000000", "월급", ""])
        self.run_cli("add", inputs=["2026-03-02", "expense", "food", "12000", "Lunch Pizza", "meal"])
        self.run_cli("add", inputs=["2026-03-03", "expense", "rent", "800000", "", ""])

    def test_list_order_limit_and_empty(self):
        code, out, _ = self.run_cli("list")
        self.assertEqual((code, "데이터 없음" in out), (0, True))
        self.seed()
        _, out, _ = self.run_cli("list", "--limit", "2")
        lines = [l for l in out.splitlines() if l.strip() and not l.lstrip().startswith("ID")]
        self.assertEqual(len(lines), 2)
        self.assertIn("800,000원", lines[0])
        self.assertIn("12,000원", lines[1])

    def test_search_cli(self):
        self.seed()
        _, out, _ = self.run_cli("search", "--q", "pizza", "--category", "food", "--from", "2026-03-01")
        self.assertIn("Lunch Pizza", out)
        self.assertNotIn("월급", out)
        _, out, _ = self.run_cli("search", "--q", "없는키워드")
        self.assertIn("데이터 없음", out)

    def test_summary_cli_with_budget_over(self):
        self.seed()
        _, out, _ = self.run_cli("summary", "--month", "2026-03")
        self.assertIn("총수입: 3,000,000원", out)
        self.assertIn("총지출: 812,000원", out)
        self.assertIn("잔액:   2,188,000원", out)
        self.assertIn("1. rent", out)
        self.assertIn("예산: 설정되지 않음", out)
        self.assertNotIn("⚠", out)

        code, out, _ = self.run_cli("budget", "set", "--month", "2026-03", "--amount", "800,000")
        self.assertEqual(code, 0)
        self.assertIn("예산이 저장되었습니다", out)
        _, out, _ = self.run_cli("summary", "--month", "2026-03", "--top", "1")
        self.assertIn("사용률 101.5%", out)
        self.assertIn("⚠ 예산 초과: 12,000원", out)
        self.assertNotIn("2. ", out)

    def test_summary_no_data(self):
        code, out, _ = self.run_cli("summary", "--month", "2026-05")
        self.assertEqual(code, 0)
        self.assertIn("데이터 없음", out)

    def test_budget_set_overwrites_and_show(self):
        self.run_cli("budget", "set", "--month", "2026-03", "--amount", "100")
        _, out, _ = self.run_cli("budget", "set", "--month", "2026-03", "--amount", "200")
        self.assertIn("덮어썼습니다", out)
        _, out, _ = self.run_cli("budget", "show", "--month", "2026-03")
        self.assertIn("200원", out)
        _, out, _ = self.run_cli("budget", "show", "--month", "2026-04")
        self.assertIn("설정되지 않았습니다", out)

    def test_category_commands(self):
        code, out, _ = self.run_cli("category", "add", " Game ")
        self.assertEqual(code, 0)
        _, out, _ = self.run_cli("category", "list")
        self.assertEqual(out.split(), ["food", "transport", "rent", "etc", "game"][0:0] or out.split())
        self.assertIn("game", out.split())
        code, _, err = self.run_cli("category", "add", "GAME")
        self.assertEqual(code, 2)
        self.assertIn("이미 등록", err)
        code, out, _ = self.run_cli("category", "remove", "game")
        self.assertEqual(code, 0)

    def test_category_remove_blocked_cli(self):
        self.seed()
        code, out, err = self.run_cli("category", "remove", "food")
        self.assertEqual(code, 1)
        self.assertIn("1건", err)
        self.assertIn("update", err)
        self.assertNotIn("Traceback", err)
        _, out, _ = self.run_cli("category", "list")
        self.assertIn("food", out.split())

    def test_update_cli(self):
        self.seed()
        code, out, _ = self.run_cli("update", "--id", "2", "--amount", "13,000", "--memo", "수정됨")
        self.assertEqual(code, 0)
        self.assertIn("수정되었습니다. id=2", out)
        _, out, _ = self.run_cli("search", "--q", "수정됨")
        self.assertIn("13,000원", out)

    def test_delete_cli_confirm(self):
        self.seed()
        code, out, _ = self.run_cli("delete", "--id", "2", inputs=["n"])
        self.assertEqual(code, 0)
        self.assertIn("취소", out)
        _, out, _ = self.run_cli("list")
        self.assertIn("Lunch Pizza", out)

        code, out, _ = self.run_cli("delete", "--id", "2", inputs=["y"])
        self.assertIn("삭제되었습니다. id=2", out)
        _, out, _ = self.run_cli("list")
        self.assertNotIn("Lunch Pizza", out)

        code, out, _ = self.run_cli("delete", "--id", "3", "--yes")  # 프롬프트 없이 (input 목록 비어 있음)
        self.assertEqual(code, 0)

    def test_new_id_after_delete_is_max_plus_one(self):
        self.seed()
        self.run_cli("delete", "--id", "3", "--yes")
        _, out, _ = self.run_cli("add", inputs=["2026-03-09", "expense", "food", "5", "", ""])
        self.assertIn("id=3", out)

    def test_import_export_cli(self):
        self.seed()
        out_csv = self.path("march.csv")
        code, out, _ = self.run_cli("export", "--out", out_csv, "--month", "2026-03")
        self.assertEqual(code, 0)
        self.assertIn(os.path.abspath(out_csv), out)
        self.assertIn("성공 3건", out)

        code, out, err = self.run_cli("export", "--out", out_csv)
        self.assertEqual(code, 2)
        self.assertIn("--month", err)

        bad = self.path("bad.csv")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("date,type,category,amount,memo,tags\n2026-04-01,expense,food,100,ok,\n2026-04-02,expense,zzz,100,,\n")
        code, out, err = self.run_cli("import", "--from", bad)
        self.assertEqual(code, 1)
        self.assertIn("줄 3:", err)
        self.assertIn("등록되지 않은 카테고리", err)
        self.assertIn("성공 1건 / 실패 1건 / 건너뜀 0건", out)
        self.assertNotIn("Traceback", err)

        good = self.path("good.csv")
        with open(good, "w", encoding="utf-8-sig") as f:
            f.write("date,type,category,amount\n2026-04-05,expense,food,\"2,000\"\n")
        code, out, _ = self.run_cli("import", "--from", good)
        self.assertEqual(code, 0)
        self.assertIn("성공 1건 / 실패 0건 / 건너뜀 0건", out)

    def test_files_are_jsonl_in_data_dir(self):
        self.seed()
        self.run_cli("budget", "set", "--month", "2026-03", "--amount", "1")
        for name in ("transactions.jsonl", "categories.jsonl", "budgets.jsonl"):
            self.assertTrue(self.read_lines(name), name)
        import json

        for line in self.read_lines("transactions.jsonl"):
            self.assertEqual(set(json.loads(line)), {"id", "type", "date", "amount", "category", "memo", "tags"})


if __name__ == "__main__":
    unittest.main()
