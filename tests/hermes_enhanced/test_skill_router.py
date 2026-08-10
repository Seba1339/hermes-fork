"""Behavior tests for the personal skill router (hermes_enhanced/skill_router.py).

This module is not part of Hermes core — it backs the live
`hermes-enhanced-gateway` deployment's skill auto-loading (see
enhanced_init.py, which monkey-patches AIAgent.run_conversation to inject
`auto_load()`'s picks into the system message). It has no test coverage
today; these tests pin the deterministic classify/auto_load contract so the
live gateway's behavior can't silently regress.

Note: `skill_router.HERMES_HOME` / `SKILLS_DIR` are resolved from the
HERMES_HOME env var at *import* time (module-level constants), not at call
time. Tests that need a custom skills directory therefore monkeypatch
`skill_router.SKILLS_DIR` directly rather than relying on the HERMES_HOME
env var isolation fixture (which only takes effect for code that reads the
env var lazily).
"""
from hermes_enhanced import skill_router


class TestClassify:
    def test_no_match_returns_empty_list(self):
        assert skill_router.classify("xyzzy plugh qwerty") == []

    def test_bujo_keyword_matches_bullet_journal(self):
        results = skill_router.classify("necesito agregar una tarea a mi bujo")
        skills = [r["skill"] for r in results]
        assert "bullet-journal" in skills

    def test_results_sorted_by_relevance_descending(self):
        results = skill_router.classify("bug error debug traceback bujo")
        relevances = [r["relevance"] for r in results]
        assert relevances == sorted(relevances, reverse=True)

    def test_each_result_reports_which_patterns_matched(self):
        results = skill_router.classify("tengo un bug con traceback")
        debugging = next(r for r in results if r["skill"] == "systematic-debugging")
        assert debugging["relevance"] == len(debugging["matches"])
        assert debugging["matches"]

    def test_custom_triggers_override_default_map(self):
        results = skill_router.classify(
            "banana", triggers={"fruit-skill": [r"banana"]},
        )
        assert results == [{"skill": "fruit-skill", "relevance": 1, "matches": ["banana"]}]


class TestAutoLoad:
    def test_returns_at_most_max_skills(self):
        # A message stuffed with triggers from many different skills must
        # still be capped at max_skills, not just at result length.
        message = "bug error debug bujo agenda review revisar plan arquitectura tdd test"
        selected = skill_router.auto_load(message, max_skills=3)
        assert len(selected) <= 3

    def test_never_selects_more_than_two_high_priority_skills(self):
        # HIGH_PRIORITY skills are capped at 2 even when many match and
        # max_skills allows more — this is what keeps the injected system
        # message small regardless of how broad the trigger match is.
        message = "bujo bug debug health salud finanzas trading github"
        selected = skill_router.auto_load(message, max_skills=10)
        high_priority_selected = [s for s in selected if s in skill_router.HIGH_PRIORITY]
        assert len(high_priority_selected) <= 2

    def test_no_match_returns_empty_list(self):
        assert skill_router.auto_load("xyzzy plugh qwerty") == []

    def test_selection_has_no_duplicates(self):
        selected = skill_router.auto_load("bujo bujo bujo tarea nota", max_skills=5)
        assert len(selected) == len(set(selected))

    def test_is_deterministic_for_the_same_input(self):
        message = "tengo un bug en el bujo y necesito revisar arquitectura"
        first = skill_router.auto_load(message, max_skills=5)
        second = skill_router.auto_load(message, max_skills=5)
        assert first == second


class TestLoadSkillTriggers:
    def test_falls_back_to_hardcoded_map_when_skills_dir_is_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(skill_router, "SKILLS_DIR", tmp_path / "does-not-exist")
        triggers = skill_router.load_skill_triggers()
        assert triggers == skill_router.TRIGGER_MAP

    def test_frontmatter_triggers_extend_the_hardcoded_map(self, tmp_path, monkeypatch):
        monkeypatch.setattr(skill_router, "SKILLS_DIR", tmp_path)
        skill_dir = tmp_path / "my-custom-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: my-custom-skill\n"
            "triggers:\n"
            "  - custom-trigger-phrase\n"
            "---\n"
            "# My Custom Skill\n"
        )
        triggers = skill_router.load_skill_triggers()
        assert triggers["my-custom-skill"] == ["custom-trigger-phrase"]
        # Hardcoded entries must still be present — frontmatter extends,
        # it does not replace, the built-in fallback map.
        assert "bullet-journal" in triggers

    def test_malformed_frontmatter_is_skipped_without_raising(self, tmp_path, monkeypatch):
        monkeypatch.setattr(skill_router, "SKILLS_DIR", tmp_path)
        skill_dir = tmp_path / "broken-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("---\nnot: [valid, yaml: :\n---\nbody\n")
        triggers = skill_router.load_skill_triggers()
        assert triggers == skill_router.TRIGGER_MAP
