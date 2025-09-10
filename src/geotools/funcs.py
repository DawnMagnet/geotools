from typing import Any

import cv2
import numpy as np
from osgeo import gdal, osr

# Enable GDAL exceptions to handle errors properly
gdal.UseExceptions()


def transform_projected_to_geographic(
    geotransform: tuple,
    projection: str,
    width: int,
    height: int,
) -> dict[str, Any]:
    """将大地坐标系转换为经纬度坐标系

    Args:
        geotransform: GDAL地理变换参数
        projection: 投影坐标系WKT字符串
        width: 栅格宽度（像素）
        height: 栅格高度（像素）

    Returns:
        dict: 包含geographic_bounds和geographic_center的字典
              geographic_bounds: {"west": float, "east": float, "south": float, "north": float}
              geographic_center: {"longitude": float, "latitude": float}
              如果转换失败，两个值都为None

    """
    geographic_bounds = None
    geographic_center = None

    try:
        # 创建坐标转换
        source_srs = osr.SpatialReference()
        source_srs.ImportFromWkt(projection)

        target_srs = osr.SpatialReference()
        target_srs.ImportFromEPSG(4326)  # WGS84经纬度坐标系

        transform = osr.CoordinateTransformation(source_srs, target_srs)

        # 计算影像四个角点的投影坐标
        corners_pixel = [
            (0, 0),  # 左上角
            (width, 0),  # 右上角
            (width, height),  # 右下角
            (0, height),  # 左下角
        ]

        # 转换为投影坐标，然后转换为地理坐标
        corners_geo = []
        for px, py in corners_pixel:
            # 像素坐标转投影坐标
            proj_x = geotransform[0] + px * geotransform[1] + py * geotransform[2]
            proj_y = geotransform[3] + px * geotransform[4] + py * geotransform[5]

            # 投影坐标转地理坐标
            lon, lat, _ = transform.TransformPoint(proj_x, proj_y)
            corners_geo.append((lon, lat))

        # 计算边界范围
        lons = [corner[0] for corner in corners_geo]
        lats = [corner[1] for corner in corners_geo]

        geographic_bounds = {
            "west": min(lons),
            "east": max(lons),
            "south": min(lats),
            "north": max(lats),
        }

        # 计算中心点
        center_x = (
            geotransform[0] + width / 2 * geotransform[1] + height / 2 * geotransform[2]
        )
        center_y = (
            geotransform[3] + width / 2 * geotransform[4] + height / 2 * geotransform[5]
        )
        center_lon, center_lat, _ = transform.TransformPoint(center_x, center_y)

        geographic_center = {"longitude": center_lon, "latitude": center_lat}

    except Exception:
        # 如果转换失败，保持None值
        pass

    return {
        "geographic_bounds": geographic_bounds,
        "geographic_center": geographic_center,
    }


def transform_point_to_geographic(
    x: float,
    y: float,
    projection: str,
) -> tuple[float, float] | None:
    """将单个投影坐标点转换为经纬度坐标

    Args:
        x: 投影坐标X值
        y: 投影坐标Y值
        projection: 投影坐标系WKT字符串

    Returns:
        tuple: (经度, 纬度) 或 None（如果转换失败）

    """
    try:
        source_srs = osr.SpatialReference()
        source_srs.ImportFromWkt(projection)

        target_srs = osr.SpatialReference()
        target_srs.ImportFromEPSG(4326)  # WGS84经纬度坐标系

        transform = osr.CoordinateTransformation(source_srs, target_srs)
        lon, lat, _ = transform.TransformPoint(x, y)

        return (lon, lat)
    except Exception:
        return None


def projected_to_geographic(
    x: float, y: float, dataset: gdal.Dataset
) -> tuple[float, float] | None:
    """将大地坐标转换为经纬度坐标（简化版本）

    Args:
        x: 投影坐标X值（大地坐标）
        y: 投影坐标Y值（大地坐标）
        dataset: GDAL数据集对象

    Returns:
        tuple: (经度, 纬度) 或 None（如果转换失败）

    """
    projection = dataset.GetProjection()
    if not projection:
        return None
    return transform_point_to_geographic(x, y, projection)


def xy_to_lonlat(x: float, y: float, proj_wkt: str) -> tuple[float, float]:
    """将大地坐标（x, y）转换为经纬度（经度, 纬度）

    这是一个简化的函数，输入两个数字（大地坐标），返回两个数字（经纬度）

    Args:
        x: 大地坐标X值
        y: 大地坐标Y值
        proj_wkt: 投影坐标系的WKT字符串

    Returns:
        tuple: (经度, 纬度)

    Raises:
        RuntimeError: 如果坐标转换失败

    """
    result = transform_point_to_geographic(x, y, proj_wkt)
    if result is None:
        raise RuntimeError(f"无法转换坐标 ({x}, {y}) 到经纬度")
    return result


