# 👁️ WhoWillSee

**누가 보느냐에 따라 자료는 다르게 읽힙니다.**

발표 자료, 브리핑, PPT, PDF, HTML, 이미지를 올리고 청중(페르소나)을 고르면,
그 사람이 **막히거나, 오해하거나, 불편해하거나, 신뢰를 잃는 지점**에 빨간 박스를 그리고
"왜 문제인지 / 어떻게 고칠지 / 그 사람의 속마음"을 메모로 달아주는 시뮬레이터입니다.

- **페르소나:** 기본 청중은 **일반 시민**입니다. 직군·소속·직위·성격·니즈·취향·전문성·디지털 숙련도·우려·자료를 보는 상황 등을 직접 설정하거나, **한 줄 설명으로 자동 생성**할 수 있습니다.
- **여러 청중 동시 분석:** 청중별 점수와 지적사항을 탭으로 비교합니다.
- **수정본 비교:** 고친 자료를 올리면 이전 버전 대비 점수 변화와 **해결 / 남음 / 새로 생김** 지적을 보여줍니다.
- **대시보드:** 전체 분석 현황, 청중별 평균 전달력, 자주 나온 문제 유형, 자료×청중 점수표, 수정본 개선 추이.
- **내보내기:** 주석 PDF(빨간 박스, 번호, PDF 메모 주석, 페이지별 메모)와 JSON.
- **내 AI 연결:** 특정 AI 계정에 묶여 있지 않습니다. 사용하는 사람이 **Claude, OpenAI, Gemini, OpenRouter, Ollama·LM Studio(내 컴퓨터)** 등 자기 AI를 화면에서 연결합니다. AI 없이도 규칙 기반 데모 모드로 동작합니다.

## 빠른 시작

Python 3.10 이상이 필요합니다.

```bash
git clone <이 저장소 주소>
cd whowillsee
pip install -r requirements.txt
python -m playwright install chromium      # HTML·웹페이지 분석용 (한 번만)
uvicorn app.main:app --port 8000
```

브라우저에서 http://localhost:8000 을 열고, 상단 **AI 연결** 메뉴에서 내 AI를 연결하세요 (연결하지 않으면 데모 모드).

PPT·DOCX 분석에는 LibreOffice가 필요합니다: `sudo apt install libreoffice-impress` / `brew install --cask libreoffice`
(없어도 PDF·HTML·이미지는 분석할 수 있고, PPT는 PDF로 저장해 올리면 됩니다.)

### Docker로 실행

LibreOffice·Chromium·한글 글꼴이 모두 들어 있는 이미지입니다.

```bash
docker build -t whowillsee .
docker run -p 8000:8000 -v whowillsee-data:/data whowillsee
```

## 내 AI 연결하기

상단 **AI 연결** 메뉴에서 서비스를 고르고 키·모델을 입력한 뒤 **연결 테스트** → **저장**을 누르면 됩니다.
연결 테스트는 작은 이미지 1장을 보내 이미지 입력까지 되는지 확인합니다.

