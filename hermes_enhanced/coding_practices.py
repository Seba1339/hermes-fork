"""
Hermes-Enhanced: Buenas Prácticas de Codificación

Sistema para mantener código limpio, ordenado y seguro de modificar.
Incluye análisis de impacto, validación de arquitectura, y detección de deuda técnica.

Uso:
    from hermes_enhanced.coding_practices import (
        analyze_modification_impact,
        validate_architecture,
        detect_technical_debt,
        safe_modification_workflow,
    )
"""

import ast
import logging
import os
import re
import subprocess
import time
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger("hermes_enhanced.coding_practices")


@dataclass
class FunctionInfo:
    """Información de una función/método."""

    name: str
    line: int
    end_line: int
    complexity: int  # cyclomatic complexity
    lines: int
    has_docstring: bool
    has_tests: bool = False
    dependencies: List[str] = field(default_factory=list)
    called_by: List[str] = field(default_factory=list)


@dataclass
class FileHealth:
    """Salud de un archivo de código."""

    path: str
    lines: int
    functions: int
    classes: int
    complexity_avg: float
    complexity_max: int
    test_coverage_pct: float
    docstring_coverage_pct: float
    issues: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════
# 1. ANÁLISIS DE COMPLEJIDAD (AST puro, sin dependencias externas)
# ═══════════════════════════════════════════════════════════════════════


class ComplexityVisitor(ast.NodeVisitor):
    """Calcula complejidad ciclomática de funciones."""

    def __init__(self):
        self.functions = []
        self._current_func = None
        self._complexity = 0
        self._deps = []
        self._called_by = []

    def visit_FunctionDef(self, node):
        old_func = self._current_func
        old_complexity = self._complexity

        self._current_func = node.name
        self._complexity = 1
        self._deps = []

        # Extraer dependencias (imports dentro de la función)
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                if isinstance(child.func, ast.Attribute):
                    self._deps.append(
                        f"{child.func.value.id}.{child.func.attr}"
                        if isinstance(child.func.value, ast.Name)
                        else str(child.func.attr)
                    )
                elif isinstance(child, ast.Name):
                    self._deps.append(child.id)
            elif isinstance(child, ast.Import):
                for alias in child.names:
                    self._deps.append(alias.name)
            elif isinstance(child, ast.ImportFrom):
                for alias in child.names:
                    self._deps.append(
                        f"{child.module}.{alias.name}" if child.module else alias.name
                    )

        # Visitar hijos para complejidad
        self.generic_visit(node)

        has_doc = isinstance(node.body[0], ast.Expr) and isinstance(
            node.body[0].value, (ast.Constant, ast.Str)
        )

        self.functions.append(
            FunctionInfo(
                name=node.name,
                line=node.lineno,
                end_line=node.end_lineno or node.lineno,
                complexity=self._complexity,
                lines=(node.end_lineno or node.lineno) - node.lineno,
                has_docstring=bool(has_doc),
                dependencies=list(
                    set(d for d in self._deps if d and not d.startswith("_"))
                ),
            )
        )

        self._current_func = old_func
        self._complexity = old_complexity

    def visit_AsyncFunctionDef(self, node):
        self.visit_FunctionDef(node)

    def visit_ClassDef(self, node):
        self.generic_visit(node)

    def _count_branch(self, node):
        self._complexity += 1

    visit_If = _count_branch
    visit_While = _count_branch
    visit_For = _count_branch
    visit_AsyncFor = _count_branch
    visit_ExceptHandler = _count_branch
    visit_With = _count_branch
    visit_AsyncWith = _count_branch
    visit_BoolOp = _count_branch
    visit_Try = _count_branch


def analyze_complexity(file_path: str) -> Tuple[List[FunctionInfo], List[str]]:
    """Analiza la complejidad de un archivo Python."""
    issues = []

    try:
        with open(file_path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=file_path)
    except SyntaxError as e:
        return [], [f"Error de sintaxis: {e}"]
    except Exception as e:
        return [], [f"No se pudo parsear: {e}"]

    visitor = ComplexityVisitor()
    visitor.visit(tree)

    for fn in visitor.functions:
        if fn.complexity > 10:
            issues.append(
                f"{fn.name}:{fn.line} - Complejidad {fn.complexity} (>10, necesita refactor)"
            )
        elif fn.complexity > 5:
            issues.append(
                f"{fn.name}:{fn.line} - Complejidad {fn.complexity} (>5, considerar simplificar)"
            )
        if fn.lines > 50:
            issues.append(
                f"{fn.name}:{fn.line} - {fn.lines} líneas (>50, función muy larga)"
            )
        if not fn.has_docstring:
            issues.append(f"{fn.name}:{fn.line} - Sin docstring")

    return visitor.functions, issues


