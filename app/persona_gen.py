"""한 줄 설명으로 페르소나 초안을 만든다 (저장은 사용자가 검토 후에 한다)."""
from __future__ import annotations

import re

from . import providers
from .messages import m
from .personas import PersonaIn

_TEXT_FIELDS = ["name", "icon", "age_group", "occupation", "organization", "role", "domain_knowledge",
                "personality", "needs", "preferences", "concerns", "context", "extra"]

SCHEMA = {
    "type": "object",
    "properties": {**{f: {"type": "string"} for f in _TEXT_FIELDS},
                   "expertise_level": {"type": "integer"}, "digital_literacy": {"type": "integer"}},
    "required": _TEXT_FIELDS + ["expertise_level", "digital_literacy"],
    "additionalProperties": False,
}

SYSTEM = """당신은 발표·브리핑 자료의 청중 페르소나를 설계하는 UX 리서처입니다.
사용자가 준 짧은 설명으로 자료 검토에 쓸 구체적인 페르소나를 만드세요.
- 설명에 없는 항목은 그 사람에게 전형적이고 그럴듯한 내용으로 추정하되, 고정관념이나 비하는 피하세요.
- needs(니즈·목적), concerns(우려·민감한 점), context(자료를 접하는 상황)는 자료 검토에 바로 쓸 수 있게 구체적으로 쓰세요.
- expertise_level 은 '자료 주제'에 대한 전문성, digital_literacy 는 디지털 숙련도이며 둘 다 1(전혀 모름)~5(전문가) 정수입니다.
- name 은 30자 이내의 짧은 이름 (예: "30대 맞벌이 학부모"), icon 은 이모지 1개입니다.
- 모든 글은 한국어로 씁니다."""


def generate(description: str, lang: str = "ko") -> tuple[PersonaIn, str | None]:
    """(페르소나 초안, 사용한 AI 엔진 이름 - 규칙 기반이면 None)."""
    description = description.strip()
    provider = providers.get_provider()  # 설정 오류는 ProviderError 로 전달
    if provider is None:
        return rule_based(description, lang), None
    text = f"설명: {description}" + ("" if lang == "ko" else "\n\n" + m(lang, "ai.output_language"))
    raw = provider.complete_json(SYSTEM, [{"type": "text", "text": text}], SCHEMA, max_tokens=4000)
    data = {f: str(raw.get(f) or "").strip() for f in _TEXT_FIELDS}
    data["name"] = (data["name"] or description)[:60]
    data["icon"] = (data["icon"] or "🙂")[:8]
    for f in ("expertise_level", "digital_literacy"):
        try:
            data[f] = min(5, max(1, int(raw.get(f, 3))))
        except (TypeError, ValueError):
            data[f] = 3
    return PersonaIn(**data), provider.engine


_RULES = [
    # (패턴, 아이콘, 직군, 전문성, 디지털 숙련도, 니즈, 선호, 우려)
    (r"교수|연구원|박사|전문가|심사|검토위원", "🔬", "연구·전문직", 5, 4,
     "근거와 방법론의 타당성", "정확한 용어, 출처, 정량 근거", "과장된 표현, 출처 없는 수치"),
    (r"임원|대표|사장|국장|본부장|팀장|결재|의사결정", "👔", "경영·관리", 3, 3,
     "핵심 결론, 비용 대비 효과, 결정할 사항", "한 장 요약, 두괄식", "장황한 배경 설명, 근거 없는 수치"),
    (r"기자|언론|PD", "📰", "언론", 3, 4,
     "뉴스 가치, 핵심 숫자, 시민 영향", "인용 가능한 문장, 명확한 날짜", "모순된 수치, '미정' 같은 빈칸"),
    (r"어르신|노인|할머니|할아버지|[6-9]0대|은퇴", "👵", "은퇴", 1, 1,
     "내가 받을 수 있는지, 어디로 가서 신청하는지", "아주 큰 글씨, 쉬운 말, 전화 문의", "작은 글씨, 앱 설치만 요구하는 안내"),
    (r"학생|대학생|청소년|고등학생", "🎒", "학생", 2, 5,
     "나와 무슨 상관인지, 참여 방법", "짧고 시각적인 정보, 모바일", "지루한 표현, 어른들만의 용어"),
    (r"학부모|엄마|아빠|부모", "👪", "학부모", 2, 3,
     "아이에게 미치는 영향, 일정, 비용", "구체적 예시, 일정표", "안전 문제, 추가 비용"),
    (r"개발자|엔지니어|IT|프로그래머", "💻", "IT·개발", 4, 5,
     "기술 구조와 구현 가능성", "정확한 기술 용어, 다이어그램", "모호한 기술 설명, 과장"),
    (r"공무원|주무관|사무관", "🏛️", "공공기관", 3, 3,
     "규정 근거, 추진 일정, 협조 사항", "공문 형식, 근거 법령", "책임 소재 불명확"),
]


