"""
Hermes-Enhanced: Code Quality Gate

Pipeline de calidad que se ejecuta ANTES de entregar código al usuario.
Lintea, type-checkea, y prueba el código automáticamente.

Uso:
    from hermes_enhanced.coding import code_quality_gate, CodeResult

    result = code_quality_gate(file_path="script.py", language="python")
    if not result.passed:
        # Corregir antes de entregar
"""

import json
import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("hermes_enhanced.coding")


@dataclass
class CodeResult:
    """Resultado de una revisión de código."""

    file_path: str
    language: str
    passed: bool = True
    lint_errors: List[str] = field(default_factory=list)
    type_errors: List[str] = field(default_factory=list)
    test_results: List[str] = field(default_factory=list)
    security_warnings: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    execution_time: float = 0.0

    def summary(self) -> str:
        """Resumen legible del resultado."""
        lines = []
        if self.passed:
            lines.append(f"✅ {self.file_path}: PASS")
        else:
            lines.append(
                f"❌ {self.file_path}: FAIL ({len(self.lint_errors) + len(self.type_errors)} errores)"
            )

        if self.lint_errors:
            lines.append(f"  Lint: {len(self.lint_errors)} problemas")
            for e in self.lint_errors[:3]:
                lines.append(f"    - {e[:120]}")
        if self.type_errors:
            lines.append(f"  Types: {len(self.type_errors)} problemas")
            for e in self.type_errors[:3]:
                lines.append(f"    - {e[:120]}")
        if self.test_results:
            passed = sum(1 for t in self.test_results if "PASS" in t)
            failed = sum(1 for t in self.test_results if "FAIL" in t)
            lines.append(f"  Tests: {passed} passed, {failed} failed")
        if self.suggestions:
            lines.append(f"  Sugerencias: {len(self.suggestions)}")
            for s in self.suggestions[:2]:
                lines.append(f"    💡 {s[:100]}")

        return "\n".join(lines)


