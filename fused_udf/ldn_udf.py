def udf(
    bbox: fused.types.TileGDF = None,
    year: int = 2023,
    variable: str = "evi2",
):
    import odc.stac
    import palettable
    from pystac import Item
    import utils

    # Fixed list of tiles until stacrs is available
    tiles = [
        (46, 237),
        (46, 238),
        (46, 239),
        (47, 237),
        (47, 238),
        (47, 239),
        (48, 237),
        (48, 238),
        (48, 239),
        (49, 237),
        (49, 238),
        (49, 239),
        (66, 78),
        (66, 79),
        (66, 80),
        (67, 78),
        (67, 79),
        (67, 80),
        (68, 78),
        (68, 79),
        (68, 80),
        (69, 78),
        (69, 79),
        (69, 80),
        (70, 60),
        (70, 61),
        (71, 60),
        (71, 61),
        (72, 60),
        (72, 61),
    ]

    # Fill tile with 0s to make it a 3 digit number
    tiles = [(f"{tile[0]:03d}", f"{tile[1]:03d}") for tile in tiles]

    template = "https://data.ldn.auspatious.com/geo_ls_lp/0_1_0/{tile_y}/{tile_x}/{year}/geo_ls_lp_{year}_{tile_y}_{tile_x}.stac-item.json"
    items = []
    for tile in tiles:
        url = template.format(tile_x=tile[1], tile_y=tile[0], year=year)
        items.append(Item.from_file(url))

    # Calculate the resolution based on zoom level.
    power = (13 - bbox.z[0])
    if power < 0:
        resolution = 30
    else:
        resolution = int(20 * 2 ** power)

    # Load the data into an XArray dataset
    data: Dataset = odc.stac.load(
        items,
        crs="EPSG:3857",
        bands=[variable],
        resolution=resolution,
        bbox=bbox.total_bounds,
    ).squeeze()

    # Create a mask where data is nan
    mask = (~data.evi2.isnull()).squeeze().to_numpy()

    # Visualize that data as an RGB image.
    rgb_image = utils.visualize(
        data=data[variable],
        mask=mask,
        min=0,
        max=360,
        colormap=palettable.matplotlib.Viridis_20,
    )
    return rgb_image
