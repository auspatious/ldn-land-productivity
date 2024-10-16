import os
from typing import Tuple

import typer

from ldn.processor import LDNPRocessor
from ldn.utils import get_tile_index, get_tiles

app = typer.Typer()


def parse_tile(tile: str | None) -> Tuple[int, int] | None:
    if tile is None:
        return None
    try:
        return tuple(map(int, tile.strip("()").split(",")))
    except ValueError:
        raise typer.BadParameter("Tile must be in the format (x, y)")


@app.command()
def run(
    tile: str | None = typer.Option(default=None, callback=parse_tile),
    year: int = 2023,
    bucket: str = "data.ldn.auspatious.com",
    bucket_path: str | None = None,
    n_workers: int = 4,
    threads_per_worker: int = 32,
    memory_limit: str = "80GB",
    lon_lat_chunks: int = 1250,
    overwrite: bool = False,
    version: str = "0.0.1",
    decimated: bool = False,
):
    if tile is None:
        aws_job_id = os.environ.get("AWS_BATCH_JOB_ARRAY_INDEX")
        typer.echo(f"AWS_BATCH_JOB_ARRAY_INDEX: {aws_job_id}")
        if aws_job_id is None:
            raise typer.BadParameter(
                "Tile must be provided as an argument or via the AWS_BATCH_JOB_ARRAY_INDEX environment variable"
            )
        try:
            tile = get_tile_index(int(aws_job_id))
        except ValueError:
            typer.echo(
                f"Invalid AWS_BATCH_JOB_ARRAY_INDEX: {aws_job_id} is outside range 0-{len(get_tiles)}"
            )
            # Exit with success
            raise typer.Exit(0)

    dask_config = {
        "n_workers": n_workers,
        "threads_per_worker": threads_per_worker,
        "memory_limit": memory_limit,
    }
    dask_chunks = {
        "longitude": lon_lat_chunks,
        "latitude": lon_lat_chunks,
        "time": -1,
    }

    proc = LDNPRocessor(
        tile=tile,
        year=year,
        bucket=bucket,
        bucket_path=bucket_path,
        dask_config=dask_config,
        dask_chunks=dask_chunks,
        overwrite=overwrite,
        version=version,
    )

    proc.run(decimated=decimated)


if __name__ == "__main__":
    app()