# ═══════════════════════════════════════════════════════════════════════
# 2. ANÁLISIS DE IMPACTO DE MODIFICACIONES
# ═══════════════════════════════════════════════════════════════════════


def find_function_dependencies(file_path: str, function_name: str) -> Dict:
    """
    Encuentra qué depende de una función específica.
    Busca imports y usos en el proyecto.

    Returns:
        Dict con {function, defined_in, used_by: [], imports: []}
    """
    project_dir = os.path.dirname(os.path.abspath(file_path))
    result = {
        "function": function_name,
        "defined_in": file_path,
        "used_by": [],
        "imports_from_module": [],
    }

    # Buscar usos en el proyecto
    try:
        rc, out, _ = _run_cmd(
            [
                "grep",
                "-rn",
                f"\\b{function_name}\\b",
                project_dir,
                "--include=*.py",
                "-l",
            ],
            timeout=10,
        )
        if rc == 0:
            files = [f for f in out.strip().split("\n") if f and f != file_path]
            result["used_by"] = files[:20]  # Top 20
    except Exception:
        pass

    # Buscar import del módulo
    module_name = Path(file_path).stem
    try:
        rc, out, _ = _run_cmd(
            [
                "grep",
                "-rn",
                f"from.*{module_name}\\b|import.*{module_name}\\b",
                project_dir,
                "--include=*.py",
                "-l",
            ],
            timeout=10,
        )
        if rc == 0:
            files = [f for f in out.strip().split("\n") if f and f != file_path]
            result["imports_from_module"] = files[:20]
    except Exception:
        pass

    return result


