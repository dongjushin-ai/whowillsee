# WhoWillSee — 설계 & 진행 체크리스트

> 발표·브리핑 자료를 "누가 보느냐(페르소나)" 관점에서 시뮬레이션해
> 문제 지점을 빨간 박스로 표시하고, 왜 문제인지 메모로 알려주는 도구.

## 1. 아키텍처

```
브라우저 (static/index.html + app.js)
  ├─ 페르소나 빌더 (프리셋 + 커스텀)
  ├─ 파일 업로드 (PDF / PPT·PPTX / HTML / PNG·JPG·WEBP)
  └─ 결과 뷰어 (페이지 이미지 + SVG 빨간 박스 + 메모 패널, 페르소나별 탭)
        │  REST (JSON)
FastAPI 서버 (app/main.py)
  ├─ converter.py  : 파일 → 페이지 PNG + 텍스트 블록(정규화 좌표 0~1)
  │     PDF  → PyMuPDF 렌더링 + 블록 추출
  │     PPT  → LibreOffice(headless) → PDF → 위와 동일
  │     HTML → Playwright 스크린샷(세로 분할) + DOM 텍스트 블록
  │     이미지 → 그대로 1페이지 (블록 없음, 모델 좌표 사용)
  ├─ personas.py   : 페르소나 스키마 · 프리셋(일반 시민 기본) · JSON 저장
  ├─ analyzer.py   : Claude Vision 분석 (구조화 JSON 출력)
  │                  API 키 없으면 휴리스틱 "데모 모드"
  ├─ exporter.py   : 주석이 달린 PDF 내보내기 (빨간 박스 + 메모)
  └─ storage.py    : data/ 폴더에 문서·결과 저장
```

### 좌표 정확도 전략
1. 텍스트 블록을 미리 추출해 `[B12] "텍스트…" (x,y,w,h)` 형태로 모델에 함께 전달
2. 모델은 지적사항마다 `block_ids` 를 우선 지정 → 서버가 블록 좌표의 합집합으로 박스 계산 (정확)
3. 블록이 없는 영역(이미지, 차트)은 모델이 준 `bbox`(0~1 정규화) 사용

### 분석 결과 스키마 (요약)
```json
{
  "overall": { "score": 0-100, "first_impression": "", "summary": "", "top_fixes": [] },
  "pages": [
    { "page": 1, "issues": [
      { "block_ids": [3], "bbox": {"x":0,"y":0,"w":0,"h":0},
        "severity": "critical|warning|suggestion",
        "category": "용어|정보과밀|가독성|논리|신뢰성|시각디자인|니즈불일치|톤|기타",
        "title": "", "reason": "", "suggestion": "", "persona_reaction": "" } ] } ]
}
```

## 2. 단계별 체크리스트

### Step 1. 설계 & 프로젝트 골격
- [x] 기술 스택 결정 (FastAPI + PyMuPDF + Playwright + LibreOffice + Anthropic SDK, 바닐라 JS)
- [x] 설계 문서 / 체크리스트 작성
- [x] 디렉터리 구조, requirements.txt, .gitignore

### Step 2. 파일 변환기
- [x] PDF → 페이지 PNG + 텍스트 블록(+글자 크기)
- [x] 이미지 → 단일 페이지 (큰 이미지 축소)
- [x] PPT/PPTX/ODP/DOCX → LibreOffice → PDF
- [x] HTML → Playwright 전체 스크린샷, 긴 페이지 분할, DOM 텍스트 블록
- [x] 변환기 테스트

### Step 3. 페르소나
- [x] 페르소나 스키마 (직군·소속·성격·니즈·취향·전문성·연령·디지털 숙련도·관심사·우려·상황 등)
- [x] 기본 프리셋: 일반 시민(기본값) + 예시 프리셋
- [x] 생성/수정/삭제/복제 API, JSON 파일 저장
- [x] 페르소나 → 프롬프트 변환

### Step 4. 분석 엔진
- [x] Claude 호출 (이미지 + 텍스트 블록 + 페르소나, 구조화 출력)
- [x] 긴 문서는 페이지 묶음 단위로 나눠 호출 후 병합
- [x] block_ids → 정확한 박스 좌표 계산
- [x] 거절(refusal)·오류 처리, 서버측 fallback
- [x] API 키 없을 때 휴리스틱 데모 모드
- [x] 분석 엔진 테스트 (데모 모드 + 좌표 계산)

### Step 5. API 서버
- [x] 업로드 → 변환 → 문서 ID
- [x] 분석 잡 (백그라운드, 여러 페르소나 동시) + 진행 상태 폴링
- [x] 결과 조회, 페이지 이미지 서빙
- [x] API 테스트

### Step 6. 프론트엔드
- [x] 페르소나 목록 / 빌더 폼 / 다중 선택
- [x] 드래그&드롭 업로드
- [x] 페이지 뷰어 + 빨간 박스 오버레이 + 번호 배지
- [x] 메모 패널 (박스와 상호 하이라이트), 심각도 필터
- [x] 페르소나별 결과 탭 + 종합 점수/첫인상
- [x] 브라우저 실동작 확인 (Playwright 스크린샷)

### Step 7. 내보내기
- [x] 주석 PDF (빨간 박스 + 번호 + 메모 페이지)
- [x] JSON 내보내기

### Step 8. 마무리
- [x] 샘플 자료로 엔드투엔드 테스트
- [x] README (설치·실행·API 키 설정·사용법)
- [x] 커밋 & 푸시

## 3. 2차 개발 체크리스트

### Step 9. AI 직접 연결 (BYO AI)
- [x] 제공자 추상화: Anthropic(Claude) / OpenAI 호환(OpenAI·Gemini·Ollama·LM Studio·OpenRouter 등) / 데모
- [x] 화면에서 제공자·API 키·모델·주소 입력 → 로컬 `data/settings.json` 저장 (git 제외)
- [x] 키는 화면·API 응답에서 마스킹, 환경 변수도 계속 지원
- [x] 연결 테스트 버튼
- [x] 공개 배포용 설정 잠금 옵션 (`WHOWILLSEE_LOCK_SETTINGS=1`)
- [x] 테스트

### Step 10. 대시보드
- [x] 통계 API (문서·분석 수, 평균 점수, 페르소나별 평균, 카테고리×심각도 분포, 문서×페르소나 점수표, 최근 분석)
- [x] 대시보드 화면 (KPI 타일, 막대 차트, 히트맵 표, 툴팁)
- [x] 테스트 + 브라우저 확인

### Step 11. 수정본 비교
- [ ] 새 버전 업로드 (이전 문서와 연결)
- [ ] 이전 버전 대비 점수 변화, 해결/남음/새로 생김 지적 비교 API
- [ ] 결과 화면에 비교 패널
- [ ] 테스트

### Step 12. 한 줄 설명으로 페르소나 자동 생성
- [ ] 생성 API (AI 연결 시 AI, 아니면 규칙 기반 초안)
- [ ] 페르소나 폼에서 "✨ 자동 채우기"
- [ ] 테스트

### Step 13. 깃허브 공개 준비
- [ ] `.env.example`, GitHub Actions 테스트 워크플로
- [ ] Dockerfile (LibreOffice·Chromium 포함)
- [ ] README 갱신 (AI 연결 방법, 대시보드, 비교)
- [ ] 전체 테스트·브라우저 확인·푸시

## 4. 이후 확장 아이디어 (범위 밖)
- 청중 구성(여러 페르소나 비율)에 따른 종합 점수
- 발표 대본/음성까지 함께 평가
