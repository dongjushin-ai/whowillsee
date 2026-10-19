"""페르소나 정의, 기본 프리셋, 저장소."""
from __future__ import annotations

from pydantic import BaseModel, Field

from . import storage

DEFAULT_PERSONA_ID = "general-citizen"

LEVELS = {1: "전혀 모름", 2: "기초 수준", 3: "보통", 4: "능숙함", 5: "전문가 수준"}


class PersonaIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    icon: str = Field(default="🙂", max_length=8)
    age_group: str = ""
    occupation: str = ""  # 직군
    organization: str = ""  # 소속
    role: str = ""  # 직위·역할
    expertise_level: int = Field(default=2, ge=1, le=5)  # 자료 주제에 대한 전문성
    domain_knowledge: str = ""  # 잘 아는 분야
    digital_literacy: int = Field(default=3, ge=1, le=5)
    personality: str = ""  # 성격·성향
    needs: str = ""  # 이 자료에서 알고 싶은 것 / 목적
    preferences: str = ""  # 선호하는 전달 방식·취향
    concerns: str = ""  # 우려·민감한 점·싫어하는 것
    context: str = ""  # 자료를 접하는 상황 (시간, 장소, 매체)
    extra: str = ""  # 기타 메모


class Persona(PersonaIn):
    id: str
    builtin: bool = False

    def to_prompt(self) -> str:
        rows = [
            ("이름", self.name),
            ("연령대", self.age_group),
            ("직군", self.occupation),
            ("소속", self.organization),
            ("직위·역할", self.role),
            ("자료 주제에 대한 전문성", f"{self.expertise_level}/5 ({LEVELS[self.expertise_level]})"),
            ("잘 아는 분야", self.domain_knowledge),
            ("디지털 숙련도", f"{self.digital_literacy}/5 ({LEVELS[self.digital_literacy]})"),
            ("성격·성향", self.personality),
            ("니즈·목적", self.needs),
            ("선호·취향", self.preferences),
            ("우려·민감한 점", self.concerns),
            ("자료를 접하는 상황", self.context),
            ("기타", self.extra),
        ]
        return "\n".join(f"- {k}: {v}" for k, v in rows if str(v).strip())


PRESETS: list[Persona] = [
    Persona(
        id=DEFAULT_PERSONA_ID, builtin=True, icon="🧑", name="일반 시민",
        age_group="30~50대", occupation="회사원·자영업 등 다양", organization="해당 없음",
        role="정책·서비스의 수혜자", expertise_level=1,
        domain_knowledge="생활 상식 수준, 전문용어·영문 약어는 거의 모름", digital_literacy=3,
        personality="바쁘고 인내심이 길지 않음. 공공기관 자료에 약간의 불신이 있음",
        needs="그래서 나한테 뭐가 좋아지는지, 언제부터, 내가 무엇을 해야 하는지, 비용은 얼마인지",
        preferences="쉬운 우리말, 짧은 문장, 큰 글씨, 구체적인 예시와 그림",
        concerns="개인정보, 세금 낭비, 숨겨진 조건, 연락처·신청 방법이 없는 안내",
        context="스마트폰이나 설명회에서 1~2분 정도 훑어봄",
    ),
    Persona(
        id="busy-executive", builtin=True, icon="👔", name="바쁜 임원 (의사결정자)",
        age_group="50대", occupation="경영·관리", organization="대기업/기관 본부",
        role="예산·승인 결정권자", expertise_level=3,
        domain_knowledge="사업·재무 지표에 능숙, 기술 세부사항은 관심 없음", digital_literacy=3,
        personality="결론부터 듣고 싶어함, 숫자의 근거를 꼼꼼히 따짐",
        needs="핵심 결론, 비용·효과, 리스크, 내가 결정해야 할 사항",
        preferences="한 장 요약, 결론 우선(두괄식), 비교표",
        concerns="근거 없는 수치, 장황한 배경 설명, 책임 소재 불명확",
        context="회의 직전 3분 동안 검토",
    ),
    Persona(
        id="domain-expert", builtin=True, icon="🔬", name="분야 전문가 (검토위원)",
        age_group="40대", occupation="연구원·교수", organization="연구기관",
        role="외부 자문·심사", expertise_level=5,
        domain_knowledge="해당 분야 최신 동향과 방법론", digital_literacy=4,
        personality="비판적, 논리적 비약과 과장을 싫어함",
        needs="방법론의 타당성, 데이터 출처, 기존 대비 차별점",
        preferences="정확한 용어, 출처 표기, 정량 근거",
        concerns="부정확한 용어 사용, 출처 없는 그래프, 과장된 기대효과",
        context="심사 자료로 꼼꼼히 정독",
    ),
    Persona(
        id="senior-citizen", builtin=True, icon="👵", name="고령 어르신",
        age_group="70대", occupation="은퇴", organization="해당 없음",
        role="복지·행정 서비스 이용자", expertise_level=1,
        domain_knowledge="외래어·영어 약어를 거의 모름", digital_literacy=1,
        personality="천천히 읽지만 복잡하면 포기함, 전화 문의를 선호",
        needs="나도 받을 수 있는지, 어디로 가서/전화해서 신청하는지",
        preferences="아주 큰 글씨, 높은 명암 대비, 한 화면에 하나의 메시지",
        concerns="작은 글씨, 앱 설치·QR만 요구하는 안내, 사기 같은 느낌",
        context="주민센터 게시물이나 인쇄물로 봄 (노안)",
    ),
    Persona(
        id="journalist", builtin=True, icon="📰", name="기자",
        age_group="30대", occupation="언론", organization="일간지 사회부",
        role="보도자료·브리핑 취재", expertise_level=3,
        domain_knowledge="정책 전반을 넓고 얕게 앎", digital_literacy=4,
        personality="기사 제목이 될 한 줄을 찾음, 논란거리에 민감",
        needs="뉴스 가치, 핵심 숫자, 시민 영향, 반론 여지",
        preferences="명확한 수치와 날짜, 인용 가능한 문장",
        concerns="오해를 부를 표현, 모순된 수치, 미정·추후공지 같은 빈칸",
        context="마감 직전 빠르게 훑어봄",
    ),
]