def find_import_chain(file_path: str) -> Dict[str, List[str]]:
    """
    Traza la cadena de importaciones de un archivo.
    Retorna {archivo: [lo que importa]}
    """
    imports = defaultdict(list)

    try:
        with open(file_path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=file_path)
    except Exception:
        return {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports[file_path].append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                full = f"{module}.{alias.name}" if module else alias.name
                imports[file_path].append(full)

    return dict(imports)


# ═══════════════════════════════════════════════════════════════════════
# 3. VALIDACIÓN DE ARQUITECTURA
# ═══════════════════════════════════════════════════════════════════════

ARCHITECTURE_RULES = {
    "flask": {
        "patterns": [
            (
                r"app\.route\(",
                "Usar blueprint en vez de app.route para módulos grandes",
            ),
            (r"from\s+app\s+import", "Import cíclico potencial - revisar estructura"),
        ],
        "file_structure": [
            ("routes/", "Separar rutas en módulos"),
            ("models/", "Modelos en directorio separado"),
            ("services/", "Lógica de negocio separada de rutas"),
        ],
    },
    "python": {
        "patterns": [
            (
                r"from\s+\w+\s+import\s+\*",
                "Wildcard import - preferir imports explícitos",
            ),
            (r"except\s*:", "Except sin especificar excepción"),
            (r"\bpass\b", "pass sin implementación - placeholder pendiente"),
            (r"#\s*type:\s*ignore", "type:ignore sin justificación"),
        ],
    },
    "flutter": {
        "patterns": [
            (r"BuildContext", "Asegurar que no se usa BuildContext después de async"),
            (r"setState\(", "Llamadas a setState dentro de callbacks asíncronos"),
            (r"MediaQuery\.of\(context\)", "MediaQuery sin null check en hot reload"),
        ],
    },
}


def validate_architecture(file_path: str, project_type: str = "python") -> List[str]:
    """
    Valida que el código siga las convenciones de arquitectura del proyecto.

    Args:
        file_path: Ruta al archivo
        project_type: python, flask, flutter

    Returns:
        Lista de problemas encontrados
    """
    issues = []
    rules = ARCHITECTURE_RULES.get(project_type, ARCHITECTURE_RULES["python"])

    try:
        content = Path(file_path).read_text(encoding="utf-8")
    except Exception:
        return [f"No se pudo leer {file_path}"]

    for pattern, message in rules["patterns"]:
        matches = re.findall(pattern, content, re.MULTILINE)
        if matches:
            for i, line in enumerate(content.split("\n"), 1):
                if re.search(pattern, line):
                    issues.append(f"L{i}: {message} - {line.strip()[:80]}")
                    break

    # Validar estructura de directorios para Flask
    if project_type == "flask" and "file_structure" in rules:
        fpath = Path(file_path)
        for expected_dir, msg in rules["file_structure"]:
            if not any(expected_dir in str(p) for p in fpath.parents):
                issues.append(
                    f"Estructura: {msg}. Se esperaba directorio {expected_dir}"
                )

    return issues


# ═══════════════════════════════════════════════════════════════════════
# 4. DETECCIÓN DE DEUDA TÉCNICA
# ═══════════════════════════════════════════════════════════════════════

TECH_DEBT_PATTERNS = [
    (
        r"#\s*TODO|#\s*FIXME|#\s*HACK|#\s*XXX|#\s*WORKAROUND",
        "TODO/FIXME/HACK pendiente",
    ),
    (r"#\s*TEMP|#\s*temp|#\s*temporary", "Código temporal"),
    (r"#\s*DEPRECATED|#\s*deprecated|@deprecated", "Código deprecado"),
    (r"#\s*OPTIMIZE|#\s*PERF|#\s*SLOW", "Optimización pendiente"),
    (r"#\s*REFACTOR|#\s*CLEANUP", "Refactor pendiente"),
    (r"#\s*HARDCODED|#\s*hardcode|#\s*CONSTANT", "Valor hardcodeado señalado"),
    (r"#\s*MAGIC|#\s*magic.?number", "Número mágico señalado"),
    (r"#\s*WORKAROUND|#\s*workaround|#\s*HACK", "Workaround señalado"),
    (r"#\s*REVIEW|#\s*review|#\s*CHECK", "Pendiente de revisión"),
    (r"#\s*bug|#\s*BUG|#\s*ISSUE", "Bug conocido señalado"),
]

DEBT_SEVERITY = {
    "BUG": 10,
    "FIXME": 8,
    "HACK": 7,
    "WORKAROUND": 6,
    "DEPRECATED": 5,
    "REFACTOR": 4,
    "TODO": 3,
    "OPTIMIZE": 2,
    "TEMP": 2,
    "REVIEW": 1,
    "MAGIC": 1,
}

COMMENT_PATTERN = re.compile(
    r"#\s*(TODO|FIXME|HACK|XXX|BUG|TEMP|DEPRECATED|OPTIMIZE|REFACTOR|WORKAROUND|REVIEW|MAGIC|HARDCODED)\s*[:=-]?\s*(.*)",
    re.IGNORECASE,
)


def detect_technical_debt(file_path: str) -> List[Dict]:
    """
    Escanea deuda técnica en un archivo.

    Returns:
        Lista de {line, type, message, severity, snippet}
    """
    issues = []

    try:
        content = Path(file_path).read_text(encoding="utf-8")
    except Exception:
        return []

    for i, line in enumerate(content.split("\n"), 1):
        match = COMMENT_PATTERN.search(line)
        if match:
            debt_type = match.group(1).upper()
            msg = match.group(2).strip()
            severity = DEBT_SEVERITY.get(debt_type, 3)
            issues.append({
                "line": i,
                "type": debt_type,
                "message": msg,
                "severity": severity,
                "snippet": line.strip()[:100],
            })

    return sorted(issues, key=lambda x: x["severity"], reverse=True)


# ═══════════════════════════════════════════════════════════════════════
# 5. ANÁLISIS DE COBERTURA DE TESTS
# ═══════════════════════════════════════════════════════════════════════


def find_test_gaps(file_path: str, project_dir: str = "") -> List[str]:
    """
    Encuentra funciones/classes sin tests.

    Returns:
        Lista de funciones sin test
    """
    functions, _ = analyze_complexity(file_path)
    gaps = []

    # Buscar archivos de test
    test_dir = os.path.join(project_dir or os.path.dirname(file_path), "tests")
    test_files = (
        list(Path(test_dir).glob("test_*.py")) if os.path.isdir(test_dir) else []
    )
    test_files += list(Path(project_dir or ".").glob("test_*.py"))

    # Extraer nombres de funciones testeadas
    tested_funcs = set()
    for tf in test_files:
        try:
            tree = ast.parse(tf.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    # Nombre de función bajo test: test_X_testea_Y
                    tested_funcs.add(node.name)
        except Exception:
            pass

    for fn in functions:
        # Si no parece tener test
        has_test = False
        for tf in tested_funcs:
            if fn.name.lower() in tf.lower():
                has_test = True
                break
        if not has_test and fn.name != "__init__":
            gaps.append(fn.name)

    return gaps


# ═══════════════════════════════════════════════════════════════════════
# 6. FLUJO DE MODIFICACIÓN SEGURA
# ═══════════════════════════════════════════════════════════════════════


def _run_cmd(cmd: List[str], timeout: int = 30) -> Tuple[int, str, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except Exception as e:
        return -1, "", str(e)


def safe_modification_workflow(file_path: str, project_dir: str = "") -> Dict:
    """
    Workflow completo para modificar código de forma segura.
    Analiza el estado actual antes de cualquier cambio.

    Returns:
        Dict con {health, impact, debt, gaps, issues}
    """
    report = {
        "file": file_path,
        "timestamp": time.time(),
        "health": None,
        "impact": {},
        "debt": [],
        "test_gaps": [],
        "arch_issues": [],
        "recommendations": [],
        "safe_to_modify": False,
    }

    if not Path(file_path).exists():
        report["recommendations"].append("Archivo no encontrado")
        return report

    # 1. Salud del archivo
    functions, issues = analyze_complexity(file_path)
    report["issues"] = issues

    if functions:
        complexities = [f.complexity for f in functions]
        report["health"] = FileHealth(
            path=file_path,
            lines=sum(f.lines for f in functions),
            functions=len(functions),
            classes=len([f for f in functions if f.name[0].isupper()]),
            complexity_avg=sum(complexities) / len(complexities),
            complexity_max=max(complexities),
            test_coverage_pct=0,
            docstring_coverage_pct=sum(1 for f in functions if f.has_docstring)
            / len(functions)
            * 100,
        )

    # 2. Deuda técnica
    report["debt"] = detect_technical_debt(file_path)

    # 3. Tests faltantes
    report["test_gaps"] = find_test_gaps(file_path, project_dir)

    # 4. Arquitectura
    report["arch_issues"] = validate_architecture(file_path, "python")

    # 5. Recomendaciones
    if issues:
        report["recommendations"].append(
            "Corregir issues de calidad antes de modificar"
        )
    if report["debt"]:
        high_debt = [d for d in report["debt"] if d["severity"] >= 7]
        if high_debt:
            report["recommendations"].append(
                f"Resolver {len(high_debt)} deudas técnicas severas"
            )
    if report["test_gaps"]:
        report["recommendations"].append(
            f"Considerar agregar tests para: {', '.join(report['test_gaps'][:5])}"
        )
    if report["health"] and report["health"].docstring_coverage_pct < 50:
        report["recommendations"].append(
            "Agregar docstrings a funciones sin documentar"
        )

    report["safe_to_modify"] = (
        len(issues) == 0 and len([d for d in report["debt"] if d["severity"] >= 7]) == 0
    )

    return report


def format_modification_report(report: Dict) -> str:
    """Formatea el reporte de modificación segura para el usuario."""
    lines = []
    lines.append(f"📋 Análisis pre-modificación: {report['file']}")
    lines.append("")

    if report["health"]:
        h = report["health"]
        lines.append(f"📊 Salud del archivo:")
        lines.append(f"   {h.functions} funciones, {h.classes} clases")
        lines.append(
            f"   Complejidad media: {h.complexity_avg:.1f} (máx: {h.complexity_max})"
        )
        lines.append(f"   Docstrings: {h.docstring_coverage_pct:.0f}%")
        lines.append("")

    if report["issues"]:
        lines.append(f"⚠️  Issues ({len(report['issues'])}):")
        for iss in report["issues"][:8]:
            lines.append(f"   • {iss}")

    if report["debt"]:
        sev = {"CRITICAL": [], "HIGH": [], "MEDIUM": [], "LOW": []}
        for d in report["debt"]:
            if d["severity"] >= 8:
                sev["CRITICAL"].append(d)
            elif d["severity"] >= 5:
                sev["HIGH"].append(d)
            elif d["severity"] >= 3:
                sev["MEDIUM"].append(d)
            else:
                sev["LOW"].append(d)

        lines.append(f"\n🔧 Deuda técnica:")
        for level, items in sev.items():
            if items:
                lines.append(f"   {level}: {len(items)}")
                for d in items[:3]:
                    lines.append(
                        f"     L{d['line']}: [{d['type']}] {d['message'][:80]}"
                    )

    if report["test_gaps"]:
        lines.append(f"\n🧪 Funciones sin test ({len(report['test_gaps'])}):")
        for g in report["test_gaps"][:5]:
            lines.append(f"   • {g}")

    if report["recommendations"]:
        lines.append(f"\n💡 Recomendaciones:")
        for r in report["recommendations"]:
            lines.append(f"   • {r}")

    lines.append(
        f"\n{'✅ Seguro de modificar' if report['safe_to_modify'] else '⚠️  Revisar antes de modificar'}"
    )

    return "\n".join(lines)
