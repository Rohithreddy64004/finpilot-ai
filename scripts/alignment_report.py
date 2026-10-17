"""Refresh and print provider-confirmed alignment/deployment state by domain."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.alignment_jobs import AlignmentJobRepository, MongoAlignmentJobRepository
from backend.app.alignment_workflow import refresh_alignment_job
from backend.app.config import get_settings
from backend.app.nugen.alignment_service import NugenAlignmentService
from backend.app.nugen.client import NugenClient


DOMAINS = ("tax", "investment", "insurance")


async def refreshed_domain_report(repository: AlignmentJobRepository, service: NugenAlignmentService) -> list[dict[str, object]]:
    """Read latest tracked jobs and refresh state; this function never submits jobs."""

    report: list[dict[str, object]] = []
    for domain in DOMAINS:
        records = await repository.list_by_domain(domain, limit=1)
        if not records:
            report.append(
                {
                    "domain": domain,
                    "alignment_id": None,
                    "alignment_status": "unaligned",
                    "verification_state": "UNVERIFIED",
                    "model_id": None,
                    "provider_status": None,
                    "deployment_status": None,
                    "inference_ready": False,
                    "note": "No tracked alignment job exists.",
                }
            )
            continue
        record = await refresh_alignment_job(records[0], service, repository)
        report.append(
            {
                "domain": domain,
                "alignment_id": record.alignment_id,
                "alignment_status": record.alignment_status,
                "verification_state": record.verification_state,
                "model_id": record.model_id,
                "provider_status": record.provider_status,
                "deployment_status": record.deployment_status,
                "inference_ready": record.inference_ready,
                "note": record.status_note,
            }
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-refresh",
        action="store_true",
        help="Read local tracking only; do not check current Nugen status",
    )
    args = parser.parse_args()
    return asyncio.run(_main_async(args.no_refresh))


async def _main_async(no_refresh: bool) -> int:
    repository = MongoAlignmentJobRepository(get_settings())
    try:
        if no_refresh:
            rows = []
            for domain in DOMAINS:
                records = await repository.list_by_domain(domain, limit=1)
                if not records:
                    rows.append((domain, None, "unaligned", "UNVERIFIED", None, None, False))
                    continue
                record = records[0]
                rows.append(
                    (
                        domain,
                        record.alignment_id,
                        record.alignment_status,
                        record.verification_state,
                        record.model_id,
                        record.deployment_status,
                        record.inference_ready,
                    )
                )
        else:
            settings = get_settings()
            async with NugenClient(settings) as client:
                report = await refreshed_domain_report(repository, NugenAlignmentService(client))
            rows = [
                (
                    row["domain"],
                    row["alignment_id"],
                    row["alignment_status"],
                    row["verification_state"],
                    row["model_id"],
                    row["deployment_status"],
                    row["inference_ready"],
                )
                for row in report
            ]
    except Exception as exc:
        # Do not print provider or Mongo exception text, which can include connection data.
        print(f"Alignment report could not be completed ({type(exc).__name__}).")
        return 1
    finally:
        await repository.close()

    print("domain | alignment_id | alignment_status | verification | model_id | deployment | inference_ready")
    for row in rows:
        print(" | ".join(str(value) if value is not None else "-" for value in row))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