# 기본 프리셋의 영어판 (언어를 영어로 고르면 이 값으로 보여주고 분석한다)
PRESETS_EN: dict[str, dict] = {
    DEFAULT_PERSONA_ID: dict(
        name="General citizen", age_group="30s–50s", occupation="Office workers, small business owners, etc.",
        organization="None", role="Beneficiary of the policy or service",
        domain_knowledge="Everyday common sense; barely knows jargon or English acronyms",
        personality="Busy and not very patient; slightly distrustful of government material",
        needs="What gets better for me, from when, what I need to do, and what it costs",
        preferences="Plain words, short sentences, large text, concrete examples and pictures",
        concerns="Privacy, wasted taxes, hidden conditions, notices without contacts or how to apply",
        context="Skims for 1–2 minutes on a phone or at a briefing"),
    "busy-executive": dict(
        name="Busy executive (decision maker)", age_group="50s", occupation="Management",
        organization="Headquarters of a large company or agency", role="Budget and approval authority",
        domain_knowledge="Fluent in business and financial metrics; not interested in technical detail",
        personality="Wants the conclusion first; scrutinizes the basis for every number",
        needs="Key conclusion, cost vs. benefit, risks, and the decision required of me",
        preferences="One-page summary, conclusion first, comparison tables",
        concerns="Unsupported numbers, long background, unclear accountability",
        context="Reviews for 3 minutes right before a meeting"),
    "domain-expert": dict(
        name="Domain expert (reviewer)", age_group="40s", occupation="Researcher / professor",
        organization="Research institute", role="External advisor / reviewer",
        domain_knowledge="Latest trends and methods in the field",
        personality="Critical; dislikes logical leaps and exaggeration",
        needs="Validity of the method, data sources, what is new compared with existing work",
        preferences="Precise terms, cited sources, quantitative evidence",
        concerns="Inaccurate terminology, charts without sources, inflated expected benefits",
        context="Reads closely as review material"),
    "senior-citizen": dict(
        name="Senior citizen", age_group="70s", occupation="Retired", organization="None",
        role="User of welfare and public services", domain_knowledge="Barely knows loanwords or English acronyms",
        personality="Reads slowly but gives up if it's complicated; prefers to call by phone",
        needs="Whether I qualify, and where to go or whom to call to apply",
        preferences="Very large text, high contrast, one message per screen",
        concerns="Small print, notices that only offer an app or QR code, anything that feels like a scam",
        context="Sees it as a printed notice or on a community-center board (presbyopia)"),
    "journalist": dict(
        name="Journalist", age_group="30s", occupation="Media", organization="Daily newspaper, city desk",
        role="Covers press releases and briefings", domain_knowledge="Broad but shallow knowledge of policy",
        personality="Looks for the one line that becomes a headline; sensitive to controversy",
        needs="News value, key numbers, impact on citizens, room for counterarguments",
        preferences="Clear numbers and dates, quotable sentences",
        concerns="Misleading phrasing, contradictory numbers, blanks like 'TBD'",
        context="Skims quickly right before deadline"),
}


def localize(persona: "Persona", lang: str) -> "Persona":
    if lang == "en" and persona.builtin and persona.id in PRESETS_EN:
        return persona.model_copy(update=PRESETS_EN[persona.id])
    return persona


def _path():
    return storage.DATA_DIR / "personas.json"


def _load_custom() -> list[Persona]:
    return [Persona(**p) for p in storage.read_json(_path(), default=[])]


def _save_custom(items: list[Persona]) -> None:
    storage.write_json(_path(), [p.model_dump() for p in items])


def list_personas(lang: str = "ko") -> list[Persona]:
    return [localize(p, lang) for p in PRESETS] + _load_custom()


def get_persona(persona_id: str, lang: str = "ko") -> Persona | None:
    return next((p for p in list_personas(lang) if p.id == persona_id), None)


def create_persona(data: PersonaIn) -> Persona:
    items = _load_custom()
    persona = Persona(id="p-" + storage.new_id(), **data.model_dump())
    items.append(persona)
    _save_custom(items)
    return persona


def update_persona(persona_id: str, data: PersonaIn) -> Persona:
    items = _load_custom()
    for i, p in enumerate(items):
        if p.id == persona_id:
            items[i] = Persona(id=persona_id, **data.model_dump())
            _save_custom(items)
            return items[i]
    if any(p.id == persona_id for p in PRESETS):
        raise PermissionError("기본 프리셋은 수정할 수 없습니다. 복제해서 사용하세요.")
    raise KeyError(persona_id)


def delete_persona(persona_id: str) -> None:
    if any(p.id == persona_id for p in PRESETS):
        raise PermissionError("기본 프리셋은 삭제할 수 없습니다.")
    items = _load_custom()
    remaining = [p for p in items if p.id != persona_id]
    if len(remaining) == len(items):
        raise KeyError(persona_id)
    _save_custom(remaining)
