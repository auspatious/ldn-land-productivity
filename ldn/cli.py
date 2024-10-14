import os
from typing import Tuple

from ldn.utils import get_tile_index

import typer

from ldn.processor import LDNPRocessor

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
    n_workers: int = 1,
    threads_per_worker: int = 64,
    memory_limit: str = "120GB",
    longitude_chunks: int = 1250,
    latitude_chunks: int = 1250,
    overwrite: bool = False,
    version: str = "0.0.1",
    decimated: bool = False,
):
    if tile is None:
        aws_job_id = os.environ.get("AWS_BATCH_JOB_ID")
        if aws_job_id is None:
            raise typer.BadParameter(
                "Tile must be provided as an argument or via the AWS_BATCH_JOB_ID environment variable"
            )

        tile = get_tile_index(int(aws_job_id))

    dask_config = {
        "n_workers": n_workers,
        "threads_per_worker": threads_per_worker,
        "memory_limit": memory_limit,
    }
    dask_chunks = {
        "longitude": longitude_chunks,
        "latitude": latitude_chunks,
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
