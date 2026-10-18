# 👁️ WhoWillSee

**누가 보느냐에 따라 자료는 다르게 읽힙니다.**

발표 자료, 브리핑, PPT, PDF, HTML, 이미지를 올리고 청중(페르소나)을 고르면,
그 사람이 **막히거나, 오해하거나, 불편해하거나, 신뢰를 잃는 지점**에 빨간 박스를 그리고
"왜 문제인지 / 어떻게 고칠지 / 그 사람의 속마음"을 메모로 달아주는 시뮬레이터입니다.

- 기본 청중은 **일반 시민**입니다. 직군·소속·직위·성격·니즈·취향·전문성·디지털 숙련도·우려·자료를 보는 상황 등을 직접 설정해 페르소나를 만들 수 있습니다.
- 여러 페르소나를 한 번에 돌려 **청중별 점수와 지적사항을 탭으로 비교**할 수 있습니다.
- 결과는 화면에서 보거나 **주석이 달린 PDF**(빨간 박스, 번호, PDF 메모 주석, 페이지별 메모)와 JSON으로 내보낼 수 있습니다.

## 지원 형식

| 형식 | 처리 방식 |
|---|---|
| PDF | 페이지별 이미지 렌더링 + 텍스트 블록·글자 크기 추출 |
| PPT / PPTX / ODP / KEY / DOC / DOCX | LibreOffice로 PDF 변환 후 위와 동일 (LibreOffice 필요) |
| HTML 파일 / 웹페이지 URL | Chromium 스크린샷(긴 페이지는 분할) + DOM 텍스트 블록 |
| PNG / JPG / WEBP / GIF / BMP | 이미지 1장 그대로 사용 (위치는 모델이 직접 지정) |

## 빠른 시작

```bash
pip install -r requirements.txt
python -m playwright install chromium        # HTML 분석을 쓸 때만 (이미 있으면 생략)
# PPT 분석을 쓰려면 LibreOffice 설치: apt install libreoffice / brew install --cask libreoffice

export ANTHROPIC_API_KEY=sk-ant-...           # 없으면 '데모 모드'로 동작
uvicorn app.main:app --port 8000
```

브라우저에서 http://localhost:8000 을 엽니다.

1. **누가 보나요?** 페르소나를 하나 이상 선택합니다 (기본: 일반 시민). `+ 새 페르소나 만들기` 또는 프리셋의 `⧉`(복제)로 나만의 청중을 만들 수 있습니다.
2. **자료 올리기:** 파일을 끌어다 놓거나 URL을 입력합니다.
3. **어떤 상황인가요?** (선택) "구청 주민설명회 10분 발표"처럼 사용 맥락을 적으면 분석에 반영됩니다.
4. **분석 시작:** 박스에 마우스를 올리면 메모가, 메모에 올리면 박스가 강조됩니다. 심각도(치명/주의/제안) 필터와 페르소나 탭을 쓸 수 있습니다.

샘플 자료(일부러 문제를 넣은 브리핑 PDF/PPTX/HTML)는 `python samples/make_samples.py` 로 만들 수 있습니다.

## 분석 모드

| 모드 | 조건 | 내용 |
|---|---|---|
| **Claude 분석** | `ANTHROPIC_API_KEY`(또는 `ANTHROPIC_AUTH_TOKEN`) 설정 | Claude가 페르소나가 되어 페이지 이미지와 텍스트를 함께 읽고, 이해도·용어·정보 밀도·논리·근거·디자인·니즈 적합성·톤·접근성을 평가합니다. |
| **데모 모드** | 키 없음, 또는 `WHOWILLSEE_DEMO=1` | 텍스트 규칙(전문용어/약어, 작은 글씨, 정보 과밀, 미정·별첨 같은 누락 정보, 숫자 과다)으로 추정합니다. 그림·차트는 분석하지 않습니다. |

### 박스 위치를 정확하게 잡는 방법