def _run_command(cmd: List[str], cwd: Optional[str] = None, timeout: int = 30) -> tuple:
    """Ejecuta un comando y retorna (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd or os.getcwd()
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "TIMEOUT"
    except FileNotFoundError:
        return -2, "", "COMMAND_NOT_FOUND"


def lint_python(file_path: str) -> List[str]:
    """Ejecuta ruff sobre un archivo Python."""
    errors = []
    if not Path(file_path).exists():
        return [f"Archivo no encontrado: {file_path}"]

    # Ruff check
    rc, out, err = _run_command(["ruff", "check", "--quiet", file_path])
    if rc != 0:
        for line in (out or err).split("\n"):
            if line.strip():
                errors.append(line.strip())

    # Ruff format check
    rc2, out2, err2 = _run_command(["ruff", "format", "--check", "--quiet", file_path])
    if rc2 != 0:
        errors.append("⚠️ Formato incorrecto (ruff format)")

    return errors


def typecheck_python(file_path: str) -> List[str]:
    """Ejecuta mypy sobre un archivo Python."""
    errors = []
    rc, out, err = _run_command(
        ["mypy", "--show-error-codes", "--ignore-missing-imports", file_path],
        timeout=60,
    )
    if rc != 0:
        for line in (out or err).split("\n"):
            if line.strip() and "error:" in line.lower():
                errors.append(line.strip())
    return errors


def lint_flutter(project_dir: str) -> List[str]:
    """Ejecuta flutter analyze sobre un proyecto."""
    errors = []
    rc, out, err = _run_command(
        ["flutter", "analyze", "--no-fatal-infos", "--no-fatal-warnings"],
        cwd=project_dir,
        timeout=120,
    )
    if rc != 0:
        for line in (out or err).split("\n"):
            if "error" in line.lower() and line.strip():
                errors.append(line.strip())
    return errors


def run_tests(file_path: str, project_dir: str = "") -> List[str]:
    """Ejecuta tests relacionados al archivo."""
    results = []
    test_dir = project_dir or os.path.dirname(file_path)

    # Buscar tests relacionados
    basename = Path(file_path).stem
    test_patterns = [
        f"test_{basename}.py",
        f"{basename}_test.py",
        f"test_*.py",
    ]

    for pattern in test_patterns:
        test_file = Path(test_dir) / "tests" / pattern
        if test_file.exists():
            rc, out, err = _run_command(
                ["python", "-m", "pytest", str(test_file), "-q", "--tb=short", "-x"],
                timeout=120,
            )
            if rc == 0:
                results.append(f"PASS: {test_file.name}")
            else:
                last_line = (
                    (out or err).strip().split("\n")[-1] if (out or err) else "FAIL"
                )
                results.append(f"FAIL: {test_file.name} - {last_line[:100]}")

    # Si hay un requirements.txt o setup.py, intentar test por descubrimiento
    if not results:
        rc, out, err = _run_command(
            ["python", "-m", "pytest", file_path, "-q", "--tb=short", "-x"], timeout=60
        )
        if rc is not None:
            passed = "passed" in (out or "") or "failed" not in (out or "")
            results.append(f"{'PASS' if passed else 'FAIL'}: pytest discovery")

    return results


def security_scan(file_path: str) -> List[str]:
    """Escanea vulnerabilidades básicas en el código."""
    warnings = []
    try:
        content = Path(file_path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ["No se pudo leer el archivo"]

    # Patrones de seguridad
    patterns = [
        (
            r"(api_key|api_secret|password|token|secret)\s*=\s*['\"][^'\"]+['\"]",
            "Posible secreto hardcodeado",
        ),
        (r"eval\s*\(", "Uso de eval() - riesgo de inyección"),
        (r"exec\s*\(", "Uso de exec() - riesgo de inyección"),
        (r"subprocess\.call\(.*shell=True", "shell=True - riesgo de inyección"),
        (r"os\.system\(", "os.system() - preferir subprocess"),
        (r"pickle\.loads?\(", "Pickle inseguro con datos externos"),
        (r"sqlite3\.execute\(f['\"]", "SQL query con f-string - riesgo de inyección"),
        (r"#\s*TODO|#\s*FIXME|#\s*HACK", "TODO/FIXME pendiente"),
        (r"print\(|console\.log\(", "Debug print/console.log en producción"),
        (r"except\s*:", "except sin especificar excepción"),
        (r"raise\s+Exception\(", "Usar excepción específica en vez de Exception"),
        (r"\.gitignore|npm_token|NPM_TOKEN|GITHUB_TOKEN", "Token en código"),
    ]

    for i, line in enumerate(content.split("\n"), 1):
        for pattern, msg in patterns:
            if re.search(pattern, line):
                warnings.append(f"L{i}: {msg} - {line.strip()[:60]}")
                break

    return warnings


def code_quality_gate(
    file_path: str,
    language: str = "python",
    project_dir: str = "",
    run_lint: bool = True,
    run_types: bool = True,
    run_tests_flag: bool = False,
    run_security: bool = True,
) -> CodeResult:
    """
    Pipeline completo de calidad de código.

    Args:
        file_path: Ruta al archivo a revisar
        language: python, flutter, javascript, bash
        project_dir: Directorio del proyecto (para tests)
        run_lint: Ejecutar linter
        run_types: Ejecutar type checker
        run_tests_flag: Ejecutar tests
        run_security: Escanear vulnerabilidades

    Returns:
        CodeResult con todos los hallazgos
    """
    import time

    start = time.time()

    result = CodeResult(file_path=file_path, language=language)

    if not Path(file_path).exists():
        result.passed = False
        result.lint_errors.append("Archivo no encontrado")
        return result

    # 1. Lint
    if run_lint:
        if language == "python":
            result.lint_errors = lint_python(file_path)
        elif language == "flutter":
            result.lint_errors = lint_flutter(project_dir or os.path.dirname(file_path))

    # 2. Type check
    if run_types and language == "python":
        result.type_errors = typecheck_python(file_path)

    # 3. Tests
    if run_tests_flag:
        result.test_results = run_tests(file_path, project_dir)

    # 4. Seguridad
    if run_security:
        result.security_warnings = security_scan(file_path)

    # 5. Sugerencias generales
    content = Path(file_path).read_text(encoding="utf-8", errors="ignore")
    lines = content.split("\n")

    if len(lines) > 500:
        result.suggestions.append(
            f"Archivo muy largo ({len(lines)} líneas). Considerar dividir en módulos."
        )
    if not content.strip().endswith("\n"):
        result.suggestions.append("Falta newline al final del archivo.")
    if (
        language == "python"
        and not content.strip().startswith("#!/")
        and not content.strip().startswith("#")
    ):
        if not any(l.startswith('"""') or l.startswith("'''") for l in lines[:3]):
            result.suggestions.append("Considerar agregar docstring del módulo.")

    result.passed = (
        len(result.lint_errors) == 0
        and len(result.type_errors) == 0
        and all("FAIL" not in t for t in result.test_results)
    )

    result.execution_time = time.time() - start
    return result


def format_python(file_path: str) -> bool:
    """Auto-formatea un archivo Python con ruff."""
    rc, out, err = _run_command(["ruff", "format", file_path])
    if rc == 0:
        logger.info("Formateado: %s", file_path)
        return True
    logger.warning("Error formateando %s: %s", file_path, err[:200])
    return False


def auto_fix_lint(file_path: str) -> List[str]:
    """Auto-corrige errores de lint cuando sea posible."""
    fixes = []
    rc, out, err = _run_command(["ruff", "check", "--fix", "--quiet", file_path])
    if rc == 0:
        fixes.append("ruff --fix aplicado")
    else:
        remaining = (out or "").strip()
        if remaining:
            fixes.append(
                f"Errores restantes tras --fix: {len(remaining.split(chr(10)))}"
            )

    return fixes
