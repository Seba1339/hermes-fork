"""
Hermes-Enhanced Skill Router
Clasificador de intencion del usuario -> skills relevantes.
Usa matching por keywords/triggers definidos en el frontmatter de cada skill.

Uso desde el agente:
    from hermes_tools import terminal
    result = terminal("python3 ~/.hermes-enhanced/scripts/skill_router.py 'mensaje del usuario'")
    skills_a_cargar = json.loads(result['output'])
    for s in skills_a_cargar: skill_view(s)
"""

import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple

# ── Config ──────────────────────────────────────────────────────────────
HERMES_HOME = Path(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes-enhanced")))
SKILLS_DIR = HERMES_HOME / "skills"

# Mapa de triggers por skill (nombre -> lista de patrones)
# Estos se cargan desde el frontmatter de cada skill cuando existe el campo "triggers"
# Fallback hardcoded para los skills mas comunes
TRIGGER_MAP: Dict[str, List[str]] = {
    # --- Bullet Journal ---
    "bullet-journal": [
        r"\bbu[ij][oó]\b", r"\bjournal\b", r"\bagenda\b", r"\bdiario\b",
        r"recordatorio", r"tarea", r"evento", r"nota", r"compr[ae]",
        r"\bpago\b", r"\bcita\b", r"\bentrada\b", r"\bentry\b",
        r"radiograf[ií]a", r"briefing",
    ],
    "bujo-writing-workflow": [
        r"\bescribir\b", r"\bwrite\b", r"\bguardar\b", r"\bsave\b",
        r"\ba[ñn]adir\b", r"\badd\b", r"nueva entrada",
    ],
    "bujo-markdown": [
        r"markdown", r"editor", r"tiptap", r"prosemirror",
        r"secci[oó]n", r"indent", r"render",
    ],
    "bujo-logseq-app": [
        r"logseq", r"visual", r"interfaz", r"web\s*interface",
    ],
    "bujo-offline-first": [
        r"offline", r"indexeddb", r"cache", r"sync", r"sin\s*conexi[oó]n",
    ],
    "bujo-semantic-search": [
        r"segundo\s*cerebro", r"sem[aá]ntic[ao]", r"b[uú]squeda",
        r"knowledge", r"conocimiento", r"insight",
    ],
    "bujo-slash-menu-and-reminders": [
        r"slash", r"comando", r"reminder", r"notificaci[oó]n",
        r"men[uú]", r"barra\s*diagonal",
    ],
    "bujo-hermes-chat": [
        r"chat.*bu[jj]o", r"bu[jj]o.*chat", r"floating.*chat",
        r"fab.*chat",
    ],
    "bujo-fastapi-react-panel": [
        r"panel.*lateral", r"sidebar.*panel", r"daily.*journal.*panel",
    ],
    "bujo-complete-and-briefing-fixes": [
        r"briefing", r"completado", r"done.*state", r"event.*mark",
    ],
    "bujo-indent-debug": [
        r"indent", r"sangr[ií]a", r"debug.*bu[jj]o", r"bu[jj]o.*debug",
    ],

    # --- Debugging ---
    "systematic-debugging": [
        r"\bbug\b", r"error", r"fall[ao]", r"debug", r"issue",
        r"no\s*funci[oó]na", r"roto", r"broken", r"crash",
        r"exception", r"traceback", r"stack.*trace",
    ],

    # --- Code Quality ---
    "hermes-enhanced-coding": [
        r"calidad", r"quality", r"deuda.*t[eé]cnica", r"refactor",
        r"clean\s*code", r"buenas\s*pr[aá]cticas", r"c[oó]digo.*limpio",
    ],
    "requesting-code-review": [
        r"review", r"revisi[oó]n", r"c[oó]digo.*revisar", r"code\s*review",
        r"auditar", r"audit",
    ],
    "audit-stack": [
        r"auditor[ií]a", r"audit", r"linter", r"ruff", r"mypy",
        r"biome", r"trufflehog", r"lighthouse",
    ],
    "test-driven-development": [
        r"\btdd\b", r"test", r"prueba", r"pytest", r"regression",
        r"red.*green", r"unit\s*test",
    ],
    "subagent-driven-development": [
        r"subagent", r"delegate", r"paralel[oa]", r"multi.*agente",
    ],
    "writing-plans": [
        r"plan", r"implementaci[oó]n", r"arquitectura",
        r"descomponer", r"decomposition",
    ],

    # --- Web App / PWA ---
    "pwa-websocket-resilience": [
        r"websocket", r"traba", r"conexi[oó]n.*pierde", r"reconnect",
        r"stuck.*connection", r"frozen",
    ],
    "pwa-installability-debugging": [
        r"instalaci[oó]n.*pwa", r"pwa.*instalar", r"install.*prompt",
        r"manifest.*json", r"service.*worker.*install",
    ],
    "pwa-update-detection": [
        r"actualizaci[oó]n.*pwa", r"pwa.*update", r"stale.*code",
        r"service.*worker.*update",
    ],
    "pwa-resilience": [
        r"wake.*lock", r"push.*disconnect", r"visibility.*recovery",
        r"pwa.*resilien",
    ],
    "spa-session-init-ordering": [
        r"sesi[oó]n.*vac[ií]a", r"blank.*page", r"load.*empty",
        r"api.*null.*session",
    ],
    "js-event-driven-ui-sync": [
        r"ui.*actualiz", r"indicador.*estado", r"stale.*value",
        r"event.*driven.*sync",
    ],

    # --- Flutter / Android ---
    "flutter-android-alarms": [
        r"alarma.*android", r"flutter.*alarm", r"alarmmanager",
        r"notificaci[oó]n.*persistente",
    ],
    "flutter-android-native-bridge": [
        r"flutter.*native", r"methodchannel", r"dart.*kotlin",
        r"webview.*flutter",
    ],
    "flutter-pwa-native-wrapper": [
        r"pwa.*flutter", r"flutter.*wrapper", r"alarm.*bridge",
        r"websocket.*alarm",
    ],
    "flutter-android-build": [
        r"flutter.*build", r"apk", r"aab", r"gradle",
        r"android.*compil", r"build.*flutter",
    ],
    "flutter-ci-build": [
        r"ci.*flutter", r"github.*actions.*flutter", r"flutter.*ci",
    ],
    "flutter-native-android-bridge": [
        r"kotlin.*flutter", r"native.*android.*flutter",
    ],

    # --- Flask / Backend ---
    "flask-socketio-realtime": [
        r"socket\.io", r"flask.*socket", r"tiempo.*real",
        r"realtime", r"websocket.*flask",
    ],
    "flask-chat-file-attachments": [
        r"adjuntar", r"attachment", r"file.*upload", r"imagen.*chat",
        r"multipart.*chat",
    ],
    "flask-security-hardening": [
        r"seguridad.*flask", r"flask.*security", r"hardening",
        r"xss", r"csrf", r"sql.*injection",
    ],
    "flask-proxy-pwa-field-mismatch": [
        r"field.*mismatch", r"nombre.*campo.*difiere",
        r"flask.*proxy.*pwa",
    ],
    "external-api-resilience": [
        r"api.*externa.*lenta", r"timeout.*api", r"resilien.*api",
        r"external.*api.*slow",
    ],
    "api-driven-webapp-testing": [
        r"e2e.*test", r"api.*test", r"endpoint.*test",
        r"playwright.*test",
    ],

    # --- DevOps ---
    "diagnose-and-supervise-webapp": [
        r"web.*ca[ií]do", r"app.*down", r"no.*responde",
        r"systemd.*app", r"supervisar.*web",
    ],
    "reverse-proxy-api-misrouting": [
        r"proxy.*inverso", r"caddy.*api", r"nginx.*api",
        r"reverse.*proxy.*misrout",
    ],
    "automation-health-audit": [
        r"cron.*audit", r"automatizaci[oó]n.*auditar",
        r"schedule.*health",
    ],

    # --- GitHub / Git ---
    "github-pr-workflow": [
        r"\bpr\b", r"pull.*request", r"merge", r"branch",
        r"abrir.*pr", r"crear.*pr",
    ],
    "github-code-review": [
        r"code.*review.*pr", r"revisar.*pr", r"diff.*review",
        r"inline.*comment",
    ],
    "github-issues": [
        r"issue", r"\bticket\b", r"\bbug.*track",
    ],
    "github-auth": [
        r"github.*auth", r"token.*github", r"ssh.*github",
        r"gh.*login",
    ],
    "github-repo-management": [
        r"clonar.*repo", r"fork", r"clone.*repo", r"crear.*repo",
    ],
    "codebase-inspection": [
        r"inspeccionar.*c[oó]digo", r"codebase.*inspect",
        r"loc.*count", r"lenguajes.*proyecto",
    ],

    # --- Research ---
    "arxiv": [
        r"arxiv", r"paper", r"art[ií]culo.*acad", r"investigaci[oó]n",
        r"research.*paper",
    ],
    "youtube-content": [
        r"youtube.*transcrip", r"video.*resumir", r"transcript",
        r"youtube.*summary",
    ],
    "blogwatcher": [
        r"blog.*feed", r"rss", r"feed.*monitor",
    ],

    # --- Productivity ---
    "personal-finance-tracking": [
        r"finanza", r"gasto", r"ingreso", r"banco", r"presupuesto",
        r"finance.*track",
    ],
    "google-workspace": [
        r"gmail", r"google.*calendar", r"google.*drive",
        r"google.*docs", r"google.*sheets",
    ],
    "obsidian": [
        r"obsidian", r"b[oó]veda", r"vault", r"nota.*markdown",
    ],
    "notion": [
        r"notion", r"ntn.*cli",
    ],
    "linear": [
        r"linear.*issue", r"linear.*project",
    ],
    "airtable": [
        r"airtable", r"base.*datos.*airtable",
    ],
    "teams-meeting-pipeline": [
        r"teams.*meeting", r"reuni[oó]n.*resumir", r"meeting.*summary",
        r"microsoft.*graph.*subscript",
    ],

    # --- Creative ---
    "excalidraw": [
        r"excalidraw", r"diagrama.*mano", r"hand.*drawn",
        r"arch.*diagram", r"flow.*diagram",
    ],
    "architecture-diagram": [
        r"diagrama.*arquitectura", r"architecture.*svg",
        r"infra.*diagram", r"cloud.*diagram",
    ],
    "sketch": [
        r"mockup", r"html.*mockup", r"prototipo.*rapido",
        r"design.*variant",
    ],
    "p5js": [
        r"p5\.js", r"processing.*sketch", r"gen.*art",
        r"visualizaci[oó]n.*creativa",
    ],

    # --- Hermes Agent Internals ---
    "hermes-agent": [
        r"hermes.*config", r"hermes.*setup", r"hermes.*tool",
        r"configurar.*hermes", r"hermes.*provider",
        r"hermes.*skill", r"hermes.*plugin",
    ],
    "hermes-enhanced-self": [
        r"enhanced", r"mejora", r"hermes.*modificado",
        r"c[oo]mo.*funcionas", r"que.*eres",
    ],
    "hermes-chat-v2": [
        r"hermes.*chat.*v2", r"chat.*app.*hermes",
        r"hermes.*fastapi",
    ],

    # --- MCP ---
    "native-mcp": [
        r"\bmcp\b", r"model.*context.*protocol", r"herramientas.*mcp",
        r"mcp.*server",
    ],

    # --- Crypto ---
    "crypto-trading": [
        r"binance", r"crypto", r"trading.*bot", r"dca.*bot",
        r"swing.*trading",
    ],

    # --- Health ---
    "salud-dashboard": [
        r"salud", r"glucosa", r"diabetes", r"libre.*link",
        r"health.*dashboard", r"exam.*medic",
    ],

    # --- Multi-agent ---
    "kanban-orchestrator": [
        r"kanban.*orchestrat", r"orquestar", r"orchestrat",
    ],
    "kanban-worker": [
        r"kanban.*worker", r"tarea.*kanban", r"board.*task",
    ],
    "claude-code": [
        r"claude.*code", r"claude.*cli",
    ],
    "codex": [
        r"codex.*cli", r"openai.*codex",
    ],
    "opencode": [
        r"opencode.*cli",
    ],
    "parallel-subagent-fullstack-rebuild": [
        r"fullstack.*rebuild", r"full.*stack.*paralel",
        r"subagent.*fullstack",
    ],
}

# Prioridades: skills que siempre revisar primero
HIGH_PRIORITY = [
    "bullet-journal",
    "systematic-debugging",
    "hermes-agent",
    "hermes-enhanced-self",
    "hermes-enhanced-coding",
]

# Skills que requieren contexto adicional para no falsos positivos
CONTEXT_REQUIRED = {
    "test-driven-development": ["test", "prueba", "pytest", "tdd", "regression"],
    "github-pr-workflow": ["pr", "pull request", "merge", "branch"],
    "github-code-review": ["review", "revisar"],
}


def load_skill_triggers() -> Dict[str, List[str]]:
    """Carga triggers desde el frontmatter de skills en disco.
    Los skills que tengan campo 'triggers' en su frontmatter extienden o
    reemplazan los del mapa hardcoded.
    """
    triggers = dict(TRIGGER_MAP)  # copia

    if not SKILLS_DIR.exists():
        return triggers

    for root, dirs, files in os.walk(SKILLS_DIR):
        if "SKILL.md" not in files:
            continue
        skill_path = Path(root) / "SKILL.md"
        try:
            content = skill_path.read_text(encoding="utf-8", errors="replace")
            # Parse frontmatter simple (--- delimitado)
            if not content.startswith("---"):
                continue
            _, fm_part, _ = content.split("---", 2)
            import yaml
            try:
                fm = yaml.safe_load(fm_part)
            except Exception:
                continue
            if not isinstance(fm, dict):
                continue
            skill_name = fm.get("name", "")
            if not skill_name:
                continue
            skill_triggers = fm.get("triggers", [])
            if isinstance(skill_triggers, list) and skill_triggers:
                triggers[skill_name] = [str(t) for t in skill_triggers]
            elif isinstance(skill_triggers, dict):
                # Formato: {keywords: [...], patterns: [...]}
                kw = skill_triggers.get("keywords", [])
                pat = skill_triggers.get("patterns", [])
                combined = [str(k) for k in kw] + [str(p) for p in pat]
                if combined:
                    triggers[skill_name] = combined
        except Exception as e:
            # Silencio - no romper por un skill mal formado
            pass

    return triggers


def classify(message: str, triggers: Dict[str, List[str]] = None) -> List[Dict]:
    """Clasifica un mensaje del usuario y retorna skills relevantes.

    Returns:
        Lista de dicts: {skill, relevance, matches}
        Ordenada por relevancia (numero de matches descendente).
    """
    if triggers is None:
        triggers = TRIGGER_MAP

    msg_lower = message.lower()
    results = []

    for skill_name, patterns in triggers.items():
        matches = []
        for pattern in patterns:
            try:
                if re.search(pattern, msg_lower, re.IGNORECASE):
                    matches.append(pattern)
            except re.error:
                # Si el patron no es regex valido, probar como substring
                if pattern.lower() in msg_lower:
                    matches.append(pattern)

        if matches:
            results.append({
                "skill": skill_name,
                "relevance": len(matches),
                "matches": matches[:5],  # max 5 ejemplos
            })

    # Ordenar por relevancia (mas matches primero)
    results.sort(key=lambda r: (-r["relevance"], r["skill"]))

    return results


def auto_load(message: str, max_skills: int = 5) -> List[str]:
    """Clasifica y retorna solo los nombres de skills a cargar.
    Usa umbral de relevancia para evitar falsos positivos.
    """
    triggers = load_skill_triggers()
    results = classify(message, triggers)

    # Siempre incluir high priority si hay match parcial
    high_matches = []
    normal_matches = []

    for r in results:
        if r["skill"] in HIGH_PRIORITY and r["relevance"] >= 1:
            high_matches.append(r["skill"])
        elif r["relevance"] >= 1:  # umbral minimo
            normal_matches.append(r["skill"])

    # Combinar: high priority primero, luego el resto hasta max_skills
    selected = high_matches[:2]  # max 2 high priority
    for s in normal_matches:
        if len(selected) >= max_skills:
            break
        if s not in selected:
            selected.append(s)

    return selected


def main():
    """CLI entry point.
    Uso: python3 skill_router.py 'mensaje del usuario'
    """
    if len(sys.argv) < 2:
        message = sys.stdin.read().strip()
    else:
        message = " ".join(sys.argv[1:])

    if not message:
        print(json.dumps({"skills": [], "message": "No input provided"}))
        return

    triggers = load_skill_triggers()
    results = classify(message, triggers)
    selected = auto_load(message)

    output = {
        "skills": selected,
        "all_matches": results,
        "total_matches": len(results),
        "message_preview": message[:100],
    }
    print(json.dumps(output, indent=2, ensure_ascii=False))


# ── Semantic Matching ───────────────────────────────────────────────────
# Uses sentence-transformers for embedding-based skill classification.
# Supplements regex triggers when they don't capture intent.

_SBERT_MODEL = None
_SKILL_EMBEDDINGS = None
_EMBEDDING_CACHE = os.path.expanduser("~/.hermes-enhanced/data/skill_embeddings.json")


def _get_skill_descriptions() -> Dict[str, str]:
    """Extract name + description from all SKILL.md frontmatter."""
    descs = {}
    if not SKILLS_DIR.exists():
        return descs
    for root, dirs, files in os.walk(SKILLS_DIR):
        if "SKILL.md" not in files:
            continue
        try:
            content = Path(root, "SKILL.md").read_text(encoding="utf-8", errors="replace")
            if not content.startswith("---"):
                continue
            _, fm_part, _ = content.split("---", 2)
            import yaml
            fm = yaml.safe_load(fm_part)
            if isinstance(fm, dict):
                name = fm.get("name", "")
                desc = fm.get("description", "")
                if name:
                    # Use name + description + category as the semantic signature
                    cat = Path(root).parent.name
                    descs[name] = f"{cat}: {name}. {desc}"
        except Exception:
            pass
    return descs


def _load_sbert():
    global _SBERT_MODEL
    if _SBERT_MODEL is None:
        from sentence_transformers import SentenceTransformer
        _SBERT_MODEL = SentenceTransformer("all-MiniLM-L6-v2")
    return _SBERT_MODEL


def _build_embedding_cache():
    """Pre-compute and cache skill embeddings to disk."""
    global _SKILL_EMBEDDINGS
    descs = _get_skill_descriptions()
    
    # Try loading cache
    if os.path.exists(_EMBEDDING_CACHE):
        try:
            with open(_EMBEDDING_CACHE) as f:
                cached = json.load(f)
            # Check if cache is still valid (same skills)
            if set(cached.get("skills", {}).keys()) == set(descs.keys()):
                _SKILL_EMBEDDINGS = cached["skills"]
                return
        except Exception:
            pass
    
    # Build new embeddings
    model = _load_sbert()
    skill_names = list(descs.keys())
    skill_texts = [descs[n] for n in skill_names]
    
    if not skill_texts:
        _SKILL_EMBEDDINGS = {}
        return
    
    embeddings = model.encode(skill_texts, normalize_embeddings=True)
    _SKILL_EMBEDDINGS = {
        name: emb.tolist() for name, emb in zip(skill_names, embeddings)
    }
    
    # Cache to disk
    os.makedirs(os.path.dirname(_EMBEDDING_CACHE), exist_ok=True)
    with open(_EMBEDDING_CACHE, "w") as f:
        json.dump({"skills": _SKILL_EMBEDDINGS}, f)


def _cosine_similarity(a, b):
    import numpy as np
    a = np.array(a)
    b = np.array(b)
    return float(np.dot(a, b))


def semantic_classify(message: str, threshold: float = 0.25) -> List[Dict]:
    """Classify message using embedding similarity against skill descriptions."""
    if _SKILL_EMBEDDINGS is None:
        _build_embedding_cache()
    if not _SKILL_EMBEDDINGS:
        return []
    
    model = _load_sbert()
    msg_emb = model.encode([message], normalize_embeddings=True)[0]
    
    results = []
    for skill_name, skill_emb in _SKILL_EMBEDDINGS.items():
        sim = _cosine_similarity(msg_emb, skill_emb)
        if sim >= threshold:
            results.append({
                "skill": skill_name,
                "relevance": round(sim * 10),  # normalize to match regex scale
                "semantic_score": round(sim, 4),
            })
    
    results.sort(key=lambda r: -r["semantic_score"])
    return results


def auto_load_semantic(message: str, max_skills: int = 5) -> List[str]:
    """Hybrid: uses regex first, then supplements with semantic matching."""
    # 1. Regex-based matching (fast path)
    triggers = load_skill_triggers()
    regex_results = classify(message, triggers)
    selected = set()
    
    # High priority regex matches always win
    for r in regex_results:
        if r["skill"] in HIGH_PRIORITY and r["relevance"] >= 1:
            selected.add(r["skill"])
    
    # Regular regex matches
    for r in regex_results:
        if r["relevance"] >= 1:
            selected.add(r["skill"])
    
    # 2. Semantic matching supplements if we have room
    if len(selected) < max_skills:
        try:
            semantic_results = semantic_classify(message)
            for r in semantic_results:
                if r["skill"] not in selected:
                    selected.add(r["skill"])
                    if len(selected) >= max_skills:
                        break
        except Exception:
            pass  # Semantic matching is optional; fall through to regex-only
    
    return list(selected)[:max_skills]


if __name__ == "__main__":
    # Override the main to use hybrid matching
    import sys
    if len(sys.argv) < 2:
        message = sys.stdin.read().strip()
    else:
        message = " ".join(sys.argv[1:])
    
    if not message:
        print(json.dumps({"skills": [], "message": "No input provided"}))
        sys.exit(0)
    
    selected = auto_load_semantic(message)
    triggers = load_skill_triggers()
    regex_results = classify(message, triggers)
    
    try:
        semantic_results = semantic_classify(message)
    except Exception:
        semantic_results = []
    
    output = {
        "skills": selected,
        "regex_matches": regex_results,
        "semantic_matches": semantic_results,
        "total_matches": len(selected),
        "message_preview": message[:100],
    }
    print(json.dumps(output, indent=2, ensure_ascii=False))