PDF·PPT·HTML에서는 텍스트 블록과 좌표를 미리 뽑아 `[B12] (x, y, w, h) 텍스트…` 형태로 모델에 함께 줍니다.
모델은 지적마다 해당 블록 번호를 고르고, 서버가 블록 좌표의 합집합으로 박스를 계산합니다.
그림·차트처럼 블록이 없는 영역만 모델이 직접 준 정규화 좌표(bbox)를 씁니다.

## 설정 (환경 변수)

| 변수 | 기본값 | 설명 |
|---|---|---|
| `ANTHROPIC_API_KEY` | (없음) | Claude API 키 |
| `WHOWILLSEE_MODEL` | `claude-opus-5-5` | 분석 모델 |
| `WHOWILLSEE_EFFORT` | `medium` | 분석 깊이 (`low`·`medium`·`high`·`xhigh`·`max`). 높을수록 꼼꼼하지만 느리고 비쌉니다 |
| `WHOWILLSEE_PAGES_PER_CALL` | `6` | 한 번의 API 호출에 넣는 페이지 수 (긴 문서는 나눠 분석 후 종합) |
| `WHOWILLSEE_MAX_PAGES` | `40` | 변환할 최대 페이지 수 |
| `WHOWILLSEE_DATA` | `./data` | 업로드·결과·사용자 페르소나 저장 폴더 |
| `WHOWILLSEE_DEMO` | | `1` 이면 키가 있어도 데모 모드 |
| `CHROMIUM_PATH` | 자동 탐색 | HTML 렌더링용 Chromium 실행 파일 경로 |

## 구조

```
app/
  main.py        FastAPI 서버 (REST API + 정적 파일)
  converter.py   파일 → 페이지 PNG + 텍스트 블록
  personas.py    페르소나 스키마 · 프리셋 · 저장
  analyzer.py    Claude 분석(구조화 JSON 출력) · 데모 모드 · 박스 좌표 계산
  exporter.py    주석 PDF 내보내기
  storage.py     data/ 폴더 저장소
  static/        웹 UI (바닐라 JS)
samples/         샘플 자료 생성 스크립트
tests/           pytest
docs/PLAN.md     설계 문서와 단계별 체크리스트
```

### API 요약

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/api/status` | 분석 모드, 모델, 지원 형식 |
| GET/POST | `/api/personas` | 페르소나 목록 / 생성 |
| PUT/DELETE | `/api/personas/{id}` | 수정 / 삭제 (기본 프리셋은 읽기 전용) |
| POST | `/api/documents` | 업로드 (`file` 또는 `url` 폼 필드) → 변환 |
| GET/DELETE | `/api/documents/{id}` | 문서 메타 조회 / 삭제 |
| GET | `/api/documents/{id}/pages/{n}.png` | 페이지 이미지 |
| POST | `/api/documents/{id}/analyze` | `{"persona_ids": [...], "context": "..."}` → 작업 ID |
| GET | `/api/jobs/{job_id}` | 진행 상태 |
| GET | `/api/documents/{id}/results[/{persona_id}]` | 결과 목록 / 상세 |
| GET | `/api/documents/{id}/results/{persona_id}/export.pdf` · `.json` | 내보내기 |

## 테스트

```bash
python -m pytest -q
```

변환기(PDF·이미지·PPTX·HTML), 페르소나, 분석 엔진(데모 모드·좌표 계산·Claude 호출 경로 모킹), API 전체 흐름(업로드→분석→PDF 내보내기)을 검사합니다.

## 알아둘 점

- 결과는 실제 사람의 반응이 아니라 "이런 사람이라면 이렇게 느낄 것"이라는 **시뮬레이션**입니다. 페르소나를 구체적으로 적을수록 지적이 날카로워집니다.
- Claude 분석 모드에서는 페이지 이미지와 텍스트가 Anthropic API로 전송됩니다. 민감한 자료는 조직 정책을 확인하세요.
- URL 가져오기는 서버가 해당 주소에 접속합니다. 외부에 공개 배포할 때는 내부망 주소 차단 등 접근 제한을 추가하세요.
- 분석 작업 상태는 메모리에 있어 서버를 재시작하면 진행 중인 작업이 사라집니다 (완료된 결과는 `data/`에 저장됩니다).