# 규칙 결과의 영어판: 직군 → (직군, 니즈, 선호, 우려)
_RULES_EN = {
    "연구·전문직": ("Research / professional", "Validity of the evidence and method", "Precise terms, sources, quantitative evidence",
               "Exaggeration, numbers without sources"),
    "경영·관리": ("Management", "Key conclusion, cost vs. benefit, decisions required", "One-page summary, conclusion first",
              "Long background, unsupported numbers"),
    "언론": ("Media", "News value, key numbers, impact on citizens", "Quotable sentences, clear dates",
           "Contradictory numbers, blanks like 'TBD'"),
    "은퇴": ("Retired", "Whether I qualify and where to apply", "Very large text, plain words, phone support",
           "Small print, notices that only offer an app"),
    "학생": ("Student", "How it relates to me and how to take part", "Short visual information, mobile",
           "Boring wording, grown-up jargon"),
    "학부모": ("Parent", "Impact on my child, schedule, cost", "Concrete examples, a timetable", "Safety, extra costs"),
    "IT·개발": ("IT / software", "Architecture and feasibility", "Precise technical terms, diagrams",
              "Vague technical claims, exaggeration"),
    "공공기관": ("Public sector", "Legal basis, schedule, what's needed from us", "Official format, cited regulations",
             "Unclear accountability"),
}
_RULES_EN_PATTERNS = [
    (r"professor|researcher|phd|expert|reviewer", "연구·전문직"),
    (r"executive|ceo|director|manager|decision", "경영·관리"),
    (r"journalist|reporter|press|media", "언론"),
    (r"senior|elderly|retired|grand(mother|father)|[6-9]0s", "은퇴"),
    (r"student|teen", "학생"),
    (r"parent|mom|dad|mother|father", "학부모"),
    (r"developer|engineer|programmer|\bit\b", "IT·개발"),
    (r"civil servant|official|government", "공공기관"),
]


def rule_based(description: str, lang: str = "ko") -> PersonaIn:
    head, *rest = [part.strip() for part in re.split(r"[,，;/]", description) if part.strip()] or ["새 페르소나"]
    if len(head) > 40:  # 단어 중간에서 자르지 않는다
        head = head[:40].rsplit(" ", 1)[0] if " " in head[:40] else head[:40]
    data = {"name": head, "icon": "🙂", "extra": description, "personality": ", ".join(rest),
            "expertise_level": 2, "digital_literacy": 3}
    age = re.search(r"([1-9]0)대", description) or re.search(r"\b([1-9]0)s\b", description)
    if age:
        data["age_group"] = f"{age.group(1)}대" if lang == "ko" else f"{age.group(1)}s"
    english_hit = next((occ for pat, occ in _RULES_EN_PATTERNS if re.search(pat, description, re.I)), None)
    for pattern, icon, occupation, exp, digital, needs, prefs, concerns in _RULES:
        if re.search(pattern, description) or occupation == english_hit:
            data.update(icon=icon, occupation=occupation, expertise_level=exp, digital_literacy=digital,
                        needs=needs, preferences=prefs, concerns=concerns)
            if lang == "en":
                occ, needs, prefs, concerns = _RULES_EN[occupation]
                data.update(occupation=occ, needs=needs, preferences=prefs, concerns=concerns)
            break
    if data["name"] == "새 페르소나" and lang == "en":
        data["name"] = "New persona"
    return PersonaIn(**data)
