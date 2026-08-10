"""Behavior tests for the deterministic perspective router.

perspective_router (tools/perspective_router.py) is a pure classifier used by
the personal Luna/Enhanced layer to pick which model perspectives (claude,
gemini, deepseek) to consult for a given task. It never calls a model itself,
so its contract is fully deterministic and safe to pin with invariant tests.
"""
import json

from tools.perspective_router import _classify, _handle_perspective_router


class TestClassify:
    def test_empty_or_unmatched_text_routes_to_simple_with_no_perspectives(self):
        category, perspectives, trigger = _classify("hola, como estas")
        assert category == "simple"
        assert perspectives == ()

    def test_security_keyword_routes_to_safety_with_claude(self):
        category, perspectives, _trigger = _classify("revisa esta vulnerabilidad de seguridad")
        assert category == "safety"
        assert "claude" in perspectives

    def test_health_keyword_routes_to_claude_only(self):
        category, perspectives, _trigger = _classify("mi presión subió mucho hoy")
        assert category == "health"
        assert perspectives == ("claude",)

    def test_first_matching_rule_wins_when_keywords_overlap_across_categories(self):
        # "seguridad" (safety) appears before any architecture keyword in the
        # rule table, so a message containing both must resolve to the first
        # rule match, not the lexically later one. This pins _ROUTE_RULES
        # order-sensitivity so reordering the table is a deliberate act.
        category, _perspectives, _trigger = _classify(
            "necesito refactor de arquitectura y también seguridad"
        )
        assert category == "architecture"


class TestHandlePerspectiveRouter:
    def _call(self, **kwargs):
        return json.loads(_handle_perspective_router(kwargs))

    def test_missing_task_is_a_tool_error(self):
        result = self._call(task="")
        assert "error" in result
        assert result["code"] == "invalid_task"

    def test_oversized_task_is_a_tool_error(self):
        result = self._call(task="x" * 20_001)
        assert "error" in result
        assert result["code"] == "task_too_large"

    def test_simple_task_recommends_direct_answer_with_no_perspectives(self):
        result = self._call(task="dime la hora")
        assert result["success"] is True
        assert result["route"] == "simple"
        assert result["perspectives"] == []

    def test_high_risk_always_includes_claude(self):
        result = self._call(task="optimiza este algoritmo de trading", risk="alto")
        assert "claude" in result["perspectives"]

    def test_perspectives_list_has_no_duplicates(self):
        result = self._call(task="seguridad y vulnerabilidad critica", risk="critical")
        assert len(result["perspectives"]) == len(set(result["perspectives"]))

    def test_max_external_perspectives_never_exceeds_two(self):
        result = self._call(task="arquitectura, seguridad, rendimiento y diseño creativo")
        assert result["max_external_perspectives"] <= 2

    def test_output_contract_is_stable(self):
        # Downstream (Luna's synthesis step) depends on these exact keys
        # existing in every response, regardless of route.
        result = self._call(task="cualquier cosa")
        assert result["output_contract"] == [
            "conclusion", "supuestos", "riesgos", "confianza", "evidencia_necesaria",
        ]
