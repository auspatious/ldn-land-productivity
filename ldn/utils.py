from pathlib import Path
from typing import Any, Dict, Tuple

import boto3
import geopandas as gpd
import numpy as np
from affine import Affine
from odc.geo import Geometry
from odc.geo.geobox import GeoBox, GeoboxTiles
from s3fs import S3FileSystem
from xarray import Dataset

WGS84GRID10 = GeoboxTiles(
    GeoBox(
        (1800000, 3600000), Affine(0.0001, 0.0, -180.0, 0.0, 0.0001, -90.0), "epsg:4326"
    ),
    (5000, 5000),
)
WGS84GRID30 = GeoboxTiles(
    GeoBox(
        (600000, 1200000), Affine(0.0003, 0.0, -180.0, 0.0, 0.0003, -90.0), "epsg:4326"
    ),
    (5000, 5000),
)

USGSCATALOG = "https://landsatlook.usgs.gov/stac-server/"
USGSLANDSAT = "landsat-c2l2-sr"


def get_tiles(grid=WGS84GRID30) -> list[Tuple[Tuple[int, int], GeoBox]]:
    """Get all the tiles for all AOIs, returning a list of (tile_index, geobox)"""
    # Load our extents
    this_folder = Path(__file__).parent
    gdf = gpd.read_file(this_folder / "aois.geojson")

    # 0 is Fiji, 1 is Caribbean and 2 is Belize
    fiji = list(grid.tiles(Geometry(gdf.geometry[0], crs="epsg:4326")))
    carb = list(grid.tiles(Geometry(gdf.geometry[1], crs="epsg:4326")))
    belz = list(grid.tiles(Geometry(gdf.geometry[2], crs="epsg:4326")))

    # This is all the tiles
    tiles = fiji + carb + belz

    return [(tile, grid[tile]) for tile in tiles]


def get_tile_index(tile_index: int) -> Tuple[int, int]:
    return get_tiles()[tile_index][0]


def http_to_s3_url(http_url):
    """Convert a USGS HTTP URL to an S3 URL"""
    s3_url = http_url.replace(
        "https://landsatlook.usgs.gov/data", "s3://usgs-landsat"
    ).rstrip(":1")
    return s3_url


def mask_usgs_landsat(data: Dataset) -> Dataset:
    """Create cloud mask, scale values to 0-1 and set nodata to NaN"""
    # Bits 3 and 4 are cloud shadow and cloud, respectively.
    bitflags = 0b00011000

    # Bitwise AND to select any pixel that is cloud shadow or cloud or nodata
    cloud_mask = (data.qa_pixel & bitflags) != 0
    # Note that it might be a good idea to dilate the mask here to catch
    # any pixels that are adjacent to clouds

    # Pick out nodata too
    nodata_mask = data.qa_pixel == 0

    # Combined the masks
    mask = cloud_mask | nodata_mask

    # Mask the original data
    masked = data.where(~mask, other=np.nan).drop_vars("qa_pixel")

    # Scale the data to 0-1
    scaled = (masked.where(masked != 0) * 0.0000275 + -0.2).clip(0, 1)

    return scaled


def create_land_productivity_indices(data: Dataset, drop: bool = True) -> Dataset:
    """Create NDVI, MSAVI and EVI2 indices"""

    # NDVI
    ndvi = (data.nir - data.red) / (data.nir + data.red)
    data["ndvi"] = ndvi.clip(-1, 1)

    # MSAVI
    msavi = 0.5 * (
        (2 * data.nir + 1)
        - np.sqrt((2 * data.nir + 1) ** 2 - 8 * (data.nir - data.red))
    )
    data["msavi"] = msavi.clip(0, 1)

    # EVI2
    evi2 = 2.5 * (data.nir - data.red) / (data.nir + 2.4 * data.red + 1)
    data["evi2"] = evi2.clip(0, 1)

    if drop:
        data = data.drop_vars(["red", "nir"])

    return data


# Submit a batch job
def submit_job(
    job_name: str,
    job_queue: str,
    job_definition: str,
    container_overrides: Dict[str, Any],
    parameters: Dict[str, str],
    multi: bool = False,
    multi_size: int = 30,  # This is how many tiles there are in each year
) -> str:
    """Submit a job to AWS Batch"""
    client = boto3.client("batch")
    extras = {}
    if multi:
        extras["arrayProperties"] = {"size": multi_size}

    response = client.submit_job(
        jobName=job_name,
        jobQueue=job_queue,
        jobDefinition=job_definition,
        containerOverrides=container_overrides,
        parameters=parameters,
        schedulingPriorityOverride=99,
        shareIdentifier="alex",
        retryStrategy={"attempts": 1},
        **extras,
    )
    return response["jobId"]


# Get the status of a job
def get_job_status(job_id: str) -> str:
    """Get the status of a job"""
    client = boto3.client("batch")
    response = client.describe_jobs(jobs=[job_id])
    return response["jobs"][0]["status"]


def get_cloudwatch_logs(
    job_id: str, log_group_name: str = "/aws/batch/auspatious-ldn"
) -> Dict[str, Any]:
    """Get the logs for a job"""
    client = boto3.client("batch")
    response = client.describe_jobs(jobs=[job_id])
    log_stream_name = response["jobs"][0]["container"]["logStreamName"]

    logs_client = boto3.client("logs")

    response = logs_client.get_log_events(
        logGroupName=log_group_name, logStreamName=log_stream_name, startFromHead=True
    )

    return response["events"]


def execute(year: int, tile: tuple[int, int] = None):
    """Submit one or a set of jobs to AWS Batch"""
    extra_params = []
    if tile is not None:
        multi = False
        extra_params = ["--tile", ",".join([str(t) for t in tile])]

    job_name = f"version-0-1-0-{year}"
    job_queue = "normalQueue"
    job_definition = "auspatious-ldn"
    container_overrides = {
        "command": [
            "ldn-processor",
            "--year",
            "Ref::year",
            "--version",
            "Ref::version",
            "--n-workers",
            "Ref::n_workers",
            "--threads-per-worker",
            "Ref::threads_per_worker",
            "--memory-limit",
            "Ref::memory_limit",
            "Ref::overwrite",
            *extra_params,
        ],
        "vcpus": 16,
        "memory": 122880,
    }
    parameters = {
        "tile": "238,47",
        "year": f"{year}",
        "version": "0.1.0",
        "n_workers": "4",
        "threads_per_worker": "32",
        "memory_limit": "100GB",
        "overwrite": "--no-overwrite",
    }

    job_id = submit_job(
        job_name,
        job_queue,
        job_definition,
        container_overrides,
        parameters,
        multi=multi,
    )
    return job_id


def get_s3_keys(version: str = "0.1.0") -> list[str]:
    """Get all the STAC keys for a version"""
    path = f"s3://data.ldn.auspatious.com/geo_ls_lp/{version.replace('.', '_')}/**/*.stac-item.json"

    fs = S3FileSystem(anon=True)
    stac_keys = fs.glob(path)

    return stac_keys
