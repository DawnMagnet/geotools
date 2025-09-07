from typing import Any

import cv2
import numpy as np
from osgeo import gdal

# Enable GDAL exceptions to handle errors properly
gdal.UseExceptions()


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


def tiff2png(input_tif: str, output_png: str, truncated_value: float = 1) -> str:
    """将tiff通过量化转换为png。

    Args:
        input_tif: 输入tiff路径
        output_png: 输出png路径
        truncated_value: 量化截断百分比

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
    import os
    import datetime
    ds: gdal.Dataset = gdal.Open(input_tif)
    if ds is None:
        raise RuntimeError(f"无法打开文件: {input_tif}")

    # 获取第一个波段进行统计分析
    band = ds.GetRasterBand(1)
    stats = band.GetStatistics(True, True)

    # 获取文件系统信息
    file_stat = os.stat(input_tif)

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
        "GeoTransform": ds.GetGeoTransform(),
        "Projection": ds.GetProjection(),

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
