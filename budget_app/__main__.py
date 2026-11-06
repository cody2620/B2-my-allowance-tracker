"""`python -m budget_app ...` 으로 실행할 때 가장 먼저 실행되는 파일.

실제 일은 cli.main() 이 하고, 여기서는 그 반환값(종료 코드: 0 성공, 1·2 오류)을
sys.exit() 로 운영체제에 돌려주기만 한다.
"""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