def gray_process(
    gray: np.ndarray | list,
    truncated_value: float = 1,
    max_out: int = 255,
    min_out: int = 0,
) -> np.ndarray:
    """对单通道灰度影像做截断式直方图拉伸

    使用更加高效的numpy向量化操作和自适应处理
    """
    # 转换为numpy数组并确保是浮点类型便于计算
    gray_array: np.ndarray = np.asarray(gray, dtype=np.float64)

    # 高效检查是否为全零或常数数组
    if not np.any(gray_array) or np.allclose(gray_array, gray_array.flat[0]):
        return np.full_like(gray_array, min_out, dtype=np.uint8)

    # 使用numpy的向量化操作计算百分位数
    lo: float
    hi: float
    lo, hi = np.percentile(gray_array, [truncated_value, 100 - truncated_value])

    # 避免除零错误的自适应处理
    if np.isclose(hi, lo):
        return np.full_like(gray_array, (max_out + min_out) // 2, dtype=np.uint8)

    # 使用numpy链式操作进行线性变换和裁剪
    return np.clip(
        (gray_array - lo) / (hi - lo) * (max_out - min_out) + min_out, min_out, max_out
    ).astype(np.uint8)


def tiff2png(
    input_tif: str,
    output_png: str,
    truncated_value: float = 1,
    downsample: int = 1,
) -> str:
    """将tiff通过量化转换为png，并支持降采样。

    Args:
        input_tif: 输入tiff路径
        output_png: 输出png路径
        truncated_value: 量化截断百分比
        downsample: 降采样倍数（>1时缩小）

    """
    ds: gdal.Dataset | None = gdal.Open(input_tif)
    if ds is None:
        raise RuntimeError(f"无法打开文件: {input_tif}")
    arr: np.ndarray | None = ds.ReadAsArray()
    if arr is None:
        raise RuntimeError(f"无法读取文件数据: {input_tif}")
    # 如果是多波段，取第一个波段
    if arr.ndim == 3:
        arr = arr[0]
    img: np.ndarray = gray_process(arr, truncated_value=truncated_value)  # type: ignore
    # 降采样处理
    if downsample > 1:
        new_h = img.shape[0] // downsample
        new_w = img.shape[1] // downsample
        img = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
    cv2.imwrite(output_png, img)
    ds = None  # type: ignore
    return output_png


def cutiff(
    input_tif: str, output_tif: str, xoff: int, yoff: int, xsize: int, ysize: int
) -> str:
    """给定坐标裁切tiff。

    Args:
        input_tif: 输入tiff路径
        output_tif: 输出tiff路径
        xoff: 裁切起始x坐标
        yoff: 裁切起始y坐标
        xsize: 裁切宽度
        ysize: 裁切高度

    """
    ds: gdal.Dataset | None = gdal.Open(input_tif)
    if ds is None:
        raise RuntimeError(f"无法打开文件: {input_tif}")
    cut_ds: np.ndarray | None = ds.ReadAsArray(xoff, yoff, xsize, ysize)
    if cut_ds is None:
        raise RuntimeError(f"无法读取文件数据: {input_tif}")
    geotransform: tuple = ds.GetGeoTransform()
    projection: str = ds.GetProjection()
    new_geotransform: tuple = (
        geotransform[0] + xoff * geotransform[1],
        geotransform[1],
        geotransform[2],
        geotransform[3] + yoff * geotransform[5],
        geotransform[4],
        geotransform[5],
    )
    driver: gdal.Driver = gdal.GetDriverByName("GTiff")
    out_ds: gdal.Dataset = driver.Create(
        output_tif, xsize, ysize, ds.RasterCount, ds.GetRasterBand(1).DataType
    )
    if ds.RasterCount == 1:
        out_ds.GetRasterBand(1).WriteArray(cut_ds)
    else:
        for i in range(ds.RasterCount):
            out_ds.GetRasterBand(i + 1).WriteArray(cut_ds[i])
    out_ds.SetGeoTransform(new_geotransform)
    out_ds.SetProjection(projection)
    out_ds.FlushCache()
    out_ds = None  # type: ignore
    ds = None  # type: ignore
    return output_tif


def tiffinfo(input_tif: str) -> dict[str, Any]:
    """查看详细tiff图信息。

    Args:
        input_tif: 输入tiff路径

    Returns:
        dict: 详细图像信息

    """
    import datetime
    import os

    from osgeo import osr

    ds: gdal.Dataset = gdal.Open(input_tif)
    if ds is None:
        raise RuntimeError(f"无法打开文件: {input_tif}")

    # 获取第一个波段进行统计分析
    band = ds.GetRasterBand(1)
    stats = band.GetStatistics(True, True)

    # 获取文件系统信息
    file_stat = os.stat(input_tif)

    # 获取地理坐标转换信息
    geotransform = ds.GetGeoTransform()
    projection = ds.GetProjection()

    # 转换为经纬度坐标
    geographic_bounds = None
    geographic_center = None

    if projection:
        try:
            # 创建坐标转换
            source_srs = osr.SpatialReference()
            source_srs.ImportFromWkt(projection)

            target_srs = osr.SpatialReference()
            target_srs.ImportFromEPSG(4326)  # WGS84经纬度坐标系

            transform = osr.CoordinateTransformation(source_srs, target_srs)

            # 计算影像四个角点的投影坐标
            width = ds.RasterXSize
            height = ds.RasterYSize

            # 四个角点的像素坐标
            corners_pixel = [
                (0, 0),  # 左上角
                (width, 0),  # 右上角
                (width, height),  # 右下角
                (0, height),  # 左下角
            ]

            # 转换为投影坐标，然后转换为地理坐标
            corners_geo = []
            for px, py in corners_pixel:
                # 像素坐标转投影坐标
                proj_x = geotransform[0] + px * geotransform[1] + py * geotransform[2]
                proj_y = geotransform[3] + px * geotransform[4] + py * geotransform[5]

                # 投影坐标转地理坐标
                lon, lat, _ = transform.TransformPoint(proj_x, proj_y)
                corners_geo.append((lon, lat))

            # 计算边界范围
            lons = [corner[0] for corner in corners_geo]
            lats = [corner[1] for corner in corners_geo]

            geographic_bounds = {
                "west": min(lons),
                "east": max(lons),
                "south": min(lats),
                "north": max(lats),
            }

            # 计算中心点
            center_x = (
                geotransform[0]
                + width / 2 * geotransform[1]
                + height / 2 * geotransform[2]
            )
            center_y = (
                geotransform[3]
                + width / 2 * geotransform[4]
                + height / 2 * geotransform[5]
            )
            center_lon, center_lat, _ = transform.TransformPoint(center_x, center_y)

            geographic_center = {"longitude": center_lon, "latitude": center_lat}

        except Exception:
            # 如果转换失败，保持None值
            pass

    info: dict[str, Any] = {
        # 基本信息
        "FilePath": input_tif,
        "FileName": os.path.basename(input_tif),
        "FileSize": file_stat.st_size,
        "CreationTime": datetime.datetime.fromtimestamp(file_stat.st_ctime),
        "ModificationTime": datetime.datetime.fromtimestamp(file_stat.st_mtime),
        # 栅格信息
        "RasterXSize": ds.RasterXSize,
        "RasterYSize": ds.RasterYSize,
        "RasterCount": ds.RasterCount,
        "DataType": ds.GetRasterBand(1).DataType,
        # 地理信息
        "GeoTransform": geotransform,
        "Projection": projection,
        # 地理坐标边界（经纬度）
        "GeographicBounds": geographic_bounds,
        "GeographicCenter": geographic_center,
        # 驱动信息
        "DriverShortName": ds.GetDriver().ShortName,
        "DriverLongName": ds.GetDriver().LongName,
        # 统计信息 (第一波段)
        "MinValue": stats[0] if stats else None,
        "MaxValue": stats[1] if stats else None,
        "MeanValue": stats[2] if stats else None,
        "StdDev": stats[3] if stats else None,
        # 波段信息
        "BandInfo": [],
        # 元数据
        "Metadata": ds.GetMetadata(),
    }

    # 获取每个波段的详细信息
    for i in range(1, ds.RasterCount + 1):
        band = ds.GetRasterBand(i)
        band_stats = band.GetStatistics(True, True)
        band_info = {
            "BandNumber": i,
            "DataType": band.DataType,
            "MinValue": band_stats[0] if band_stats else None,
            "MaxValue": band_stats[1] if band_stats else None,
            "MeanValue": band_stats[2] if band_stats else None,
            "StdDev": band_stats[3] if band_stats else None,
            "NoDataValue": band.GetNoDataValue(),
            "ColorInterpretation": band.GetColorInterpretation(),
            "Metadata": band.GetMetadata(),
        }
        info["BandInfo"].append(band_info)

    ds = None  # type: ignore
    return info
