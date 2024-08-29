//Enter the latitude and longitude range (e.g. 117-118°E,37-38°N)
var longtitude_min = 117
var longtitude_max = 118
var latitude_min = 37
var latitude_max = 38

//Enter the monitoring window (e.g. 2013-2020 year)
var start_year = 2013
var end_year = 2020


//--------------------------------------------start operation-----------------------------------------------
var mod_sr = ee.ImageCollection("MODIS/006/MOD09Q1"),
    landsat_sr = ee.ImageCollection("LANDSAT/LC08/C02/T1_L2");

var visParam4 = { "min": 0, "max": 10000, "palette": ["FF0000", "FF8800", "FFFF00", "00FF00", "00FFFF", "#000000"] };

//---------------------------------------------------------------------------

var number_longtitude_min = longtitude_min
var number_longtitude_max = longtitude_max
var number_latitude_min = latitude_min
var number_latitude_max = latitude_max

var geometry =
    /* color: #d63000 */
    /* displayProperties: [
      {
        "type": "rectangle"
      }
    ] */
    ee.Geometry.Polygon(
        [[[number_longtitude_min, number_latitude_max],
        [number_longtitude_min, number_latitude_min],
        [number_longtitude_max, number_latitude_min],
        [number_longtitude_max, number_latitude_max]]], null, false);

var testregion = geometry
Map.centerObject(testregion, 7)
Map.addLayer({ eeObject: testregion, name: 'processing area' })


