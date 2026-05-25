"""
Hermes-Enhanced: Sandbox Docker para probar código
Ejecuta código en un contenedor limpio y reporta resultado.
"""

import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger("hermes_enhanced.sandbox")

DOCKER_IMAGE = "python:3.11-slim"
SANDBOX_TIMEOUT = 30  # segundos


def test_code(
    code: str, language: str = "python", timeout: int = SANDBOX_TIMEOUT
) -> dict:
    """
    Ejecuta código en sandbox Docker y retorna resultado.

    Args:
        code: Código a ejecutar
        language: python, bash
        timeout: Timeout máximo

    Returns:
        {"passed": bool, "output": str, "error": str, "time": float}
    """
    start = time.time()

    with tempfile.TemporaryDirectory() as tmpdir:
        if language == "python":
            script_path = os.path.join(tmpdir, "test_script.py")
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(code)
            cmd = [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",  # Sin red por seguridad
                "-v",
                f"{script_path}:/test.py:ro",
                DOCKER_IMAGE,
                "python",
                "/test.py",
            ]
        elif language == "bash":
            script_path = os.path.join(tmpdir, "test_script.sh")
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(code)
            cmd = [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "-v",
                f"{script_path}:/test.sh:ro",
                DOCKER_IMAGE,
                "bash",
                "/test.sh",
            ]
        else:
            return {
                "passed": False,
                "output": "",
                "error": f"Idioma no soportado: {language}",
                "time": 0,
            }

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout
            )
            elapsed = time.time() - start
            if result.returncode == 0:
                return {
                    "passed": True,
                    "output": result.stdout.strip(),
                    "error": "",
                    "time": round(elapsed, 2),
                }
            else:
                return {
                    "passed": False,
                    "output": result.stdout.strip(),
                    "error": result.stderr.strip() or f"Exit code: {result.returncode}",
                    "time": round(elapsed, 2),
                }
        except subprocess.TimeoutExpired:
            return {
                "passed": False,
                "output": "",
                "error": f"Timeout ({timeout}s)",
                "time": timeout,
            }
        except FileNotFoundError:
            return {
                "passed": False,
                "output": "",
                "error": "Docker no disponible",
                "time": 0,
            }


def test_file(file_path: str, language: str = "python") -> dict:
    """Ejecuta un archivo en sandbox Docker."""
    if not Path(file_path).exists():
        return {
            "passed": False,
            "output": "",
            "error": "Archivo no encontrado",
            "time": 0,
        }
    code = Path(file_path).read_text(encoding="utf-8")
    return test_code(code, language)
