import pytest

from app import personas


def test_default_persona_is_general_citizen():
    first = personas.list_personas()[0]
    assert first.id == personas.DEFAULT_PERSONA_ID
    assert first.name == "일반 시민"
    assert "전문성: 1/5" in first.to_prompt()


def test_crud_custom_persona():
    p = personas.create_persona(personas.PersonaIn(name="초등 교사", occupation="교육", expertise_level=2))
    assert p.id.startswith("p-") and not p.builtin
    assert personas.get_persona(p.id).occupation == "교육"

    updated = personas.update_persona(p.id, personas.PersonaIn(name="초등 교사", needs="수업 활용"))
    assert updated.needs == "수업 활용"
    assert "니즈·목적: 수업 활용" in updated.to_prompt()
    assert "직군" not in updated.to_prompt()  # 빈 항목은 프롬프트에서 생략

    personas.delete_persona(p.id)
    assert personas.get_persona(p.id) is None


def test_presets_are_read_only():
    with pytest.raises(PermissionError):
        personas.delete_persona(personas.DEFAULT_PERSONA_ID)
    with pytest.raises(PermissionError):
        personas.update_persona(personas.DEFAULT_PERSONA_ID, personas.PersonaIn(name="x"))


def test_validation():
    with pytest.raises(ValueError):
        personas.PersonaIn(name="", expertise_level=3)
    with pytest.raises(ValueError):
        personas.PersonaIn(name="a", expertise_level=9)


def test_rule_based_generation():
    from app import persona_gen

    p, engine = persona_gen.generate("70대 독거 어르신, 스마트폰 서툼")
    assert engine is None
    assert p.age_group == "70대" and p.icon == "👵" and p.digital_literacy == 1
    assert p.name == "70대 독거 어르신" and p.personality == "스마트폰 서툼"
    p, _ = persona_gen.generate("예산 결재권자인 구청 국장")
    assert p.occupation == "경영·관리" and p.expertise_level == 3


def test_ai_generation_is_clamped(monkeypatch):
    from app import persona_gen, providers

    class Fake:
        engine = "Fake · m"

        def complete_json(self, system, content, schema, max_tokens=0):
            assert "설명: 간호사" in content[0]["text"]
            return {"name": "야간 근무 간호사", "icon": "🩺", "occupation": "간호사", "expertise_level": 9,
                    "digital_literacy": "x", "needs": "근무 중 짧게 확인"}

    monkeypatch.setattr(providers, "get_provider", lambda s=None: Fake())
    p, engine = persona_gen.generate("간호사")
    assert engine == "Fake · m"
    assert p.expertise_level == 5 and p.digital_literacy == 3 and p.needs == "근무 중 짧게 확인"