for (var y = start_year; y <= end_year; y++) {


    var begin_date = ee.String(ee.Number(y)).cat('-01-01');
    var end_date = ee.String(ee.Number(y + 1)).cat('-01-01');
    // var begin_date = ee.String('2019-01-01');
    // var end_date = ee.String('2020-01-01');

    var imgcol_lt = landsat_sr.filterDate(begin_date, end_date).filterBounds(testregion);

    var list_lt = imgcol_lt.toList(imgcol_lt.size());

    var list_PathRow = list_lt.map(function (img) {

        img = ee.Image(img);
        var wrs_path = ee.Number(img.get("WRS_PATH"));
        var wrs_row = ee.Number(img.get("WRS_ROW"));

        return ee.List([wrs_path, wrs_row]);


    });

    //print(list_PathRow)
    list_PathRow = list_PathRow.distinct();
    //print('list_PathRow',list_PathRow);
    var l = ee.Number(list_PathRow.length()).getInfo()
    //print('l',l)
    var mean_ndvi_list = ee.List([])
    //循环控制;
    for (var rank_img = 0; rank_img < l; rank_img++) {

        /*Extract bitpacked flags
         * image - the band to extract the bits from.
         * start - the lowest bit to extract
         * end - the highest bit to extract*/
        var getQABits = function (image, start, end) {
            var pattern = 0;
            for (var i = start; i <= end; i++) {
                pattern += 1 << i;
            }
            // Return a single band image of the extracted QA bits
            return image.bitwiseAnd(pattern).rightShift(start);
        };

        // Applies scaling factors when using USGS Landsat 5/7/8 Level 2, Collection 2.
        function applyScaleFactors(image) {
            var opticalBands = image.select('SR_B.').multiply(0.0000275).add(-0.2);
            var thermalBands = image.select('ST_B.*').multiply(0.00341802).add(149.0);
            return image.addBands(opticalBands, null, true)
                .addBands(thermalBands, null, true);
        }

        //main procedure

        var path = ee.Number(ee.List(list_PathRow.get(rank_img)).get(0));
        var row = ee.Number(ee.List(list_PathRow.get(rank_img)).get(1));

        /*var begin_date = ee.String('2013-01-01');  //Start date of processing time 
        var end_date = ee.String('2014-01-01');  //End date of processing time
        var path = ee.Number(93);
        var row = ee.Number(84);  //target Landsat scene (e.g.: 93/84)*/

        //Calculate Landsat NDVI time series data
        var landsat_ndvi_mask = landsat_sr.filterDate(begin_date, end_date)
            .filter(ee.Filter.eq('WRS_PATH', path))
            .filter(ee.Filter.eq('WRS_ROW', row))
            .map(applyScaleFactors)
            .map(function (img) {
                var img_sub = img;
                var ndvi = img_sub.normalizedDifference(['SR_B5', 'SR_B4']).rename('L_ndvi');  //calculate Landsat 8 NDVI
                //var ndvi = img_sub.normalizedDifference(['SR_B4', 'SR_B3']).rename('L_ndvi');  //calculate Landsat 5/7 NDVI  
                var Q = img_sub.select('QA_PIXEL');
                var cloud2BitMask = (1 << 2);
                var cloud3BitMask = (1 << 3);
                var cloud4BitMask = (1 << 4);
                var mask = Q.bitwiseAnd(cloud2BitMask).eq(0).and(Q.bitwiseAnd(cloud3BitMask).eq(0))
                    .and(Q.bitwiseAnd(cloud4BitMask).eq(0)).rename('L_cmask');  //generate Landsat 8 cloud mask from pixel_qa band (0:clear;1: cloud/shadow)
                /*//generate Landsat 5/7 cloud mask
                var Q = img_sub.select('QA_PIXEL');
                var cloud3BitMask = (1 << 3);
                var cloud4BitMask = (1 << 4);
                var mask = Q.bitwiseAnd(cloud3BitMask).eq(0).and(Q.bitwiseAnd(cloud4BitMask).eq(0)).eq(0)).rename('L_cmask');
                */
                return ndvi.addBands(mask).copyProperties(img_sub, img_sub.propertyNames());
            });

        var landsat_ndvi = landsat_ndvi_mask.select('L_ndvi');
        var landsat_cmask = landsat_ndvi_mask.select('L_cmask');

        //Show the location of target area
        //print('landsat_ndvi',landsat_ndvi);
        var landsat_ndvi_lis = landsat_ndvi.toList(landsat_ndvi.size())
        var mod_shp = ee.Image(landsat_ndvi_lis.get(-1)).geometry().buffer(-4500)
        //var mod_shp = landsat_ndvi.union().geometry().buffer(-500);
        //Map.addLayer(mod_shp);
        //Map.centerObject(mod_shp);


        //Calculate MODIS NDVI time series data
        var mod_ndvi = mod_sr.filterDate(begin_date, end_date).filterBounds(mod_shp)
            .map(function (image) {
                var image0 = image;
                var img_sub = image0.clip(mod_shp);
                var ndvi = img_sub.normalizedDifference(['sur_refl_b02', 'sur_refl_b01']);  //calculate Landsat NDVI
                var Q = img_sub.select('State');
                var masknum = getQABits(Q, 0, 2);
                var mask = masknum.eq(ee.Image(0)).or(masknum.eq(ee.Image(3)));
                var ndvi_masked = ndvi.updateMask(mask);   //generate MODIS cloud mask from state band (0:clear;1: cloud/shadow/mixed)
                return ndvi_masked.rename(['MOD_NDVI']).copyProperties(img_sub, img_sub.propertyNames());
            });

        //fill the gaps in the original MODIS NDVI time series by linear interpolation
        var interl_m = require('users/Yang_Chen/GF-SG:Interpolation_v1');
        var frame = 8 * 4;
        var nodata = -9999;
        var mod_ndvi_interp = interl_m.linearInterp(mod_ndvi, frame, nodata);
        var mod_ndvi_interp0 = mod_ndvi_interp.select(['MOD_NDVI_INTER']);

        //Smooth the MODIS interpolation time series by the Savitzky–Golay filter
        var sg_filter = require('users/Yang_Chen/GF-SG:SG_filter_v1');
        var list_trend_sgCoeff = ee.List([-0.076923102, -1.4901161e-008, 0.062937059, 0.11188812, 0.14685316, 0.16783218,
            0.17482519, 0.16783218, 0.14685316, 0.11188812, 0.062937059, -1.4901161e-008, -0.076923102]);  //suggested trend parameter: (6,6,0,2), Users can choose other optimal parameters.   
        var list_sg_coeff = ee.List([-0.090909064, 0.060606077, 0.16883118, 0.23376624, 0.25541127, 0.23376624,
            0.16883118, 0.060606077, -0.090909064]);  //suggested parameter of filtering: (4,4,0,3), Users can choose other optimal parameters.
        var mod_ndvi_sg = sg_filter.sg_filter_chen(mod_ndvi_interp0, list_trend_sgCoeff, list_sg_coeff);

        //Resample MODIS NDVI images to 30m by using the bicubic interpolation method
        var L_ref = landsat_ndvi.first();
        var proj = ee.Image(L_ref).select('L_ndvi').projection().crs();
        var mod_ndvi_sg30 = mod_ndvi_sg.map(function (img) {
            return img.multiply(1.023).subtract(0.013).float().resample('bicubic')
                .copyProperties(img, img.propertyNames());
        });

        var combine_ndvi = landsat_ndvi.merge(mod_ndvi_sg30);
        var combine_ndvi0 = landsat_cmask.merge(mod_ndvi_sg30);

        //Get the date of each Landsat image and each MODIS image
        var startDate = ee.Date(begin_date);
        var l_size = ee.Number(landsat_ndvi.size());
        var m_size = ee.Number(mod_ndvi_sg30.size());
        var combinendvi_list = combine_ndvi.toList(l_size.add(m_size));
        var combinendvi_list0 = combine_ndvi0.toList(l_size.add(m_size));
        var landsatndvi_list = combinendvi_list.slice(0, l_size);
        var landsatmask_list = combinendvi_list0.slice(0, l_size);
        var modndvi_list = combinendvi_list.slice(l_size, l_size.add(m_size));
        var list_process = ee.List.sequence(0, l_size.subtract(1));
        var ldate = list_process.map(function (i) {
            i = ee.Number(i);
            var one = landsatndvi_list.get(i);
            var date = ee.Image(one).date();
            var relDoy = date.difference(startDate, 'day');
            return relDoy.toInt();
        });
        var list_process = ee.List.sequence(0, m_size.subtract(1));
        var mdate = list_process.map(function (i) {
            i = ee.Number(i);
            var one = modndvi_list.get(i);
            var date = ee.Image(one).date();
            var relDoy = date.difference(startDate, 'day');
            return relDoy;
        });


        //Get the date with both modis and landsat
        var mdate0 = ee.Array(mdate);
        var list_process = ee.List.sequence(0, l_size.subtract(1));
        var lm_loc = list_process.map(function (i) {
            i = ee.Number(i);
            var onenum = ee.Number(ldate.get(i));
            var diff = mdate0.subtract(onenum).abs().toList();
            var minloc = diff.indexOf(diff.reduce(ee.Reducer.min()));
            return minloc;
        });

        //extract corresponding MODIS images of each Landsat image
        var list_loc_old = ee.List.repeat(1, m_size);
        var replace = function (current, previous) {
            previous = ee.List(previous);
            var i = ee.Number(current);
            var oneloc = ee.Number(lm_loc.get(i));
            var list_loc0 = previous.set(oneloc, 0);
            return list_loc0;
        };
        var list_loc0 = ee.List.sequence(0, l_size.subtract(1)).iterate(replace, list_loc_old);
        var list_loc = ee.List(list_loc0);
        var list_process = ee.List.sequence(0, l_size.subtract(1));
        var modndviPair_list = list_process.map(function (i) {
            i = ee.Number(i);
            var onenum = ee.Number(lm_loc.get(i));
            var onelist = modndvi_list.get(onenum);
            return onelist;
        });
        var modndviPair = ee.ImageCollection(modndviPair_list);

        //Generate adjusted MODIS NDVI time series (series after shape matching and correction)
        var list_process = ee.List.sequence(0, l_size.subtract(1));
        var landsatNdviPair_list = list_process.map(function (i) {
            i = ee.Number(i);
            var landsatone = landsatndvi_list.get(i);
            var landsatone_img = ee.Image(landsatone);
            var maskone = landsatmask_list.get(i);
            var maskone_img = ee.Image(maskone);
            var landsatone_masked = landsatone_img.updateMask(maskone_img);  //mask cloudy pixels in Landsat out
            return landsatone_masked;
        });
        var landsatNdviPair = ee.ImageCollection(landsatNdviPair_list);


        var W0 = ee.List.repeat(1, 31); var W = ee.List.repeat(W0, 31);  //the size of local window for searching the neighboring similar MODIS pixels
        var kW = ee.Kernel.fixed(31, 31, W);
        var modndviPair_nei0 = modndviPair.map(function (img) {
            var img_nei = img.neighborhoodToBands(kW);
            return (img_nei);
        });
        var modndvisg30 = ee.ImageCollection(modndvi_list);
        var modndvisg30_nei = modndvisg30.map(function (img) {
            var img_nei = img.neighborhoodToBands(kW);
            return (img_nei);
        });

        //search Landsat cloudy pixels in the neigborhood of target pixel
        var modndviPair_nei_list = modndviPair_nei0.toList(l_size);
        var modndviPair_nei_list = list_process.map(function (i) {
            i = ee.Number(i);
            var modisone = modndviPair_nei_list.get(i);
            var modisone_img = ee.Image(modisone);
            var maskone = landsatmask_list.get(i);
            var maskone_img = ee.Image(maskone);
            var modisone_masked = modisone_img.updateMask(maskone_img);
            return modisone_masked;
        });
        var modndviPair_nei = ee.ImageCollection(modndviPair_nei_list);

        //Calculate the correlation coefficient between similar pixel and the target pixel
        var modndviPair_nei_mean = modndviPair_nei.mean();
        var landsatNdviPair_mean = landsatNdviPair.mean();

        var modndviPair_nei_diffm = modndviPair_nei.map(function (img) {
            var img_diff = img.subtract(modndviPair_nei_mean);
            return img_diff;
        });
        var landsatNdviPair_diffm = landsatNdviPair.map(function (img) {
            var img_diff = img.subtract(landsatNdviPair_mean);
            return img_diff;
        });

        var modndviPair_nei_diffm_list = modndviPair_nei_diffm.toList(l_size);
        var landsatNdviPair_diffm_diffm_list = landsatNdviPair_diffm.toList(l_size);

        var modnei_landsat_diff_mul_list = list_process.map(function (i) {
            i = ee.Number(i);
            var modnei = modndviPair_nei_diffm_list.get(i);
            var modnei_img = ee.Image(modnei);
            var landsat = landsatNdviPair_diffm_diffm_list.get(i);
            var landsat_img = ee.Image(landsat);
            var landsat_modnei_mul = modnei_img.multiply(landsat_img);
            return landsat_modnei_mul;
        });

        var modnei_landsat_diff_mul = ee.ImageCollection(modnei_landsat_diff_mul_list);
        var modnei_landsat_diffmul_sum = modnei_landsat_diff_mul.sum();
        var modndviPair_nei_diffm_2 = modndviPair_nei_diffm.map(function (img) {
            var img_2 = img.multiply(img);
            return img_2;
        });
        var landsatNdviPair_diffm_2 = landsatNdviPair_diffm.map(function (img) {
            var img_2 = img.multiply(img);
            return img_2;
        });
        var modndviPair_nei_diffm2_sum = modndviPair_nei_diffm_2.sum();
        var landsatNdviPair_diffm2_sum = landsatNdviPair_diffm_2.sum();
        var modnei_landsat_diffmul_sqrt = modndviPair_nei_diffm2_sum.multiply(landsatNdviPair_diffm2_sum)
            .sqrt();
        var modnei_landsat_corre = modnei_landsat_diffmul_sum.divide(modnei_landsat_diffmul_sqrt);

        //Get neighboring similar pixels with the correlation coefficient above 0.8
        var threshold_img = ee.Image.constant(0.8);
        var threshold_mask = modnei_landsat_corre.gte(threshold_img);
        var modnei_landsat_corre_valid = modnei_landsat_corre.multiply(threshold_mask);
        var modnei_landsat_corre_sum = modnei_landsat_corre_valid.reduce(ee.Reducer.sum());
        var modnei_landsat_corre_normal = modnei_landsat_corre_valid.divide(modnei_landsat_corre_sum);
        var modndviPair_match0 = modndviPair_nei0.map(function (img) {
            var onemodis_w = img.multiply(modnei_landsat_corre_normal);
            var onemodis_normal = onemodis_w.reduce(ee.Reducer.sum());
            return onemodis_normal;
        });

        var modndvisg30_match0 = modndvisg30_nei.map(function (img) {
            var onemodis_w = img.multiply(modnei_landsat_corre_normal);
            var onemodis_normal = onemodis_w.reduce(ee.Reducer.sum());
            return onemodis_normal;
        });  //generate the reference time series of the target pixel

        //For these pixels which may be difficult to generate the reference series, the reference can be obtained by averaging the reference series of the neighboring pixels.  
        var wm0 = ee.List.repeat(1, 21); var wm = ee.List.repeat(wm0, 21);  //the size of local window for searching the neighboring pixels
        var thresholdmask_sum = threshold_mask.reduce(ee.Reducer.sum());
        var thresimg = ee.Image.constant(0);
        var valid_flag_mask = thresholdmask_sum.neq(thresimg);
        var invalid_flag_mask = thresholdmask_sum.eq(thresimg);
        var modndviPair_match = modndviPair_match0.map(function (img) {
            var meanimg = img.reduceNeighborhood({
                reducer: ee.Reducer.mean(),
                kernel: ee.Kernel.fixed(21, 21, wm)
            });
            var newimg = img.multiply(valid_flag_mask).add(meanimg.multiply(invalid_flag_mask));
            return newimg;
        });
        var modndvisg30_match = modndvisg30_match0.map(function (img) {
            var meanimg = img.reduceNeighborhood({
                reducer: ee.Reducer.mean(),
                kernel: ee.Kernel.fixed(21, 21, wm)
            });
            var newimg = img.multiply(valid_flag_mask).add(meanimg.multiply(invalid_flag_mask));
            return newimg;
        });  //search corresponding MODIS time series data of clear Landsat points 

        //shape correction by linear transfer function
        var modndviPair_match_list = modndviPair_match.toList(l_size);
        var modndviPair_matchmask_list = list_process.map(function (i) {
            i = ee.Number(i);
            var modisone = modndviPair_match_list.get(i);
            var modisone_img = ee.Image(modisone);
            var maskone = landsatmask_list.get(i);
            var maskone_img = ee.Image(maskone);
            var modisone_masked = modisone_img.updateMask(maskone_img);
            return modisone_masked;
        });

        var mergelist = list_process.map(function (i) {
            i = ee.Number(i);
            var one = ee.Image(landsatNdviPair_list.get(i)).rename('y');
            var one0 = ee.Image(modndviPair_matchmask_list.get(i)).rename('x');
            var two = one.addBands(one0).addBands(ee.Image.constant(1));
            return two;
        });
        var merge = ee.ImageCollection(mergelist);
        var independents = ee.List(['constant', 'x']);
        var dependent = ee.String('y');
        var trend = merge.select(independents.add(dependent))
            .reduce(ee.Reducer.linearRegression(independents.length(), 1));
        var coefficients = trend.select('coefficients')
            .arrayProject([0])
            .arrayFlatten([independents]);  //b1 and b0 are solved by minimizing the difference between adjusted time series and the corresponding Landsat time series
        var b1 = coefficients.select('x');
        var b0 = coefficients.select('constant');

        var modndvisg30_adjust = modndvisg30_match.map(function (img) {
            var newimg = img.multiply(b1).add(b0);
            return newimg;
        });

        var combine_lm = modndvisg30_adjust.merge(ee.ImageCollection(landsatndvi_list));
        var combine_lm_list = combine_lm.toList(l_size.add(m_size));
        var modndvisg30_adjust_list = combine_lm_list.slice(0, m_size);
        var landsatndvi_list0 = combine_lm_list.slice(m_size, l_size.add(m_size));

        //filling the missing values with the corresponding values of modndvisg30_adjust
        var list_process = ee.List.sequence(0, m_size.subtract(1));
        var syn_series_list = list_process.map(function (i) {
            i = ee.Number(i);
            var modimg = ee.Image(modndvisg30_adjust_list.get(i));
            var flag = lm_loc.indexOf(i);
            var imgd = ee.Image(modndvi_list.get(i));
            var landsatimg = ee.Image(landsatndvi_list0.get(flag));
            var landsatmask0 = ee.Image(landsatmask_list.get(flag));
            var validone = ee.Number(1).subtract(list_loc.get(i));
            var validimg = ee.Image.constant(validone);
            var landsatmask = landsatmask0.multiply(validimg);
            var relocimg = landsatimg.multiply(landsatmask).add(modimg
                .subtract(modimg.multiply(landsatmask)));
            return relocimg.rename('MOD_NDVI_INTER')
                .set('system:time_start', imgd.get('system:time_start'));
        });


        var syn_series = ee.ImageCollection(syn_series_list);

        //Reduce the residual noise in the synthesized NDVI time series by the weighted SG filter
        var list_trend_sgCoeff = ee.List([-0.070588261, -0.011764720, 0.038009040, 0.078733027, 0.11040724, 0.13303168, 0.14660634,
            0.15113123, 0.14660634, 0.13303168, 0.11040724, 0.078733027, 0.038009040, -0.011764720, -0.070588261]);   //suggested trend parameter:(7,7,0,2)
        var list_sg_coeff = ee.List([0.034965038, -0.12820521, 0.069930017, 0.31468537, 0.41724950, 0.31468537,
            0.069930017, -0.12820521, 0.034965038]);   //suggested parameters of SG:(4,4,0,5)
        var syn_series_sg = sg_filter.sg_filter_chen(syn_series, list_trend_sgCoeff, list_sg_coeff);

        var syn_series_sg_int = syn_series_sg.map(function (img) {
            var constant = ee.Image.constant(10000);
            var newimg = img.multiply(constant).toInt16();
            return newimg
        });

        //print(syn_series_sg_int);

        //output procedure
        //choose date range (the whole (0,46) or part of time series (10,15)) to export
        var syn_series_sg_int_list = syn_series_sg_int.toList(46);
        var syn_series_sg_part_list = syn_series_sg_int_list.slice(0, 46); //the whole (0,46) or part of time series (10,15)->(DOY81-113)
        var syn_series_sg_part = ee.ImageCollection(syn_series_sg_part_list);

        var ndvi_mean = syn_series_sg_part.mean().set("system:time_start", ee.Date(begin_date))

        mean_ndvi_list = mean_ndvi_list.add(ndvi_mean)

    }

    var mean_ndvi_col = ee.ImageCollection(mean_ndvi_list)

    var img = mean_ndvi_col.max().toInt16().clip(testregion).rename('ndvi').set('system:time_start', ee.Date(begin_date));//每年网格内的年平均ndvi

    var description = (ee.String('mean_ndvi_').cat(begin_date.slice(0, 4)).cat('_').cat(ee.String(ee.Number(number_longtitude_min))).cat('_').cat(ee.String(ee.Number(number_latitude_max)))).getInfo()
    var assetId = (ee.String('Global_LPDI_ndvi').cat('/').cat(description)).getInfo()

    Export.image.toAsset({
        image: img,
        description: description,
        assetId: description,
        region: testregion,
        scale: 30,
        crs: 'EPSG:4326',
        maxPixels: 1e13
    })
    //Map.addLayer(img.select('ndvi'),visParam4,description)

}
