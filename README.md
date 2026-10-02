# 나만의 용돈 기입장 프로그램 만들기

## 실행 방법

## 저장 파일 위치와 형식
- 파일구조
```
  budget_app/                ← 프로젝트 루트
├── README.md                ← 실행 방법, 저장 파일 위치/형식, 명령 예시, CSV 스키마
├── data/                    ← 기본 저장 폴더 (--data-dir 로 변경 가능, 자동 생성)
│   ├── transactions.jsonl
│   ├── categories.jsonl
│   └── budgets.jsonl
└── budget_app/              ← 패키지 (python -m budget_app 으로 실행)
    ├── __init__.py
    ├── __main__.py          ← 진입점: main() 호출 후 sys.exit(종료코드)
    ├── cli.py               ← argparse 서브커맨드 정의, 대화형 input(), 출력 포맷
    ├── models.py            ← dataclass: Transaction, Budget, Category
    ├── repository.py        ← 파일 I/O: TransactionRepository, CategoryStore, BudgetStore
    ├── services.py          ← 비즈니스 로직: TransactionService, BudgetService, CategoryService
    ├── csv_io.py            ← import/export CSV 처리
    ├── validators.py        ← 날짜/금액/type/category 검증 함수
    ├── decorators.py        ← 예외 처리·시간 측정·로그 데코레이터
    └── errors.py            ← 사용자 정의 예외 (원인 + 힌트 메시지)
```

## 주요 명령

## import/export CSV 스키마

--- 

## 트러블 슈팅

## 용어 및 개념

#

<details> 
  <summary> 최종 결과물 </summary>
  <img width="666" height="641" alt="1" src="https://github.com/user-attachments/assets/11e4a251-5def-4747-a3ae-a3cfd63fe8fb" />
  <img width="666" height="597" alt="2" src="https://github.com/user-attachments/assets/aea6acd6-6fd2-4090-a5e0-33274df41469" />




</details>


#

<details> 
  <summary> 기능 요구 사항 </summary>

  ### 

다음 요구사항을 모두 만족해야 한다.

#### 1. 실행 및 입력 방식

- 실행 예시는 아래 중 하나를 권장합니다.
  - `python -m budget_app <command> [options]`
- 모든 명령은 `--help` 옵션으로 사용 방법이 출력되어야 합니다.
- 입력 기본 방식은 "대화형" 입니다.
  - 예: `add` 실행 시 날짜/타입/카테고리/금액 등을 `input()`으로 순차 입력
- 단, 아래 항목은 옵션 인자 방식도 허용(권장) 합니다.
  - `search`, `list`, `summary`, `export`, `import`, `delete`
  - `update`는 "옵션 방식 또는 대화형" 중 하나를 선택해도 되나, 문서에 명확히 고정해야 합니다(아래 6번 참고).
- 옵션 표기는 리눅스 표준인 `--`로 통일해야 합니다.
  - 예: `--help`, `--limit`, `--from`, `--to`, `--month`

#### 2. 데이터 모델

- 거래 내역(Transaction)은 최소 아래 필드를 포함해야 합니다.
  - `id`(유일), `type`(income/expense), `date`(YYYY-MM-DD), `amount`(양수), `category`, `memo`(선택), `tags`(선택)
- 데이터 모델은 dataclass 또는 그에 준하는 구조로 정의해야 합니다.
- 최소 2개 이상의 클래스를 사용해야 합니다.
  - 예시: Transaction, TransactionRepository, BudgetStore, CategoryStore, BudgetService 등
- 입력 검증
  - 날짜 형식 오류, 음수/0 금액, 허용되지 않은 type, 존재하지 않는 category 등은 재입력 요구 또는 오류 메시지 출력으로 처리합니다.

#### 3. 저장 정책

- 저장 포맷은 JSONL 또는 CSV 중 1개를 선택합니다.
- 저장 파일은 **3개 이상(필수)** 로 분리해 영구 저장합니다.
  - `transactions.<fmt>`, `categories.<fmt>`, `budgets.<fmt>`
- 기본 저장 폴더는 `./data`를 권장하며, 옵션으로 변경 가능해야 합니다(예: `-data-dir`).
- 초기 실행(저장 파일이 없을 때)
  - 파일이 없으면 자동 생성하거나, "초기화 안내 메시지"를 출력해야 합니다.
  - 카테고리 파일이 비어있으면 아래 중 하나를 택해 동작을 명확히 하세요.
    - (안 A) 기본 카테고리 자동 생성(예: food, transport, rent, etc)
    - (안 B) `category add`를 먼저 하도록 안내하고 `add`를 막음      

