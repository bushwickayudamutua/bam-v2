"""Command-line entry points (spec section 5 cron jobs + operational tasks).

Cron mapping per the contract: hourly ``bam website-data`` (the spec's
``UpdateWebsiteRequestData`` job); daily ``bam expire && bam scrub-pii``.
The remaining subcommands cover intake processing (spec 6.1), the outreach
text blast (spec 6.2), the end-of-distro no-show pass (spec 6.3), and
running the API server. Every non-serve command opens its own session and
prints a JSON report to stdout.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime

from sqlmodel import Session

from bam.config import settings
from bam.db import get_engine, init_db
from bam.services.checkin import process_no_shows
from bam.services.expiration import expire_stale_requests
from bam.services.intake import process_pending
from bam.services.metrics import update_website_request_data
from bam.services.outreach import build_outreach_list, send_text_blast
from bam.services.privacy import scrub_expired_pii
from bam.sms.base import get_provider
from bam.sms.console import ConsoleSMSProvider


def _print_json(data: object) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def _parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Invalid date {value!r}; expected YYYY-MM-DD"
        ) from exc


def cmd_serve(args: argparse.Namespace) -> None:
    """Run the FastAPI app (startup calls init_db itself)."""
    import uvicorn

    uvicorn.run("bam.api.main:app", host=args.host, port=args.port, reload=args.reload)


def cmd_init_db(args: argparse.Namespace) -> None:
    init_db()
    _print_json({"initialized": True, "database_url": settings.database_url})


def cmd_process_intake(args: argparse.Namespace) -> None:
    init_db()
    with Session(get_engine()) as session:
        results = process_pending(session)
    _print_json([result.model_dump(mode="json") for result in results])


def cmd_expire(args: argparse.Namespace) -> None:
    init_db()
    with Session(get_engine()) as session:
        report = expire_stale_requests(session)
    _print_json(report.model_dump(mode="json"))


def cmd_website_data(args: argparse.Namespace) -> None:
    init_db()
    with Session(get_engine()) as session:
        data = update_website_request_data(session)
    _print_json(data)


def cmd_scrub_pii(args: argparse.Namespace) -> None:
    init_db()
    with Session(get_engine()) as session:
        report = scrub_expired_pii(session)
    _print_json(report.model_dump(mode="json"))


def cmd_no_shows(args: argparse.Namespace) -> None:
    init_db()
    with Session(get_engine()) as session:
        report = process_no_shows(session, args.date)
    _print_json(report.model_dump(mode="json"))


def _run_job(fn) -> None:
    """Open a session, run a parity job, print its JSON report."""
    from pydantic import BaseModel

    init_db()
    with Session(get_engine()) as session:
        report = fn(session)
    _print_json(report.model_dump(mode="json") if isinstance(report, BaseModel) else report)


def cmd_consolidate(args: argparse.Namespace) -> None:
    from bam.services.consolidate import consolidate_requests

    _run_job(lambda s: consolidate_requests(s, household_id=args.household_id))


def cmd_dedupe(args: argparse.Namespace) -> None:
    from bam.services.dedupe import dedupe_households

    _run_job(dedupe_households)


def cmd_count_closed(args: argparse.Namespace) -> None:
    from bam.services.count_closed import count_closed_requests

    _run_job(lambda s: count_closed_requests(s, delete=args.delete or None))


def cmd_mailjet(args: argparse.Namespace) -> None:
    from bam.services.mailjet import sync_mailjet_lists

    _run_job(sync_mailjet_lists)


def cmd_snapshot(args: argparse.Namespace) -> None:
    from bam.services.snapshot import snapshot_data

    _run_job(snapshot_data)


def cmd_analyze(args: argparse.Namespace) -> None:
    from bam.services.analytics import analyze_fulfilled_requests

    _run_job(analyze_fulfilled_requests)


def cmd_import_airtable(args: argparse.Namespace) -> None:
    """Migrate the production Airtable V2 base into the local database.

    Pulls a raw snapshot first (auditable, re-runnable offline via
    ``--from-snapshot``), then imports it. Idempotent: re-runs update rows
    in place by their Airtable record id.
    """
    from bam.airtable import AirtableClient, SnapshotSource, dump_snapshot
    from bam.services.airtable_import import import_base

    init_db()
    if args.from_snapshot:
        source = SnapshotSource(args.from_snapshot)
        snapshot_counts = None
    else:
        client = AirtableClient(
            token=settings.airtable_token, base_id=args.base_id
        )
        snapshot_counts = dump_snapshot(client, args.snapshot_dir)
        source = SnapshotSource(args.snapshot_dir)
    with Session(get_engine()) as session:
        report = import_base(session, source)
    output = {"import": report.model_dump(mode="json")}
    if snapshot_counts is not None:
        output["snapshot"] = {"dir": args.snapshot_dir, "records": snapshot_counts}
    _print_json(output)


def cmd_blast(args: argparse.Namespace) -> None:
    """Build the outreach list and send the text blast (spec 6.2).

    ``--dry-run`` previews the blast: the console provider is used so no
    real messages go out, and nothing is persisted (in particular
    ``last_texted`` stays untouched so the real send's recency filters
    still match).
    """
    init_db()
    provider = ConsoleSMSProvider() if args.dry_run else get_provider(settings)
    with Session(get_engine()) as session:
        candidates = build_outreach_list(
            session,
            request_types=args.request_types,
            languages=args.languages,
            exclude_texted_within_days=args.exclude_texted_days,
            exclude_attended_within_days=args.exclude_attended_days,
            limit=args.limit,
        )
        report = send_text_blast(
            session,
            [candidate.household_id for candidate in candidates],
            args.template,
            provider,
            max_messages=args.max_messages,
            dry_run=args.dry_run,
        )
    _print_json(
        {
            "dry_run": args.dry_run,
            "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
            "blast": report.model_dump(mode="json"),
        }
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bam", description="BAM Mutual Aid System V2 command line."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="Run the API server with uvicorn.")
    serve.add_argument("--host", default="127.0.0.1", help="Bind address.")
    serve.add_argument("--port", type=int, default=8000, help="Bind port.")
    serve.add_argument("--reload", action="store_true", help="Enable auto-reload.")
    serve.set_defaults(func=cmd_serve)

    init_db_cmd = subparsers.add_parser("init-db", help="Create all database tables.")
    init_db_cmd.set_defaults(func=cmd_init_db)

    process_intake = subparsers.add_parser(
        "process-intake", help="Process all unprocessed form submissions (spec 6.1)."
    )
    process_intake.set_defaults(func=cmd_process_intake)

    expire = subparsers.add_parser(
        "expire", help="Time out stale open requests (daily cron, spec 2/4)."
    )
    expire.set_defaults(func=cmd_expire)

    website_data = subparsers.add_parser(
        "website-data",
        help="Write open request counts to the website JSON (hourly cron, spec 5).",
    )
    website_data.set_defaults(func=cmd_website_data)

    scrub_pii = subparsers.add_parser(
        "scrub-pii", help="Scrub expired PII (daily cron, privacy goal)."
    )
    scrub_pii.set_defaults(func=cmd_scrub_pii)

    no_shows = subparsers.add_parser(
        "no-shows", help="Process no-shows for a distribution date (spec 6.3)."
    )
    no_shows.add_argument(
        "--date", type=_parse_date, required=True, help="Distribution date (YYYY-MM-DD)."
    )
    no_shows.set_defaults(func=cmd_no_shows)

    # Parity automations / maintenance jobs.
    consolidate = subparsers.add_parser(
        "consolidate", help="Consolidate duplicate requests (consolidate-requests.js)."
    )
    consolidate.add_argument(
        "--household-id", type=int, default=None, help="Limit to one household."
    )
    consolidate.set_defaults(func=cmd_consolidate)

    dedupe = subparsers.add_parser(
        "dedupe-households", help="Merge same-phone duplicate households (DedupeAirtableViews)."
    )
    dedupe.set_defaults(func=cmd_dedupe)

    count_closed = subparsers.add_parser(
        "count-closed",
        help="Tally delivered requests into fulfilled counts (count-closed-requests.js).",
    )
    count_closed.add_argument(
        "--delete", action="store_true", help="Delete the requests after counting (prod behaviour)."
    )
    count_closed.set_defaults(func=cmd_count_closed)

    mailjet = subparsers.add_parser(
        "mailjet-sync", help="Sync email contacts to Mailjet (UpdateMailjetLists; daily)."
    )
    mailjet.set_defaults(func=cmd_mailjet)

    snapshot = subparsers.add_parser(
        "snapshot", help="Write a full data snapshot to S3/disk (SnapshotAirtableViews; daily)."
    )
    snapshot.set_defaults(func=cmd_snapshot)

    analyze = subparsers.add_parser(
        "analyze", help="Fulfilled-vs-open analytics (analyze_fulfilled_requests)."
    )
    analyze.set_defaults(func=cmd_analyze)

    import_airtable = subparsers.add_parser(
        "import-airtable",
        help="Migrate the production Airtable V2 base into the local database.",
    )
    import_airtable.add_argument(
        "--base-id",
        default=settings.airtable_base_id,
        help=f"Airtable base id (default {settings.airtable_base_id}).",
    )
    import_airtable.add_argument(
        "--snapshot-dir",
        default="airtable-snapshot",
        help="Directory for the raw JSON snapshot (gitignored; contains PII).",
    )
    import_airtable.add_argument(
        "--from-snapshot",
        default=None,
        metavar="DIR",
        help="Import from an existing snapshot directory instead of the API.",
    )
    import_airtable.set_defaults(func=cmd_import_airtable)

    blast = subparsers.add_parser(
        "blast", help="Build the outreach list and send the text blast (spec 6.2)."
    )
    blast.add_argument(
        "--template",
        required=True,
        help="Message template; supports [FIRST_NAME] and [REQUEST_URL].",
    )
    blast.add_argument(
        "--request-types",
        nargs="*",
        default=None,
        help="Restrict to households with these open request types (supplies match).",
    )
    blast.add_argument(
        "--languages",
        nargs="*",
        default=None,
        help="Restrict to households speaking any of these languages.",
    )
    blast.add_argument(
        "--limit", type=int, default=None, help="Truncate the outreach list to N households."
    )
    blast.add_argument(
        "--max-messages",
        type=int,
        default=None,
        help=f"Message cap for this blast (default {settings.sms_max_messages}).",
    )
    blast.add_argument(
        "--exclude-texted-days",
        type=int,
        default=0,
        help="Skip households texted within the last N days (0 disables).",
    )
    blast.add_argument(
        "--exclude-attended-days",
        type=int,
        default=0,
        help="Skip households that attended within the last N days (0 disables).",
    )
    blast.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview only: console SMS provider, and no state (Last Texted) is persisted.",
    )
    blast.set_defaults(func=cmd_blast)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
