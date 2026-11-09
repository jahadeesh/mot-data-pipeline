"""`mot-pipeline` command line: download | extract | transform | validate | run."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

from mot_pipeline import config
from mot_pipeline import download as download_mod
from mot_pipeline.config import Layout, resolve_data_dir, resolve_manifest
from mot_pipeline.extract import extract_zip
from mot_pipeline.transform import run_transform
from mot_pipeline.validate import print_report, run_validation, save_report

app = typer.Typer(
    help="Download, transform and validate the DVSA anonymised MOT test dataset.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

Year = Annotated[
    int,
    typer.Option(
        "--year",
        "-y",
        help="Release year (post-May-2018 format only).",
        min=config.MIN_SUPPORTED_YEAR,
    ),
]
Sample = Annotated[
    int | None,
    typer.Option(
        "--sample",
        "-n",
        min=1,
        help="Limit to N tests from the first month (and their failure items).",
    ),
]
DataDir = Annotated[
    Path | None,
    typer.Option(
        "--data-dir",
        help=f"Data root. Defaults to ${config.DATA_DIR_ENV}, then ./data.",
        show_default=False,
    ),
]
Manifest = Annotated[
    Path | None,
    typer.Option(
        "--manifest",
        help=f"Download manifest (committed to git). Defaults to ${config.MANIFEST_ENV}, "
        f"then ./{config.DEFAULT_MANIFEST.as_posix()}.",
        show_default=False,
    ),
]


def _layout(data_dir: Path | None, manifest: Path | None = None) -> Layout:
    return Layout(resolve_data_dir(data_dir), manifest_path=resolve_manifest(manifest))


def _do_download(layout: Layout, year: int, verify: bool) -> None:
    with (
        Progress(
            TextColumn("{task.description}"),
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=console,
            transient=True,
        ) as progress,
        download_mod.make_client() as client,
    ):
        tasks: dict[str, int] = {}

        def on_progress(label: str, advance: int, total: int | None) -> None:
            if label not in tasks:
                tasks[label] = progress.add_task(label, total=total)
            progress.advance(tasks[label], advance)

        download_mod.download_year(
            layout, year, client, verify=verify, progress=on_progress, log=console.print
        )


def _do_extract(layout: Layout, year: int, sample: int | None) -> None:
    for kind in ("lookups", "results", "items"):
        zip_path = layout.raw_zip(kind, year)
        if not zip_path.exists():
            raise typer.BadParameter(f"{zip_path} not found; run `mot-pipeline download` first")
        paths = extract_zip(
            zip_path, layout.extracted(kind, year), sample=None if kind == "lookups" else sample
        )
        console.print(f"extracted {kind}: {len(paths)} file(s)")


def _do_validate(layout: Layout, year: int, sample: int | None) -> bool:
    report = run_validation(layout, year, sample)
    print_report(report, console)
    path = layout.report_file(year, sample)
    save_report(report, path)
    console.print(f"report written to {path}")
    return report.passed


@app.command()
def download(
    year: Year = config.DEFAULT_YEAR,
    data_dir: DataDir = None,
    manifest: Manifest = None,
    verify: Annotated[
        bool, typer.Option(help="Re-hash existing files against the manifest.")
    ] = False,
) -> None:
    """Download the results, failure-item and lookup ZIPs (resumable, skips up-to-date files)."""
    _do_download(_layout(data_dir, manifest), year, verify)


@app.command()
def extract(
    year: Year = config.DEFAULT_YEAR, sample: Sample = None, data_dir: DataDir = None
) -> None:
    """Extract the CSVs from the downloaded ZIPs (sample mode: first month only)."""
    _do_extract(_layout(data_dir), year, sample)


@app.command()
def transform(
    year: Year = config.DEFAULT_YEAR, sample: Sample = None, data_dir: DataDir = None
) -> None:
    """Transform extracted CSVs to partitioned zstd parquet with DuckDB."""
    run_transform(_layout(data_dir), year, sample, log=console.print)


@app.command()
def validate(
    year: Year = config.DEFAULT_YEAR, sample: Sample = None, data_dir: DataDir = None
) -> None:
    """Validate the parquet output; exits with code 1 if any check fails."""
    if not _do_validate(_layout(data_dir), year, sample):
        raise typer.Exit(code=1)


@app.command()
def run(
    year: Year = config.DEFAULT_YEAR,
    sample: Sample = None,
    data_dir: DataDir = None,
    manifest: Manifest = None,
    skip_download: Annotated[
        bool, typer.Option(help="Use the ZIPs already in raw/ without contacting the server.")
    ] = False,
) -> None:
    """Run download -> extract -> transform -> validate end to end."""
    layout = _layout(data_dir, manifest)
    console.rule(f"[bold]mot-pipeline {year}" + (f" (sample {sample:,})" if sample else ""))
    console.print(f"data dir: {layout.root.resolve()}")
    if not skip_download:
        console.rule("download")
        _do_download(layout, year, verify=False)
    console.rule("extract")
    _do_extract(layout, year, sample)
    console.rule("transform")
    run_transform(layout, year, sample, log=console.print)
    console.rule("validate")
    if not _do_validate(layout, year, sample):
        raise typer.Exit(code=1)


if __name__ == "__main__":  # pragma: no cover
    app()
