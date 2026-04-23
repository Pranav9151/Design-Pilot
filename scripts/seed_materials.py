"""
Seed the `materials` table from app/data/materials.py (POC dataset).

Usage:
    python -m scripts.seed_materials

Idempotent: uses INSERT ... ON CONFLICT (slug) DO UPDATE so it can be
re-run safely. This preserves stable UUIDs across seeds.

NOTE on scale: v1.0 seeds 12 verified materials. The expansion to 200
is deliberately a separate, manually-reviewed sourcing effort (not
AI-generated fill-in, not scraped). Each new row requires:
  - Handbook or ASM reference on every numeric property
  - A human engineer's signoff in the review PR
  - A unit-tested roundtrip (seed -> query -> compare)
This is tracked as a Week 3+ task; do not lower the bar to "seed faster."
"""
from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session_factory
from app.data.materials import MATERIALS


async def seed() -> int:
    """Insert or update every material from the POC module. Returns count."""
    factory = get_session_factory()
    count = 0

    async with factory() as session:  # type: AsyncSession
        for slug, mat in MATERIALS.items():
            params = {
                "slug": slug,
                "name": mat.name,
                "grade": mat.grade,
                "category": mat.category,
                "youngs_modulus_mpa": mat.youngs_modulus_mpa,
                "yield_strength_mpa": mat.yield_strength_mpa,
                "ultimate_strength_mpa": mat.ultimate_strength_mpa,
                "density_kg_m3": mat.density_kg_m3,
                "poissons_ratio": mat.poissons_ratio,
                "elongation_percent": mat.elongation_percent,
                "cte": mat.cte,
                "thermal_conductivity": mat.thermal_conductivity,
                "max_service_temp_c": mat.max_service_temp_c,
                "machinability_rating": mat.machinability_rating,
                "cost_per_kg_usd": mat.cost_per_kg_usd,
                "source": mat.source,
            }

            await session.execute(
                text("""
                    INSERT INTO materials (
                        slug, name, grade, category,
                        youngs_modulus_mpa, yield_strength_mpa, ultimate_strength_mpa,
                        density_kg_m3, poissons_ratio, elongation_percent,
                        cte, thermal_conductivity, max_service_temp_c,
                        machinability_rating, cost_per_kg_usd, source
                    ) VALUES (
                        :slug, :name, :grade, :category,
                        :youngs_modulus_mpa, :yield_strength_mpa, :ultimate_strength_mpa,
                        :density_kg_m3, :poissons_ratio, :elongation_percent,
                        :cte, :thermal_conductivity, :max_service_temp_c,
                        :machinability_rating, :cost_per_kg_usd, :source
                    )
                    ON CONFLICT (slug) DO UPDATE SET
                        name = EXCLUDED.name,
                        grade = EXCLUDED.grade,
                        category = EXCLUDED.category,
                        youngs_modulus_mpa = EXCLUDED.youngs_modulus_mpa,
                        yield_strength_mpa = EXCLUDED.yield_strength_mpa,
                        ultimate_strength_mpa = EXCLUDED.ultimate_strength_mpa,
                        density_kg_m3 = EXCLUDED.density_kg_m3,
                        poissons_ratio = EXCLUDED.poissons_ratio,
                        elongation_percent = EXCLUDED.elongation_percent,
                        cte = EXCLUDED.cte,
                        thermal_conductivity = EXCLUDED.thermal_conductivity,
                        max_service_temp_c = EXCLUDED.max_service_temp_c,
                        machinability_rating = EXCLUDED.machinability_rating,
                        cost_per_kg_usd = EXCLUDED.cost_per_kg_usd,
                        source = EXCLUDED.source
                """),
                params,
            )
            count += 1

        await session.commit()

    return count


async def main() -> None:
    n = await seed()
    print(f"seeded {n} materials")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"seed failed: {exc}", file=sys.stderr)
        sys.exit(1)
