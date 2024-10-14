# Description: This file contains the processor class that is used to process the data

import json
from datetime import datetime, timezone
from logging import INFO, Formatter, Logger, StreamHandler, getLogger
from typing import Iterable, List, Tuple

import boto3
import numpy as np
import rasterio
from botocore.client import BaseClient
from dask import config
from dask.distributed import Client as DaskClient
from odc.geo.cog import to_cog
from odc.geo.geobox import GeoBox
from odc.stac import configure_s3_access, load
from pystac import Asset, Item, ItemCollection
from pystac_client import Client
from rio_stac import create_stac_item
from rio_stac.stac import get_raster_info
from xarray import Dataset

from ldn.utils import (
    USGSCATALOG,
    USGSLANDSAT,
    WGS84GRID30,
    create_land_productivity_indices,
    http_to_s3_url,
    mask_usgs_landsat,
)


class LDNPRocessor:
    log = None
    overwrite: bool = False

    tile: Iterable = None
    geobox: GeoBox = None
    year: int = None
    bucket: str = None
    bucket_path: str = None

    items: ItemCollection = None
    data: Dataset = None
    results: Dataset = None

    collection: str = "geo-ls-lp"
    item: Item = None
    version: str = None
    written: List[Tuple[str, str]] = []

    dask_config = {
        "n_workers": 4,
        "threads_per_worker": 8,
        "memory_limit": "16GB",
    }

    dask_chunks = {"x": 2501, "y": 2501, "time": 1}

    def __init__(
        self,
        tile: Iterable,
        year: int,
        bucket: str,
        bucket_path: str | None = None,
        dask_config: dict | None = None,
        dask_chunks: dict | None = None,
        overwrite: bool = False,
        version: str = "0.0.0",
        *args,
        **kwargs,
    ):
        self.tile = tile
        self.geobox = WGS84GRID30.tile_geobox(tile)
        self.tile = tile
        self.year = year
        self.bucket = bucket
        self.bucket_path = bucket_path
        self.version = version

        if dask_config is not None:
            self.dask_config = dask_config

        if dask_chunks is not None:
            self.dask_chunks = dask_chunks

        self.overwrite = overwrite

        # Initialise the logger
        self._configure_logging()

        # Configure the S3 read access and performance settings
        configure_s3_access(cloud_defaults=True, requester_pays=True)

    def _configure_logging(self) -> Logger:
        """Set up a simple logger"""
        console = StreamHandler()
        time_format = "%Y-%m-%d %H:%M:%S"
        console.setFormatter(
            Formatter(
                fmt=f"%(asctime)s %(levelname)s ({self.tile[0]}_{self.tile[1]}): %(message)s",
                datefmt=time_format,
            )
        )

        log = getLogger("LDN")
        if not log.hasHandlers():
            log.addHandler(console)
        log.setLevel(INFO)

        self.log = log

    @property
    def tile_id(self):
        return f"{self.collection.replace('-', '_')}_{self.year}_{self.tile[0]:03}_{self.tile[1]:03}"

    @property
    def path(self):
        parts = []
        if self.bucket_path is not None:
            parts.append(self.bucket_path)

        parts = parts + [
            self.collection.replace("-", "_"),
            self.version.replace(".", "_"),
            f"{self.tile[0]:03}",
            f"{self.tile[1]:03}",
            str(self.year),
        ]

        return "/".join(parts)

    def key(self, var: str | None, ext: str = "tif"):
        name = self.tile_id if var is None else f"{self.tile_id}_{var}"
        return f"{self.path}/{name}.{ext}"

    def s3_exists(
        self, bucket: str, key: str, client: BaseClient | None = None
    ) -> bool:
        """Check if a key exists in a bucket."""
        if client is None:
            client = boto3.client("s3")

        try:
            client.head_object(Bucket=bucket, Key=key)
            return True
        except client.exceptions.ClientError:
            return False

    def s3_dump(
        self,
        data: bytes,
        key: str,
        client: BaseClient,
        content_type="application/octet-stream",
    ):
        r = client.put_object(
            Bucket=self.bucket, Key=key, Body=data, ContentType=content_type
        )
        code = r["ResponseMetadata"]["HTTPStatusCode"]
        assert 200 <= code < 300

        return f"https://{self.bucket}/{key}"

    def stac_exists(self):
        client = boto3.client("s3")
        return self.s3_exists(self.bucket, self.key(None, "stac-item.json"), client)

    def get_stac_item(self):
        assets = {}

        for name, href in self.written:
            if name == "STAC":
                continue
            with rasterio.open(href) as src_dst:
                raster_info = {"raster:bands": get_raster_info(src_dst, max_size=1024)}
            assets[name] = Asset(
                media_type="image/tiff; application=geotiff; profile=cloud-optimized",
                href=href,
                roles=["data"],
                extra_fields=raster_info
            )

        # Get the first asset href
        href = self.written[0][1]

        item = create_stac_item(
            href,
            id=self.tile_id,
            collection=self.collection,
            input_datetime=datetime(self.year, 1, 1, tzinfo=timezone.utc),
            assets=assets,
            with_proj=True,
            with_raster=True,
        )

        item.set_self_href(f"https://{self.bucket}/{self.key(None, 'stac-item.json')}")

        self.item = item

        return item

    def find(self):
        client = Client.open(USGSCATALOG)
        self.items = client.search(
            collections=[USGSLANDSAT],
            intersects=self.geobox.geographic_extent,
            datetime=f"{self.year - 1}-11/{self.year + 1}-01",
            query={"landsat:collection_category": {"in": ["T1"]}},
        ).item_collection()

        self.log.info(f"Found {len(self.items)} items")

    def load(self, decimated=False):
        if self.items is None:
            self.log.error("No items to load")
            return

        self.log.info(f"Loading {len(self.items)} items")

        geobox = self.geobox
        if decimated:
            self.log.warning("Working at low resolution")
            geobox = geobox.zoom_out(10)

        data = load(
            self.items,
            geobox=geobox,
            measurements=["red", "nir08", "qa_pixel"],
            chunks=self.dask_chunks,
            groupby="solar_day",
            dtype="uint16",
            nodata=0,
            resampling={"qa_pixel": "nearest"},
            patch_url=http_to_s3_url,
        )

        data = data.rename_vars({"nir08": "nir"})

        self.log.info(
            f"Loaded data with shape lon: {data.sizes['longitude']} lat: {data.sizes['latitude']} time: {data.sizes['time']}"
        )

        self.data = data

    def transform(self):
        if self.data is None:
            self.log.error("No data to transform")
            return

        self.log.info("Preparing data...")
        # Mask the data and compute the indices
        masked = mask_usgs_landsat(self.data)
        indices = create_land_productivity_indices(masked, drop=True)

        self.log.info("Resampling to monthly...")
        # Group data by month, so we can more easily interpolate and fill gaps
        monthly = indices.resample(time="1ME").max()

        # Load data into memory here
        with config.set(
            {
                "dataframe.shuffle.method": "p2p",
                "distributed.worker.memory.target": False,
                "distributed.worker.memory.spill": False,
                "distributed.worker.memory.pause": 0.9,
                "distributed.worker.memory.terminate": 0.98,
            }
        ):
            with DaskClient(**self.dask_config):
                self.log.info("Interpolating and filling gaps...")
                filled = (
                    monthly.chunk({"time": -1})
                    .interpolate_na("time", method="linear")
                    .bfill("time")
                    .ffill("time")
                ).compute()

                self.log.info("Computing the integral...")
                # Select just the year we are interested in and compute the integral
                self.results = filled.sel(time=f"{self.year}").integrate(
                    "time", datetime_unit="D"
                )

            # Todo: consider adding other statistics, like median or max

        self.log.info(
            f"Results processed with shape lon: {self.results.sizes['longitude']} lat: {self.results.sizes['latitude']}"
        )

    def write(self, overwrite=False):
        if self.results is None:
            self.log.error("No results to save")
            return

        overwrite = overwrite or self.overwrite

        # Set up our client
        s3 = boto3.client("s3")

        # Check if we're already done
        if not overwrite and self.s3_exists(self.bucket, self.key(None, "stac-item.json"), s3):
            self.log.info("Skipping this tile as it already exists")
            return

        # Write files to S3 as cloud optimised GeoTIFFs
        for var in self.results.data_vars:
            key = self.key(var, "tif")

            # Skip if it exists and we're not overwriting
            if not overwrite and self.s3_exists(self.bucket, key, s3):
                self.log.info(f"Skipping {var} as it already exists")
                continue

            # Get and write the cog
            binary_cog = to_cog(self.results[var], nodata=np.nan)
            out_path = self.s3_dump(binary_cog, key, s3, content_type="image/tiff")

            # Keep notes
            self.written.append((var, out_path))

            self.log.info(f"Finished writing {var} to {out_path}")

        # Create a STAC document
        item = self.get_stac_item()

        # Write the STAC document
        stac_item_json = json.dumps(item.to_dict(), indent=4)
        self.s3_dump(stac_item_json, self.key(None, "stac-item.json"), s3, content_type="application/json")

        self.log.info(f"Finished writing STAC to {self.item.self_href}")
        self.written.append(("STAC", self.item.self_href))

        self.log.info(f"Finished writing {len(self.written)} files")

    def run(self, decimated=False):
        self.log.info("Starting full run...")

        if not self.overwrite and self.stac_exists():
            self.log.info("STAC item already exists")
        else:
            self.find()
            self.load(decimated=decimated)
            self.transform()
            self.write()

        self.log.info(f"Finished full run, STAC is at: {self.item.self_href}")