#### 4. add(거래 추가)

- `add` 실행 후 대화형으로 필드를 입력받아 거래를 저장해야 합니다.
- category는 등록된 목록에 존재해야 합니다(없으면 안내 후 재입력/등록 유도).
- 저장 완료 시 생성된 id를 사용자에게 출력해야 합니다.

#### 5. list(목록 조회)

- `list`는 최신순으로 거래를 출력해야 합니다.
- `--limit N` 옵션을 지원해야 합니다(기본값 제공).
- 파일 전체를 한 번에 로드하지 않고, 제너레이터 기반 스트리밍 처리로 구현해야 합니다.

#### 6. update/delete(수정/삭제)

- `delete --id <id>`로 특정 거래를 삭제할 수 있어야 합니다.
- 존재하지 않는 id는 "없는 데이터"로 처리하고 사용자 메시지를 출력해야 합니다.
- `update`는 아래 중 하나의 방식으로 문서에 명확히 고정해 구현해야 합니다.
  - (안 A) 옵션 기반: `update --id <id> [--date ...] [--type ...] [--category ...] [--amount ...] [--memo ...] [--tags ...]`
  - (안 B) 대화형 기반: 수정할 필드만 선택/재입력 받는 흐름
- 파일 기반 저장에서 update/delete는 "전체 재작성/임시 파일/원자적 교체(권장)" 등 안정성을 고려해야 합니다.

#### 7. search(검색)

- 조건 기반 검색을 지원해야 합니다.
  - 기간: `--from`, `--to`
  - 카테고리: `--category`
  - 타입: `--type`
  - 메모 키워드: `--q`
  - 태그: `--tag`
- 검색 결과는 최신순으로 출력해야 합니다.
- 스트리밍 처리(제너레이터 기반)를 유지해야 합니다.

#### 8. summary(월별 요약)

- `summary --month YYYY-MM` 입력을 받아 해당 월의 요약을 출력해야 합니다.
- 출력 항목:
  - 총 수입, 총 지출, 잔액(총수입-총지출)
  - 카테고리별 지출 합계 TOP N(`-top` 옵션 지원)
- 데이터가 없는 달은 "데이터 없음"을 명확히 출력해야 합니다.

#### 9. budget(예산)

- `budget set --month YYYY-MM --amount <금액>`으로 월 예산을 저장해야 합니다.
- `summary` 실행 시 예산이 설정되어 있으면
  - 예산 대비 사용률(%)
  - 초과 여부(초과 시 경고 문구) 를 함께 출력해야 합니다.
- 예산 데이터도 반드시 영구 저장되어야 합니다.

#### 10. category(카테고리 관리)

- `category add/list/remove`를 제공해야 합니다.
- 카테고리 삭제 시, 해당 카테고리를 사용하는 내역이 존재하면
- 삭제를 막거나
- 대체 카테고리를 요구해야 합니다

#### 11. import/export(가져오기/내보내기)

- `import --from \`로 거래를 일괄 등록한다.
- `export --out \`로 조건에 맞는 거래를 CSV로 저장한다.
- export는 `--month YYYY-MM` 또는 `--from YYYY-MM-DD --to YYYY-MM-DD` 중 하나 이상 조건을 필수로 받는다.
- import/export는 아래 CSV 최소 스키마를 고정한다.
  
| column | required | 설명 |
| --- | --- | --- |
| date | Y | YYYY-MM-DD |
| type | Y | income / expense |
| category | Y | 등록된 카테고리 |
| amount | Y | 양수 정수 |
| memo | N | 문자열 |
| tags | N | 쉼표(,) 구분 문자열 |
| 공통: UTF-8, 헤더 포함 |   |   |

(1) 데코레이터(Decorator)
- 공통 관심사(예: 예외 처리/로그/시간 측정) 데코레이터를 1개 이상 구현하고 실제 적용한다.

(2) 예외 처리 및 종료 코드
- 오류는 스택트레이스 대신 원인 + 해결 힌트로 출력한다.
- 정상 종료는 0, 오류 종료는 0이 아닌 값으로 종료한다.

(3) 모듈화(구조화)
- 한 파일에 몰아넣지 않고 최소 3개 이상 모듈로 분리한다.
- (권장) CLI / 서비스 / 저장소(파일 I/O) / 모델(데이터 구조)로 책임을 나눈다.


</details>

<details>
  <summary>제약사항</summary>
  <img width="658" height="583" alt="제약사항" src="https://github.com/user-attachments/assets/d20d82b0-b0f5-4401-a37e-a4ad768a9180" />

</details> 

