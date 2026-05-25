#!/usr/bin/env python3
"""
Hermes-Enhanced Auto-Review Runner
Ejecutado por cronjob cada 24h para auto-crecimiento.
"""
import json
import sys
import os

# Asegurar que el fork está en el path
FORK_DIR = os.path.expanduser("~/hermes-fork")
if FORK_DIR not in sys.path:
    sys.path.insert(0, FORK_DIR)

os.environ.setdefault("HERMES_HOME", os.path.expanduser("~/.hermes-enhanced"))

from hermes_enhanced.growth import run_self_review, get_growth_stats

def main():
    print("=" * 60)
    print("Hermes-Enhanced: Auto-Review Diario")
    print("=" * 60)

    # Revisión completa (full=True consolida memoria y genera parches)
    report = run_self_review(full=True)

    print(f"\n📊 Estadísticas de crecimiento:")
    stats = report["growth_stats"]
    print(f"  Skills: {stats['total_skills']}")
    print(f"  Sesiones totales: {stats['total_sessions']}")
    print(f"  Entradas de memoria: {stats['total_memory_entries']}")
    print(f"  Correcciones recientes: {stats['last_correction_incidents']}")
    if stats["skills_by_category"]:
        print(f"  Skills por categoría: {stats['skills_by_category']}")

    print(f"\n🔍 Consolidación de memoria:")
    mc = report["memory_consolidation"]
    print(f"  Entradas revisadas: {mc['entries_checked']}")
    print(f"  Duplicados encontrados: {mc['duplicates_found']}")
    print(f"  Fusionados: {mc['merged']}")

    if report["correction_incidents"]:
        print(f"\n⚠️  Incidentes de corrección detectados: {len(report['correction_incidents'])}")
        for inc in report["correction_incidents"][:3]:
            print(f"  - [{inc['type']}] {inc['preview'][:80]}...")
    else:
        print(f"\n✅ Sin correcciones recientes detectadas")

    if report["suggested_patches"]:
        print(f"\n🔧 Parches sugeridos: {len(report['suggested_patches'])}")
        for patch in report["suggested_patches"][:3]:
            print(f"  - {patch['skill']} ({patch['priority']}): {patch['reason'][:60]}")

    if report["actions_taken"]:
        print(f"\n✅ Acciones ejecutadas:")
        for action in report["actions_taken"]:
            print(f"  ✓ {action}")

    print(f"\n{'=' * 60}")
    print(f"Auto-Review completado: {report['timestamp']}")
    print(f"{'=' * 60}")

    # Guardar reporte JSON
    report_path = os.path.expanduser("~/.hermes-enhanced/data/auto_review_log.json")
    try:
        existing = []
        if os.path.exists(report_path):
            with open(report_path) as f:
                existing = json.load(f)
        existing.append(report)
        # Mantener solo últimos 30 reportes
        existing = existing[-30:]
        with open(report_path, "w") as f:
            json.dump(existing, f, indent=2, default=str)
    except Exception as e:
        print(f"  (no se pudo guardar reporte: {e})")

    return 0

if __name__ == "__main__":
    sys.exit(main())
