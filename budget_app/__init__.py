"""표준 라이브러리만 사용하는 CLI 가계부.

패키지 구성 (위에서 아래로 호출한다. 아래 계층은 위 계층을 모른다):

    __main__.py    `python -m budget_app` 실행 진입점. cli.main() 을 부르기만 한다.
    cli.py         [입출력] 명령줄 옵션 해석, 대화형 입력, 화면 출력.
    decorators.py  [공통] 모든 명령 함수에 씌우는 오류 처리·실행 시간 측정.
    services.py    [업무 규칙] 거래·카테고리·예산·요약·CSV 가져오기/내보내기 로직.
    repository.py  [저장] data/*.jsonl 파일 읽기·쓰기.
    models.py      [데이터] Transaction/Budget 정의와 값 검증·변환(parse_*).
    errors.py      [공통] 사용자에게 보여줄 메시지와 힌트를 담는 예외들.

명령 하나의 흐름 예: cli.cmd_add → services.TransactionService.add
→ models.parse_* (검증) → repository.TransactionRepository.add → data/transactions.jsonl
"""

__version__ = "1.0.0"
