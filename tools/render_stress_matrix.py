"""Render the editorial-card stress matrix (edge cases, not happy paths)."""
from __future__ import annotations

from pathlib import Path

from ghos_v6.cards import render_editorial_card

BASE = dict(pushed_at="2026-10-31T00:00:00Z", license={"spdx_id": "MIT"})
CASES: list[tuple[str, dict, dict]] = [
    ("01_longname_no_stack", dict(name="astronomically-long-project-name", language="Kotlin", stars=8300, forks=410, contributors_count=37, topics=[], description="A tiny CLI."),
     dict(card_hook="A tiny CLI that replaces six tools", card_subline="One binary that handles linting, formatting and packaging for JVM projects.")),
    ("02_shortname_full_stack", dict(name="rq", language="Python", stars=1900, forks=55, contributors_count=12, topics=["docker", "postgresql", "redis", "fastapi"], description="Queue."),
     dict(card_hook="Queues without a broker", card_subline="Background jobs backed by Postgres and Redis, no extra infrastructure to run.")),
    ("03_worst_case", dict(name="supercalifragilistic-expialidocious-service", language="TypeScript", stars=1234000, forks=98765, contributors_count=4321, topics=["kubernetes", "postgresql", "tailwindcss", "nextjs"], description="Everything."),
     dict(card_hook="Deploy a full stack from a single file and never touch a YAML chart again",
          card_subline="Opinionated platform that bundles Kubernetes, Postgres, a Tailwind design system and Next.js routing into one deployable unit with sane defaults.")),
    ("04_no_language", dict(name="wiki-dump", language=None, stars=640, forks=18, contributors_count=None, topics=["docker"], description="Static archives."),
     dict(card_hook="Take Wikipedia offline", card_subline="Turns a dump file into a browsable static archive you can carry on a hard drive.")),
    ("05_unknown_language", dict(name="nix-flakes-mono", language="Nix", stars=2100, forks=140, contributors_count=31, topics=["nix", "docker"], description="Nix monorepo."),
     dict(card_hook="Monorepo builds that cache themselves", card_subline="Nix-based build graph for large repositories; every dependency is reproducible by construction.")),
    ("06_license_unknown", dict(name="shadow-index", language="Go", stars=3100, forks=260, contributors_count=44, topics=["cli", "docker"], description="File indexer."),
     dict(card_hook="Find any file in 40 ms", card_subline="A background indexer that keeps a searchable map of every file on the machine.")),
    ("07_license_long", dict(name="legal-eagle", language="Ruby", stars=940, forks=61, contributors_count=19, topics=["postgresql"], description="Licence auditor."),
     dict(card_hook="Audit licences before the lawyers do", card_subline="Scans every dependency and flags copyleft licences that clash with your own.")),
    ("08_huge_numbers", dict(name="linux-kernel-fork", language="C", stars=199999999, forks=15999999, contributors_count=21543, topics=["docker"], description="Kernel."),
     dict(card_hook="A kernel tuned for tiny machines", card_subline="Stripped-down Linux for single-board computers and routers with 32 MB of RAM.")),
    ("09_bright_brand", dict(name="tiny-json", language="JavaScript", stars=2700, forks=310, contributors_count=8, topics=["nodejs"], description="JSON parser."),
     dict(card_hook="JSON parsing without dependencies", card_subline="Drop-in replacement that parses faster than the platform built-in on large payloads.")),
    ("10_dotnet_cloud", dict(name="dotnet-observability", language="C#", stars=5200, forks=430, contributors_count=96, topics=["kubernetes", "dotnet", "grafana"], description="Tracing for .NET."),
     dict(card_hook="Tracing that ships with the framework", card_subline="OpenTelemetry wiring for .NET services with one line in Program.cs.")),
    ("11_no_stack", dict(name="beancount-importer", language="Python", stars=330, forks=12, contributors_count=3, topics=[], description="Bank import."),
     dict(card_hook="Turn bank CSVs into double-entry books", card_subline="Importer that maps statement exports into Beancount entries.")),
    ("12_long_no_stack", dict(name="distributed-consensus-benchmarking-harness", language="Rust", stars=1100, forks=80, contributors_count=22, topics=[], description="Bench for Raft."),
     dict(card_hook="Benchmark Raft implementations side by side", card_subline="Reproducible harness with fault injection, latency percentiles and cluster snapshots.")),
    ("13_four_one_letter_tools", dict(name="go", language="Go", stars=88000, forks=12400, contributors_count=2100, topics=["docker", "kubernetes", "graphql", "grpc"], description="Language."),
     dict(card_hook="The language that made concurrency boring", card_subline="Goroutines, a single static binary and a toolchain that ships with the compiler.")),
    ("14_one_char_name", dict(name="c", language="C", stars=21000, forks=3900, contributors_count=610, topics=["cmake"], description="Compiler."),
     dict(card_hook="A C compiler in 20 000 lines", card_subline="Self-hosting compiler that builds itself in under a second on a laptop.")),
]


def main() -> None:
    out = Path("/home/user/system-design/v6_test_preview/matrix")
    out.mkdir(parents=True, exist_ok=True)
    for case_id, candidate, generated in CASES:
        render_editorial_card({**BASE, **candidate}, generated, out / f"M_{case_id}.png", issue_number=5)
    print("rendered", len(CASES), "cards →", out)


if __name__ == "__main__":
    main()
