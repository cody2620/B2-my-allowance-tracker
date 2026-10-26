import contextlib
import io
import os
import shutil
import tempfile
import unittest
from typing import List, Tuple
from unittest import mock

from budget_app.cli import AppContext, main


class TempDirTestCase(unittest.TestCase):
    """테스트마다 임시 폴더를 --data-dir 로 쓴다 (실제 ./data 를 건드리지 않는다)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="budget_test_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.data_dir = os.path.join(self.tmp, "data")

    def path(self, *parts: str) -> str:
        return os.path.join(self.tmp, *parts)

    def ctx(self) -> AppContext:
        with contextlib.redirect_stdout(io.StringIO()):
            return AppContext(self.data_dir)

    def add_tx(self, ctx: AppContext, date="2026-03-01", type="expense", category="food", amount=1000, memo="", tags=None):
        return ctx.transactions.add(date=date, type=type, category=category, amount=amount, memo=memo, tags=tags)

    def run_cli(self, *argv: str, inputs: List[str] = ()) -> Tuple[int, str, str]:
        """main()을 실행해 (종료 코드, stdout, stderr)를 돌려준다."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with mock.patch("builtins.input", side_effect=list(inputs)):
                code = main(["--data-dir", self.data_dir] + list(argv))
        return code, out.getvalue(), err.getvalue()

    def read_lines(self, name: str) -> List[str]:
        with open(os.path.join(self.data_dir, name), encoding="utf-8") as f:
            return [line for line in f.read().split("\n") if line]
