# Description: This file contains the processor class that is used to process the data

import json
from typing import Iterable
import numpy as np
from pystac_client import Client
from pystac import ItemCollection, Asset, Item
from odc.stac import load, configure_s3_access
from odc.geo.cog import to_cog
from dask.distributed import Client as DaskClient
from xarray import Dataset

from datetime import datetime, timezone

import boto3
from botocore.client import BaseClient

from rio_stac import create_stac_item


from utils import (
    WGS84GRID30,
    USGSLANDSAT,
    USGSCATALOG,
    http_to_s3_url,
    mask_usgs_landsat,
    create_land_productivity_indices,
)

from odc.geo.geobox import GeoBox

from logging import getLogger, StreamHandler, Formatter, Logger, INFO


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
    cached: bool = False

    collection: str = "geo-ls-lp"
    item: Item = None
    version: str = None

    dask_config = {
        "n_workers": 4,
        "threads_per_worker": 1,
        "memory_limit": "16GB",
    }

    def __init__(
        self,
        tile: Iterable,
        year: int,
        bucket: str,
        bucket_path: str,
        dask_config: dict = None,
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

        self.overwrite = overwrite

        self._setup_logger()
        configure_s3_access(cloud_defaults=True, requester_pays=True)

    def _setup_logger(self) -> Logger:
        """Set up a simple logger"""
        console = StreamHandler()
        time_format = "%Y-%m-%d %H:%M:%S"
        console.setFormatter(
            Formatter(
                fmt=f"%(asctime)s %(levelname)s ({self.tile[0]}_{self.tile[1]}): %(message)s",
                datefmt=time_format,
            )
        )

        log = getLogger("GEOMAD")
        log.addHandler(console)
        log.setLevel(INFO)

        self.log = log

    @property
    def tile_id(self):
        return f"{self.collection}_{self.year}_{self.tile[0]:03}_{self.tile[1]:03}"

    @property
    def path(self):
        out_path = (
            f"{self.bucket_path}/"
            f"{self.collection.replace('-', '_')}/"
            f"{self.version.replace('.', '_')}/"
            f"{self.year}/{self.tile[0]:03}/{self.tile[1]:03}"
        )
        return out_path

    def key(self, var: str | None, ext: str = "tif"):
        name = self.tile_id if var is None else f"{self.tile_id}_{var}"
        return f"{self.path}/{name}.{ext}"

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

        return f"s3://{self.bucket}/{key}"

    def get_stac_item(self):
        assets = {
            var: Asset(
                media_type="image/tiff; application=geotiff; profile=cloud-optimized",
                href=f"https://{self.bucket}/{self.key(var)}",
                roles=["data"],
            )
            for var in self.results.data_vars
        }

        # Get the first asset href
        href = list(assets.values())[0].href

        return create_stac_item(
            href,
            id=self.tile_id,
            collection=self.collection,
            input_datetime=datetime(self.year, 1, 1, tzinfo=timezone.utc),
            assets=assets,
            with_proj=True,
            with_raster=True,
        )

    def find(self):
        client = Client.open(USGSCATALOG)
        self.items = client.search(
            collections=[USGSLANDSAT],
            intersects=self.geobox.geographic_extent,
            datetime=f"{self.year - 1}-11/{self.year + 1}-01",
            query={"landsat:collection_category": {"in": ["T1"]}},
        ).item_collection()

        self.log.info(f"Found {len(self.items)} items")

    def load(self):
        if self.items is None:
            self.log.error("No items to load")

        self.log.info(f"Loading {len(self.items)} items")

        data = load(
            self.items,
            geobox=self.geobox,
            measurements=["red", "nir08", "qa_pixel"],
            chunks={"x": 2501, "y": 2501},
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

    def transform(self, cache=False):
        if self.data is None:
            self.log.error("No data to transform")

        masked = mask_usgs_landsat(self.data)
        indices = create_land_productivity_indices(masked, drop=True)
        monthly = indices.resample(time="1ME").median()

        # Todo: see if this can be done with more fancy interpolation
        filled = monthly.bfill("time").ffill("time")

        # Interpolate daily data, so we can compute the integral
        daily = filled.resample(time="1D")
        interpolated = daily.interpolate("linear").sel(
            time=slice(f"{self.year}-01-01", f"{self.year}-12-31")
        )
        integral = interpolated.integrate("time", datetime_unit="D")

        if cache:
            with DaskClient(**self.dask_config):
                integral = integral.compute()

        # Todo: consider adding other statistics, like median or max

        self.log.info(
            f"Results processed with shape lon: {integral.sizes['longitude']} lat: {integral.sizes['latitude']}"
        )

        self.results = integral

    def write(self):
        if self.results is None:
            self.log.error("No results to save")

        # Set up our client
        s3 = boto3.client("s3")

        # Check if we're already done
        stac_key = self.key(None, "stac-item.json")
        if not self.overwrite:
            try:
                s3.head_object(Bucket=self.bucket, Key=stac_key)
                self.log.info(f"Skipping {stac_key} as it already exists")
                return
            except s3.exceptions.ClientError as e:
                self.log.exception(e)

        written = []

        def _process():
            # Write files to S3 as cloud optimised GeoTIFFs
            for var in self.results.data_vars:
                if not self.overwrite:
                    try:
                        s3.head_object(Bucket=self.bucket, Key=self.key(var, "tif"))
                        self.log.info(f"Skipping {var} as it already exists")
                        continue
                    except s3.exceptions.ClientError as e:
                        self.log.exception(e)
                # Get and write the cog
                binary_cog = to_cog(self.results[var], nodata=np.nan)
                out_path = self.s3_dump(
                    binary_cog, self.key(var, "tif"), s3, content_type="image/tiff"
                )

                # Keep notes
                written.append((var, out_path))

                self.log.info(f"Finished writing {var} to {out_path}")

        if not self.cached:
            with DaskClient(**self.dask_config):
                _process()
        else:
            _process()

        # Create a STAC document
        item = self.get_stac_item()
        item.set_self_href(f"https://{self.bucket}/{stac_key}")

        self.item = item

        # Write the STAC document
        stac_item_json = json.dumps(item.to_dict(), indent=4)
        self.s3_dump(
            stac_item_json, stac_key, s3, content_type="application/json"
        )

        self.log.info(f"Saved {len(written)} files and STAC item to https://{self.bucket}/{stac_key}")
