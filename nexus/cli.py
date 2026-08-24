"""Command line for local runs, ops and inspection.

    python -m nexus.cli produce --brief "..." --minutes 12 --profile balanced
    python -m nexus.cli models --modality video
    python -m nexus.cli routes --profile premium
    python -m nexus.cli quote --minutes 12
    python -m nexus.cli doctor
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from nexus.config import settings
from nexus.util.logging import configure_logging


def _fmt_usd(value: float) -> str:
    return f"${value:,.2f}" if value >= 0.01 else f"${value:.4f}"


async def cmd_produce(args: argparse.Namespace) -> int:
    from nexus.pipeline.context import ProductionOptions
    from nexus.pipeline.runner import ProductionRequest, build_context, produce

    options = ProductionOptions(
        target_seconds=args.minutes * 60,
        profile=args.profile,
        resolution=args.resolution,
        aspect_ratio=args.aspect,
        reference_images_per_character=args.refs,
        generate_score=not args.no_score,
        critique=not args.no_critique,
        continuity_audit=not args.no_continuity,
        burn_subtitles=args.burn_subtitles,
        max_shot_concurrency=args.concurrency,
        seed=args.seed,
    )
    request = ProductionRequest(
        brief=args.brief, premise=args.premise or "", options=options,
        budget_usd=args.budget, job_id=args.job_id,
    )
    context = build_context(request)

    width = 0

    async def hook(event):
        nonlocal width
        if event.status == "progress":
            line = f"  {event.overall_pct:5.1f}%  {event.stage:<12} {event.message}"
            width = max(width, len(line))
            sys.stdout.write("\r" + line.ljust(width))
            sys.stdout.flush()
            return
        if event.status in ("started", "completed", "skipped", "failed"):
            mark = {"started": "▸", "completed": "✓", "skipped": "·", "failed": "✗"}[event.status]
            sys.stdout.write("\r" + " " * width + "\r")
            print(f"{mark} {event.overall_pct:5.1f}%  {event.stage:<12} {event.message}")
            width = 0

    print(f"\n  {settings.app_name} — producing a {args.minutes:g}-minute episode "
          f"on the '{args.profile}' profile\n")

    if args.resume:
        saved = await context.load_checkpoint()
        if saved:
            context.plan = saved
            print(f"  resuming job {context.job_id} "
                  f"({saved.episode.shot_count()} shots already planned)\n")

    try:
        summary = await produce(request, context=context, progress_hook=hook)
    except Exception as exc:
        print(f"\n✗ production failed: {exc}\n")
        print(f"  spent {_fmt_usd(context.router.ledger.spent_usd)} before failing")
        print(f"  resume with: python -m nexus.cli produce --resume --job-id {context.job_id} ...")
        return 1

    cost = summary["cost"]
    print(f"\n  {summary['title']}")
    print(f"  {summary['scenes']} scenes · {summary['shots']} shots · "
          f"{(summary['actual_seconds'] or 0) / 60:.1f} min")
    print(f"  cost {_fmt_usd(cost['spent_usd'])} over {cost['calls']} model calls "
          f"({cost['fallbacks']} fell back)")
    print(f"  video → {summary['video_url']}\n")
    if cost["by_role"]:
        print("  spend by role:")
        for role, amount in list(cost["by_role"].items())[:8]:
            print(f"    {role:<16} {_fmt_usd(amount)}")
    if summary["warnings"]:
        print(f"\n  {len(summary['warnings'])} warning(s); first few:")
        for warning in summary["warnings"][:5]:
            print(f"    · {warning}")
    print()
    if args.json:
        print(json.dumps(summary, indent=2, default=str))
    return 0


async def cmd_models(args: argparse.Namespace) -> int:
    from nexus.routing.catalog import list_specs
    from nexus.routing.types import Modality, Tier

    specs = list_specs(
        Modality(args.modality) if args.modality else None,
        tier=Tier(args.tier) if args.tier else None,
        available_only=args.available,
    )
    print(f"\n  {len(specs)} model(s)\n")
    print(f"  {'MODEL':<34} {'MODALITY':<8} {'TIER':<9} {'Q':<3} {'PRICE':<16} READY")
    print("  " + "─" * 84)
    for spec in specs:
        if spec.usd_per_second:
            price = f"${spec.usd_per_second:.3f}/s"
        elif spec.usd_per_image:
            price = f"${spec.usd_per_image:.3f}/img"
        elif spec.usd_per_1k_chars:
            price = f"${spec.usd_per_1k_chars:.3f}/1k ch"
        elif spec.usd_per_1m_output:
            price = f"${spec.usd_per_1m_input:.2f}/${spec.usd_per_1m_output:.2f}"
        else:
            price = "free"
        print(f"  {spec.id:<34} {spec.modality.value:<8} {spec.tier.value:<9} "
              f"{int(spec.quality):<3} {price:<16} {'yes' if spec.available() else '—'}")
    print()
    return 0


async def cmd_routes(args: argparse.Namespace) -> int:
    from nexus.routing.router import build_router

    router = build_router(profile=args.profile)
    print(f"\n  routing plan for profile '{args.profile}'\n")
    for role, chain in router.plan().items():
        head, *rest = chain or ["<none>"]
        print(f"  {role:<18} → {head}")
        for fallback in rest[:3]:
            print(f"  {'':<18}   ↳ {fallback}")
    print()
    return 0


async def cmd_quote(args: argparse.Namespace) -> int:
    from nexus.billing.plans import estimate_episode_cost_usd, estimate_episode_credits

    print(f"\n  estimated cost for a {args.minutes:g}-minute episode\n")
    print(f"  {'PROFILE':<12} {'RAW COST':>12} {'CREDITS':>10} {'RETAIL':>10}")
    print("  " + "─" * 48)
    for profile in ("free", "budget", "balanced", "premium", "flagship"):
        usd = estimate_episode_cost_usd(args.minutes * 60, profile)
        credits = estimate_episode_credits(args.minutes * 60, profile)
        print(f"  {profile:<12} {_fmt_usd(usd):>12} {credits:>10,} "
              f"{_fmt_usd(credits * 0.01):>10}")
    print()
    return 0


async def cmd_doctor(_: argparse.Namespace) -> int:
    from nexus.render.ffmpeg import ffmpeg_available
    from nexus.routing.catalog import available_providers
    from nexus.worker.bus import bus

    print(f"\n  {settings.app_name} environment check\n")
    ok = True

    ffmpeg_ok = ffmpeg_available()
    ok &= ffmpeg_ok
    print(f"  {'✓' if ffmpeg_ok else '✗'} ffmpeg / ffprobe"
          f"{'' if ffmpeg_ok else '   (required for rendering — apt-get install ffmpeg)'}")

    try:
        from sqlalchemy import text

        from nexus.db.session import get_engine

        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        print("  ✓ database reachable")
    except Exception as exc:
        ok = False
        print(f"  ✗ database unreachable: {str(exc)[:120]}")

    redis_client = await bus.redis()
    print(f"  {'✓' if redis_client else '·'} queue backend: "
          f"{'redis' if redis_client else 'in-process (single node only)'}")

    print(f"  · storage backend: {settings.storage_backend}")
    print(f"  · billing: {'enabled' if settings.billing_enabled else 'disabled'}")

    providers = available_providers()
    live = sorted(n for n, available in providers.items() if available and n != "simulation")
    print(f"\n  providers configured: {', '.join(live) if live else 'none'}")
    if not live:
        print("    → the offline engine will serve every role; add API keys for real output")

    if settings.is_production and settings.secret_key == "dev-insecure-secret-change-me":
        ok = False
        print("\n  ✗ SECRET_KEY is still the development default")

    print(f"\n  {'ready' if ok else 'not ready — fix the items above'}\n")
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nexus", description="Nexus Motion CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    produce = sub.add_parser("produce", help="produce an episode locally")
    produce.add_argument("--brief", required=True, help="the creative brief for the series")
    produce.add_argument("--premise", help="what happens in this specific episode")
    produce.add_argument("--minutes", type=float, default=settings.default_target_minutes)
    produce.add_argument("--profile", default="balanced",
                         choices=["offline", "free", "budget", "balanced", "premium", "flagship"])
    produce.add_argument("--resolution", default="1080p",
                         choices=["480p", "720p", "1080p", "1440p", "4k"])
    produce.add_argument("--aspect", default="16:9",
                         choices=["16:9", "9:16", "1:1", "4:5", "2.39:1"])
    produce.add_argument("--refs", type=int, default=3, help="reference portraits per character")
    produce.add_argument("--budget", type=float, help="hard spend cap in USD")
    produce.add_argument("--concurrency", type=int, default=0)
    produce.add_argument("--seed", type=int)
    produce.add_argument("--job-id", help="reuse a job id (with --resume)")
    produce.add_argument("--resume", action="store_true", help="continue from the last checkpoint")
    produce.add_argument("--no-score", action="store_true")
    produce.add_argument("--no-critique", action="store_true")
    produce.add_argument("--no-continuity", action="store_true")
    produce.add_argument("--burn-subtitles", action="store_true")
    produce.add_argument("--json", action="store_true", help="print the full summary as JSON")
    produce.set_defaults(func=cmd_produce)

    models = sub.add_parser("models", help="list the model catalog")
    models.add_argument("--modality", choices=["text", "vision", "image", "video", "tts", "music", "sfx"])
    models.add_argument("--tier", choices=["free", "budget", "standard", "premium", "flagship"])
    models.add_argument("--available", action="store_true", help="only models with credentials")
    models.set_defaults(func=cmd_models)

    routes = sub.add_parser("routes", help="show the resolved routing chain")
    routes.add_argument("--profile", default="balanced")
    routes.set_defaults(func=cmd_routes)

    quote = sub.add_parser("quote", help="estimate episode cost across profiles")
    quote.add_argument("--minutes", type=float, default=12.0)
    quote.set_defaults(func=cmd_quote)

    doctor = sub.add_parser("doctor", help="check this deployment's dependencies")
    doctor.set_defaults(func=cmd_doctor)

    return parser


def main() -> int:
    configure_logging()
    args = build_parser().parse_args()
    return asyncio.run(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