| 선택 | 필요한 것 | 참고 |
|---|---|---|
| **Claude (Anthropic)** | API 키 ([발급](https://console.anthropic.com/settings/keys)) | 모델을 비워두면 `claude-opus-5-5`. 분석 깊이(effort)를 고를 수 있습니다 |
| **OpenAI 호환 → OpenAI** | API 키, 이미지 입력을 지원하는 모델 이름 | |
| **OpenAI 호환 → Google Gemini** | API 키, 모델 이름 | Gemini의 OpenAI 호환 주소를 자동으로 씁니다 |
| **OpenAI 호환 → OpenRouter** | API 키, 모델 이름 | 여러 회사 모델을 한 키로 |
| **OpenAI 호환 → Ollama / LM Studio** | 내 컴퓨터에서 실행 중인 서버, 비전 모델 | 키 불필요. 자료가 외부로 나가지 않습니다 |
| **OpenAI 호환 → 직접 입력** | Base URL, 모델 이름, (필요하면) 키 | vLLM, 사내 게이트웨이 등 |
| **AI 없이 (데모 모드)** | 없음 | 텍스트 규칙만으로 점검 (그림·차트 분석 불가) |

- 키는 **서비스별로 따로** 이 서버의 `data/settings.json`에 저장됩니다. `data/`는 `.gitignore`에 들어 있어 깃에 올라가지 않고, 화면과 API 응답에는 `sk-ant…abcd`처럼 일부만 표시됩니다.
- 화면 설정 대신 환경 변수를 쓸 수도 있습니다. `.env.example`을 참고하세요. 화면에서 저장한 설정이 있으면 그것이 우선합니다.
- 작은 로컬 모델은 지시를 덜 따르거나 위치를 부정확하게 잡을 수 있습니다. 서버가 JSON 스키마 출력을 지원하지 않으면 더 단순한 요청 방식으로 자동 재시도합니다.

## 사용 방법

1. **누가 보나요?** 청중을 하나 이상 고릅니다. `+ 새 페르소나 만들기`에서 "예산 결재권을 가진 50대 구청 국장, 숫자에 깐깐함"처럼 한 줄로 설명하고 **자동 채우기**를 누르면 나머지 항목이 채워집니다 (저장 전에 고칠 수 있음). 기본 프리셋은 `⧉`로 복제해 고칠 수 있습니다.
2. **자료 올리기:** 파일을 끌어다 놓거나 웹페이지 URL을 입력합니다.
3. **어떤 상황인가요?** (선택) "구청 주민설명회 10분 발표"처럼 사용 맥락을 적으면 분석에 반영됩니다.
4. **분석 시작:** 박스에 마우스를 올리면 메모가, 메모에 올리면 박스가 강조됩니다. 심각도(치명/주의/제안) 필터와 청중 탭을 쓸 수 있습니다.
5. **고치고 다시 확인:** 결과 화면의 **⬆ 수정본 올리기**로 고친 파일을 올리고 분석하면 이전 버전과 비교 패널이 나타납니다. 이전 버전과 같은 청중이 자동으로 선택됩니다.
6. **대시보드:** 전체 현황과 개선 추이를 봅니다. 점수표 칸을 누르면 해당 결과로 이동합니다.

샘플 자료(일부러 문제를 넣은 브리핑 PDF/PPTX/HTML과 그 수정본 PDF)는 `python samples/make_samples.py`로 만들 수 있습니다.

## 지원 형식

| 형식 | 처리 방식 |
|---|---|
| PDF | 페이지별 이미지 렌더링 + 텍스트 블록·글자 크기 추출 |
| PPT / PPTX / ODP / KEY / DOC / DOCX | LibreOffice로 PDF 변환 후 위와 동일 |
| HTML 파일 / 웹페이지 URL | Chromium 스크린샷(긴 페이지는 분할) + DOM 텍스트 블록 |
| PNG / JPG / WEBP / GIF / BMP | 이미지 1장 그대로 사용 (위치는 AI가 직접 지정) |

### 박스 위치를 정확하게 잡는 방법

PDF·PPT·HTML에서는 텍스트 블록과 좌표를 미리 뽑아 `[B12] (x, y, w, h) 텍스트…` 형태로 AI에 함께 줍니다.
AI는 지적마다 해당 블록 번호를 고르고, 서버가 블록 좌표의 합집합으로 박스를 계산합니다.
그림·차트처럼 블록이 없는 영역만 AI가 직접 준 정규화 좌표(bbox)를 씁니다.

## 환경 변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `ANTHROPIC_API_KEY` | | Claude 키 (화면 설정이 없을 때) |
| `WHOWILLSEE_PROVIDER` | 자동 | `anthropic` · `openai_compatible` |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` | | OpenAI 호환 API 키·주소 |
| `WHOWILLSEE_MODEL` | | 모델 이름 (Claude는 비우면 `claude-opus-5-5`) |
| `WHOWILLSEE_EFFORT` | `medium` | Claude 분석 깊이 |
| `WHOWILLSEE_PAGES_PER_CALL` | `6` | 한 번의 AI 호출에 넣는 페이지 수 (긴 문서는 나눠 분석 후 종합) |
| `WHOWILLSEE_MAX_PAGES` | `40` | 변환할 최대 페이지 수 |
| `WHOWILLSEE_DATA` | `./data` | 업로드·결과·페르소나·설정 저장 폴더 |
| `WHOWILLSEE_DEMO` | | `1`이면 키가 있어도 데모 모드 |
| `WHOWILLSEE_LOCK_SETTINGS` | | `1`이면 화면에서 AI 설정을 바꿀 수 없음 (공용 서버용) |
| `CHROMIUM_PATH` | 자동 | HTML 렌더링용 Chromium 경로 |

## 구조

```
app/
  main.py         FastAPI 서버 (REST API + 정적 파일)
  converter.py    파일 → 페이지 PNG + 텍스트 블록
  personas.py     페르소나 스키마 · 프리셋 · 저장
  persona_gen.py  한 줄 설명 → 페르소나 초안
  providers.py    AI 연결 (Claude / OpenAI 호환 / 데모), 설정 저장, 연결 테스트
  analyzer.py     분석 프롬프트 · 결과 정리 · 데모 모드 · 박스 좌표 계산
  compare.py      수정본 비교 (해결 / 남음 / 새로 생김)
  stats.py        대시보드 집계
  exporter.py     주석 PDF 내보내기
  storage.py      data/ 폴더 저장소
  static/         웹 UI (바닐라 JS, 빌드 과정 없음)
samples/          샘플 자료 생성 스크립트
tests/            pytest
docs/PLAN.md      설계 문서와 단계별 체크리스트
```

### API 요약

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/api/status` | 분석 모드, 연결된 AI, 지원 형식 |
| GET / PUT | `/api/settings` | AI 연결 설정 조회 (키는 마스킹) / 저장 |
| POST | `/api/settings/test` | 연결 테스트 (저장 전 값으로) |
| GET / POST | `/api/personas` | 페르소나 목록 / 생성 |
| POST | `/api/personas/generate` | `{"description": "..."}` → 페르소나 초안 (저장 안 함) |
| PUT / DELETE | `/api/personas/{id}` | 수정 / 삭제 (기본 프리셋은 읽기 전용) |
| POST | `/api/documents` | 업로드 (`file` 또는 `url`, 수정본이면 `parent_id`) → 변환 |
| GET / DELETE | `/api/documents/{id}` | 문서 메타 조회 / 삭제 |
| GET | `/api/documents/{id}/pages/{n}.png` | 페이지 이미지 |
| POST | `/api/documents/{id}/analyze` | `{"persona_ids": [...], "context": "..."}` → 작업 ID |
| GET | `/api/jobs/{job_id}` | 진행 상태 |
| GET | `/api/documents/{id}/results[/{persona_id}]` | 결과 목록 / 상세 |
| GET | `/api/documents/{id}/compare/{persona_id}` | 이전 버전과 비교 |
| GET | `/api/documents/{id}/results/{persona_id}/export.pdf` · `.json` | 내보내기 |
| GET | `/api/stats` | 대시보드 집계 |

## 테스트

```bash
python -m pytest -q
```

변환기(PDF·이미지·PPTX·HTML), 페르소나·자동 생성, AI 연결(Claude 호출 모킹, OpenAI 호환 서버 모킹, 키 마스킹·서비스별 격리·설정 잠금), 분석 엔진, 수정본 비교, 대시보드 집계, API 전체 흐름을 검사합니다. GitHub Actions(`.github/workflows/test.yml`)에서도 같은 테스트가 돕니다.

## 알아둘 점

- 결과는 실제 사람의 반응이 아니라 "이런 사람이라면 이렇게 느낄 것"이라는 **시뮬레이션**입니다. 페르소나를 구체적으로 적을수록 지적이 날카로워집니다.
- AI를 연결하면 페이지 이미지와 텍스트가 **연결한 AI 서비스로 전송**됩니다. 민감한 자료는 조직 정책을 확인하거나 Ollama·LM Studio 같은 로컬 모델을 쓰세요.
- 이 앱에는 로그인 기능이 없습니다. 기본 실행(`uvicorn ... --port 8000`)은 내 컴퓨터에서만 접속됩니다. 다른 사람에게 공개하는 서버(`--host 0.0.0.0`, Docker)라면 저장된 키로 누구나 분석을 돌릴 수 있으므로, 앞단에 인증을 두고 `WHOWILLSEE_LOCK_SETTINGS=1`로 설정을 잠그세요. URL 가져오기는 서버가 그 주소에 접속하므로 내부망 주소 차단도 고려하세요.
- 분석 작업 상태는 메모리에 있어 서버를 재시작하면 진행 중인 작업이 사라집니다 (완료된 결과는 `data/`에 저장됩니다).
- 수정본 비교에서 "같은 지적인지"는 유형·제목·해당 문구의 유사도로 추정합니다.
