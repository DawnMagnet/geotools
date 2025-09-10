"""
GeoTools 核心功能模块

提供 TIFF 图像处理、坐标转换、数据分析等地理信息处理功能。
采用高内聚、低耦合的设计原则，将通用功能提取为独立的工具函数。

主要功能模块：
1. 坐标转换模块：投影坐标与地理坐标之间的转换
2. 图像处理模块：TIFF 文件的读取、处理、转换
3. 信息提取模块：TIFF 文件的元数据分析和统计
4. 显示工具模块：格式化输出和可视化显示

作者: GeoTools Team
版本: 2.0
"""

from typing import Any, Dict, List, Optional, Tuple, Union
import datetime
import math
import os
import re

import cv2
import numpy as np
from osgeo import gdal, osr

# Enable GDAL exceptions to handle errors properly
gdal.UseExceptions()


# ===========================
# 核心工具函数模块
# ===========================

def _safe_open_dataset(file_path: str) -> gdal.Dataset:
    """安全打开GDAL数据集

    Args:
        file_path: 文件路径

    Returns:
        gdal.Dataset: GDAL数据集对象

    Raises:
        RuntimeError: 如果无法打开文件
    """
    dataset = gdal.Open(file_path)
    if dataset is None:
        raise RuntimeError(f"无法打开文件: {file_path}")
    return dataset


def _safe_read_array(dataset: gdal.Dataset, *args) -> np.ndarray:
    """安全读取数组数据

    Args:
        dataset: GDAL数据集对象
        *args: ReadAsArray的参数

    Returns:
        np.ndarray: 数组数据

    Raises:
        RuntimeError: 如果无法读取数据
    """
    array = dataset.ReadAsArray(*args)
    if array is None:
        raise RuntimeError(f"无法读取文件数据")
    return array


def _create_coordinate_transform(source_wkt: str, target_epsg: int = 4326) -> osr.CoordinateTransformation:
    """创建坐标转换对象

    Args:
        source_wkt: 源坐标系WKT字符串
        target_epsg: 目标坐标系EPSG代码，默认为4326(WGS84)

    Returns:
        osr.CoordinateTransformation: 坐标转换对象

    Raises:
        Exception: 如果无法创建坐标转换
    """
    source_srs = osr.SpatialReference()
    source_srs.ImportFromWkt(source_wkt)

    target_srs = osr.SpatialReference()
    target_srs.ImportFromEPSG(target_epsg)

    return osr.CoordinateTransformation(source_srs, target_srs)


def _transform_corners_to_geographic(
    geotransform: tuple,
    projection: str,
    width: int,
    height: int
) -> Tuple[List[Tuple[float, float]], Dict[str, float]]:
    """将图像四角点转换为地理坐标

    Args:
        geotransform: GDAL地理变换参数
        projection: 投影坐标系WKT字符串
        width: 图像宽度
        height: 图像高度

    Returns:
        tuple: (角点地理坐标列表, 边界字典)
    """
    transform = _create_coordinate_transform(projection)

    # 定义四个角点的像素坐标
    corners_pixel = [(0, 0), (width, 0), (width, height), (0, height)]

    # 转换为地理坐标
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

    bounds = {
        "west": min(lons),
        "east": max(lons),
        "south": min(lats),
        "north": max(lats),
    }

    return corners_geo, bounds


def _calculate_geographic_center(geotransform: tuple, projection: str, width: int, height: int) -> Dict[str, float]:
    """计算图像中心的地理坐标

    Args:
        geotransform: GDAL地理变换参数
        projection: 投影坐标系WKT字符串
        width: 图像宽度
        height: 图像高度

    Returns:
        dict: 包含longitude和latitude的字典
    """
    transform = _create_coordinate_transform(projection)

    # 计算中心点投影坐标
    center_x = geotransform[0] + width / 2 * geotransform[1] + height / 2 * geotransform[2]
    center_y = geotransform[3] + width / 2 * geotransform[4] + height / 2 * geotransform[5]

    # 转换为地理坐标
    center_lon, center_lat, _ = transform.TransformPoint(center_x, center_y)

    return {"longitude": center_lon, "latitude": center_lat}


def _get_band_statistics(band: gdal.Band) -> Dict[str, Optional[float]]:
    """获取波段统计信息

    Args:
        band: GDAL波段对象

    Returns:
        dict: 包含统计信息的字典
    """
    stats = band.GetStatistics(True, True)
    return {
        "MinValue": stats[0] if stats else None,
        "MaxValue": stats[1] if stats else None,
        "MeanValue": stats[2] if stats else None,
        "StdDev": stats[3] if stats else None,
    }


def _extract_projection_info(projection: str) -> Dict[str, Optional[str]]:
    """从投影WKT字符串中提取关键信息

    Args:
        projection: 投影坐标系WKT字符串

    Returns:
        dict: 包含投影信息的字典
    """
    info = {"projcs": None, "geogcs": None, "datum": None}

    if not projection:
        return info

    patterns = {
        "projcs": r'PROJCS\["([^"]+)"',
        "geogcs": r'GEOGCS\["([^"]+)"',
        "datum": r'DATUM\["([^"]+)"'
    }

    for key, pattern in patterns.items():
        match = re.search(pattern, projection)
        if match:
            info[key] = match.group(1)

    return info


def _calculate_file_compression_ratio(actual_size: Union[int, float], uncompressed_size: Union[int, float]) -> float:
    """计算文件压缩率

    Args:
        actual_size: 实际文件大小（字节）
        uncompressed_size: 未压缩数据大小（字节）

    Returns:
        float: 压缩率百分比
    """
    if uncompressed_size <= 0:
        return 0.0
    return (1 - actual_size / uncompressed_size) * 100


# ===========================
# 坐标转换模块
# ===========================

def transform_projected_to_geographic(
    geotransform: tuple,
    projection: str,
    width: int,
    height: int,
) -> Dict[str, Any]:
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
) -> Optional[Tuple[float, float]]:
    """将单个投影坐标点转换为经纬度坐标

    Args:
        x: 投影坐标X值
        y: 投影坐标Y值
        projection: 投影坐标系WKT字符串

    Returns:
        tuple: (经度, 纬度) 或 None（如果转换失败）
    """
    try:
        transform = _create_coordinate_transform(projection)
        lon, lat, _ = transform.TransformPoint(x, y)
        return (lon, lat)
    except Exception:
        return None


def projected_to_geographic(
    x: float, y: float, dataset: gdal.Dataset
) -> Optional[Tuple[float, float]]:
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


def xy_to_lonlat(x: float, y: float, proj_wkt: str) -> Tuple[float, float]:
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


# ===========================
# 图像处理模块
# ===========================


def gray_process(
    gray: Union[np.ndarray, list],
    truncated_value: float = 1,
    max_out: int = 255,
    min_out: int = 0,
) -> np.ndarray:
    """对单通道灰度影像做截断式直方图拉伸

    使用高效的numpy向量化操作和自适应处理

    Args:
        gray: 输入的灰度数组或列表
        truncated_value: 截断百分比，用于确定拉伸的上下限
        max_out: 输出的最大值
        min_out: 输出的最小值

    Returns:
        np.ndarray: 处理后的8位无符号整数数组
    """
    # 转换为numpy数组并确保是浮点类型便于计算
    gray_array = np.asarray(gray, dtype=np.float64)

    # 高效检查是否为全零或常数数组
    if not np.any(gray_array) or np.allclose(gray_array, gray_array.flat[0]):
        return np.full_like(gray_array, min_out, dtype=np.uint8)

    # 使用numpy的向量化操作计算百分位数
    lo, hi = np.percentile(gray_array, [truncated_value, 100 - truncated_value])

    # 避免除零错误的自适应处理
    if np.isclose(hi, lo):
        return np.full_like(gray_array, (max_out + min_out) // 2, dtype=np.uint8)

    # 使用numpy链式操作进行线性变换和裁剪
    return np.clip(
        (gray_array - lo) / (hi - lo) * (max_out - min_out) + min_out,
        min_out,
        max_out
    ).astype(np.uint8)


def tiff2png(
    input_tif: str,
    output_png: str,
    truncated_value: float = 1,
    downsample: int = 1,
) -> str:
    """将TIFF文件转换为PNG格式，支持降采样

    Args:
        input_tif: 输入TIFF文件路径
        output_png: 输出PNG文件路径
        truncated_value: 量化截断百分比
        downsample: 降采样倍数（>1时缩小图像）

    Returns:
        str: 输出PNG文件路径

    Raises:
        RuntimeError: 如果文件打开或读取失败
    """
    # 使用安全的文件打开和读取函数
    dataset = _safe_open_dataset(input_tif)
    array = _safe_read_array(dataset)

    # 如果是多波段，取第一个波段
    if array.ndim == 3:
        array = array[0]

    # 进行灰度处理
    processed_img = gray_process(array, truncated_value=truncated_value)

    # 降采样处理
    if downsample > 1:
        new_h = processed_img.shape[0] // downsample
        new_w = processed_img.shape[1] // downsample
        processed_img = cv2.resize(processed_img, (new_w, new_h), interpolation=cv2.INTER_AREA)

    # 保存图像
    cv2.imwrite(output_png, processed_img)
    dataset = None  # 释放资源

    return output_png


def cutiff(
    input_tif: str,
    output_tif: str,
    xoff: int,
    yoff: int,
    xsize: int,
    ysize: int
) -> str:
    """裁切TIFF文件到指定区域

    Args:
        input_tif: 输入TIFF文件路径
        output_tif: 输出TIFF文件路径
        xoff: 裁切起始X坐标（像素）
        yoff: 裁切起始Y坐标（像素）
        xsize: 裁切宽度（像素）
        ysize: 裁切高度（像素）

    Returns:
        str: 输出TIFF文件路径

    Raises:
        RuntimeError: 如果文件操作失败
    """
    # 安全打开和读取数据
    dataset = _safe_open_dataset(input_tif)
    cut_array = _safe_read_array(dataset, xoff, yoff, xsize, ysize)

    # 获取地理信息
    geotransform = dataset.GetGeoTransform()
    projection = dataset.GetProjection()

    # 计算新的地理变换参数
    new_geotransform = (
        geotransform[0] + xoff * geotransform[1],
        geotransform[1],
        geotransform[2],
        geotransform[3] + yoff * geotransform[5],
        geotransform[4],
        geotransform[5],
    )

    # 创建输出数据集
    driver = gdal.GetDriverByName("GTiff")
    output_dataset = driver.Create(
        output_tif, xsize, ysize, dataset.RasterCount, dataset.GetRasterBand(1).DataType
    )

    # 写入数据
    if dataset.RasterCount == 1:
        output_dataset.GetRasterBand(1).WriteArray(cut_array)
    else:
        for i in range(dataset.RasterCount):
            output_dataset.GetRasterBand(i + 1).WriteArray(cut_array[i])

    # 设置地理信息
    output_dataset.SetGeoTransform(new_geotransform)
    output_dataset.SetProjection(projection)
    output_dataset.FlushCache()

    # 释放资源
    output_dataset = None
    dataset = None

    return output_tif


# ===========================
# 数据分析模块
# ===========================

def _get_datatype_info(datatype: int) -> Tuple[str, str, str, str]:
    """获取GDAL数据类型的详细信息

    Args:
        datatype: GDAL数据类型编号

    Returns:
        tuple: (类型名称, 描述, 数值范围, 内存占用)
    """
    datatype_map = {
        1: ("Byte", "8位无符号整数", "0-255", "1 byte/pixel"),
        2: ("UInt16", "16位无符号整数", "0-65,535", "2 bytes/pixel"),
        3: ("Int16", "16位有符号整数", "-32,768 到 32,767", "2 bytes/pixel"),
        4: ("UInt32", "32位无符号整数", "0-4,294,967,295", "4 bytes/pixel"),
        5: ("Int32", "32位有符号整数", "-2,147,483,648 到 2,147,483,647", "4 bytes/pixel"),
        6: ("Float32", "32位浮点数", "IEEE 754 单精度", "4 bytes/pixel"),
        7: ("Float64", "64位浮点数", "IEEE 754 双精度", "8 bytes/pixel"),
        8: ("CInt16", "复数16位整数", "复数对", "4 bytes/pixel"),
        9: ("CInt32", "复数32位整数", "复数对", "8 bytes/pixel"),
        10: ("CFloat32", "复数32位浮点", "复数对", "8 bytes/pixel"),
        11: ("CFloat64", "复数64位浮点", "复数对", "16 bytes/pixel"),
    }
    return datatype_map.get(datatype, ("Unknown", "未知类型", "未知", "未知"))


def _get_bytes_per_pixel(datatype: int) -> int:
    """根据GDAL数据类型获取每像素字节数

    Args:
        datatype: GDAL数据类型编号

    Returns:
        int: 每像素字节数
    """
    bytes_map = {1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 8, 8: 4, 9: 8, 10: 8, 11: 16}
    return bytes_map.get(datatype, 1)


def _get_file_info(file_path: str) -> Optional[Dict[str, Any]]:
    """获取文件基本信息

    Args:
        file_path: 文件路径

    Returns:
        dict: 文件信息字典，如果文件不存在返回None
    """
    if not os.path.exists(file_path):
        return None

    stat = os.stat(file_path)
    return {
        "size_bytes": stat.st_size,
        "size_mb": stat.st_size / (1024 * 1024),
        "created_time": datetime.datetime.fromtimestamp(stat.st_ctime),
        "modified_time": datetime.datetime.fromtimestamp(stat.st_mtime),
        "accessed_time": datetime.datetime.fromtimestamp(stat.st_atime),
    }


def tiffinfo(input_tif: str) -> Dict[str, Any]:
    """查看详细TIFF图像信息

    使用重构后的辅助函数，提高代码复用性和可维护性

    Args:
        input_tif: 输入TIFF文件路径

    Returns:
        dict: 详细图像信息字典

    Raises:
        RuntimeError: 如果无法打开文件
    """
    # 安全打开数据集
    dataset = _safe_open_dataset(input_tif)

    # 获取基本统计信息
    first_band = dataset.GetRasterBand(1)
    first_band_stats = _get_band_statistics(first_band)

    # 获取文件系统信息
    file_stat = os.stat(input_tif)

    # 获取地理坐标转换信息
    geotransform = dataset.GetGeoTransform()
    projection = dataset.GetProjection()

    # 转换为经纬度坐标
    geographic_result = transform_projected_to_geographic(
        geotransform, projection, dataset.RasterXSize, dataset.RasterYSize
    )

    # 构建基本信息字典
    info: Dict[str, Any] = {
        # 基本信息
        "FilePath": input_tif,
        "FileName": os.path.basename(input_tif),
        "FileSize": file_stat.st_size,
        "CreationTime": datetime.datetime.fromtimestamp(file_stat.st_ctime),
        "ModificationTime": datetime.datetime.fromtimestamp(file_stat.st_mtime),
        # 栅格信息
        "RasterXSize": dataset.RasterXSize,
        "RasterYSize": dataset.RasterYSize,
        "RasterCount": dataset.RasterCount,
        "DataType": first_band.DataType,
        # 地理信息
        "GeoTransform": geotransform,
        "Projection": projection,
        # 地理坐标边界（经纬度）
        "GeographicBounds": geographic_result["geographic_bounds"],
        "GeographicCenter": geographic_result["geographic_center"],
        # 驱动信息
        "DriverShortName": dataset.GetDriver().ShortName,
        "DriverLongName": dataset.GetDriver().LongName,
        # 统计信息 (第一波段)
        "MinValue": first_band_stats["MinValue"],
        "MaxValue": first_band_stats["MaxValue"],
        "MeanValue": first_band_stats["MeanValue"],
        "StdDev": first_band_stats["StdDev"],
        # 波段信息
        "BandInfo": [],
        # 元数据
        "Metadata": dataset.GetMetadata(),
    }

    # 获取每个波段的详细信息
    for i in range(1, dataset.RasterCount + 1):
        band = dataset.GetRasterBand(i)
        band_stats = _get_band_statistics(band)
        band_info = {
            "BandNumber": i,
            "DataType": band.DataType,
            "MinValue": band_stats["MinValue"],
            "MaxValue": band_stats["MaxValue"],
            "MeanValue": band_stats["MeanValue"],
            "StdDev": band_stats["StdDev"],
            "NoDataValue": band.GetNoDataValue(),
            "ColorInterpretation": band.GetColorInterpretation(),
            "Metadata": band.GetMetadata(),
        }
        band_info_list = info["BandInfo"]
        if isinstance(band_info_list, list):
            band_info_list.append(band_info)

    dataset = None  # 释放资源
    return info


def format_coordinate_bounds(bounds: Optional[Dict[str, float]]) -> Optional[str]:
    """格式化地理边界信息

    Args:
        bounds: 包含west, east, south, north的字典

    Returns:
        str: 格式化后的边界字符串
    """
    if not bounds:
        return None

    west_dir = "W" if bounds["west"] < 0 else "E"
    east_dir = "E" if bounds["east"] >= 0 else "W"
    south_dir = "S" if bounds["south"] < 0 else "N"
    north_dir = "N" if bounds["north"] >= 0 else "S"

    return f"{abs(bounds['west']):.6f}°{west_dir} - {abs(bounds['east']):.6f}°{east_dir}, {abs(bounds['south']):.6f}°{south_dir} - {abs(bounds['north']):.6f}°{north_dir}"


def format_coordinate_center(center: Optional[Dict[str, float]]) -> Optional[str]:
    """格式化地理中心点信息

    Args:
        center: 包含longitude, latitude的字典

    Returns:
        str: 格式化后的中心点字符串
    """
    if not center:
        return None

    lon_dir = "E" if center["longitude"] >= 0 else "W"
    lat_dir = "N" if center["latitude"] >= 0 else "S"

    return f"{abs(center['longitude']):.6f}°{lon_dir}, {abs(center['latitude']):.6f}°{lat_dir}"


def calculate_projected_distance_and_area(info: Dict[str, Any]) -> Dict[str, Any]:
    """基于投影坐标系计算距离和面积

    Args:
        info: TIFF信息字典，包含投影和地理变换信息

    Returns:
        dict: 包含距离和面积信息的字典
    """
    geotransform = info.get("GeoTransform")
    projection = info.get("Projection")

    if not geotransform or not projection:
        return {
            "x_span_km": None,
            "y_span_km": None,
            "area_km2": None,
            "unit_name": "未知单位",
        }

    # 计算投影坐标系的范围
    width = info["RasterXSize"]
    height = info["RasterYSize"]

    min_x = geotransform[0]
    max_x = min_x + width * geotransform[1]
    min_y = geotransform[3] + height * geotransform[5]
    max_y = geotransform[3]

    x_span = abs(max_x - min_x)
    y_span = abs(max_y - min_y)
    area = x_span * y_span

    # 获取投影坐标系的单位信息
    try:
        srs = osr.SpatialReference()
        srs.ImportFromWkt(projection)

        # 获取线性单位
        unit_name = srs.GetLinearUnitsName()
        unit_to_meter = srs.GetLinearUnits()

        # 将距离和面积转换为千米和平方千米
        x_span_km = (x_span * unit_to_meter) / 1000.0
        y_span_km = (y_span * unit_to_meter) / 1000.0
        area_km2 = (area * unit_to_meter * unit_to_meter) / 1000000.0

        return {
            "x_span_km": x_span_km,
            "y_span_km": y_span_km,
            "area_km2": area_km2,
            "unit_name": unit_name or "未知单位",
            "unit_to_meter": unit_to_meter,
            "x_span_original": x_span,
            "y_span_original": y_span,
            "area_original": area,
        }

    except Exception:
        # 如果无法获取单位信息，使用地理坐标计算近似值
        if info.get("GeographicBounds"):
            bounds = info["GeographicBounds"]
            # 使用地理坐标的近似距离计算（赤道附近1度约等于111km）
            lat_center = (bounds["north"] + bounds["south"]) / 2
            lon_span = abs(bounds["east"] - bounds["west"])
            lat_span = abs(bounds["north"] - bounds["south"])

            # 考虑纬度的cos修正
            cos_lat = math.cos(math.radians(lat_center))
            x_span_km = lon_span * 111.0 * cos_lat
            y_span_km = lat_span * 111.0
            area_km2 = x_span_km * y_span_km

            return {
                "x_span_km": x_span_km,
                "y_span_km": y_span_km,
                "area_km2": area_km2,
                "unit_name": "度 (近似计算)",
                "unit_to_meter": None,
                "x_span_original": x_span,
                "y_span_original": y_span,
                "area_original": area,
            }

        return {
            "x_span_km": None,
            "y_span_km": None,
            "area_km2": None,
            "unit_name": "未知单位",
        }


def get_file_info(file_path: str) -> Optional[Dict[str, Any]]:
    """获取文件基本信息

    Args:
        file_path: 文件路径

    Returns:
        dict: 文件信息字典，如果文件不存在返回None
    """
    return _get_file_info(file_path)


def analyze_tiff_comprehensive(file_path: str) -> Dict[str, Any]:
    """综合分析TIFF文件，返回结构化数据

    Args:
        file_path: TIFF文件路径

    Returns:
        dict: 包含完整分析结果的字典
    """
    # 获取文件基本信息
    file_info = get_file_info(file_path)

    # 获取TIFF信息
    tiff_info = tiffinfo(file_path)

    # 计算距离面积信息
    distance_area = calculate_projected_distance_and_area(tiff_info)

    # 格式化坐标信息
    bounds_str = format_coordinate_bounds(tiff_info.get("GeographicBounds"))
    center_str = format_coordinate_center(tiff_info.get("GeographicCenter"))

    # 计算总像素数
    total_pixels = tiff_info["RasterXSize"] * tiff_info["RasterYSize"]

    # 计算纵横比
    aspect_ratio = tiff_info["RasterXSize"] / tiff_info["RasterYSize"]

    # 获取数据类型信息
    datatype_info = _get_datatype_info(tiff_info["DataType"])

    # 计算内存占用
    bpp = _get_bytes_per_pixel(tiff_info["DataType"])
    uncompressed_size = total_pixels * tiff_info["RasterCount"] * bpp

    compression_ratio = 0.0
    if file_info and uncompressed_size > 0:
        compression_ratio = _calculate_file_compression_ratio(file_info["size_bytes"], uncompressed_size)

    return {
        "file_info": file_info,
        "tiff_info": tiff_info,
        "distance_area": distance_area,
        "coordinates": {
            "bounds_str": bounds_str,
            "center_str": center_str,
        },
        "analysis": {
            "total_pixels": total_pixels,
            "aspect_ratio": aspect_ratio,
            "aspect_type": "横版"
            if aspect_ratio > 1
            else ("竖版" if aspect_ratio < 1 else "正方形"),
            "datatype_info": datatype_info,
            "uncompressed_size": uncompressed_size,
            "compression_ratio": compression_ratio,
        },
    }


# ===========================
# 显示工具模块
# ===========================


def process_tiff_conversion(
    input_path: str,
    output_path: str,
    truncated_value: float = 1,
    downsample: int = 1
) -> Dict[str, Any]:
    """处理TIFF转换为PNG的完整流程

    Args:
        input_path: 输入TIFF文件路径
        output_path: 输出PNG文件路径
        truncated_value: 量化截断百分比
        downsample: 降采样倍数

    Returns:
        dict: 包含处理结果的详细信息
    """
    start_time = datetime.datetime.now()

    # 获取输入文件信息
    input_analysis = analyze_tiff_comprehensive(input_path)

    # 执行转换
    result_path = tiff2png(input_path, output_path, truncated_value, downsample)

    end_time = datetime.datetime.now()
    processing_time = (end_time - start_time).total_seconds()

    # 分析输出文件
    output_info = _get_file_info(result_path) if os.path.exists(result_path) else None

    png_info = None
    if os.path.exists(result_path):
        try:
            from PIL import Image
            with Image.open(result_path) as img:
                png_info = {
                    "size": img.size,
                    "mode": img.mode,
                    "format": img.format,
                    "info": getattr(img, "info", {}),
                }
        except Exception as e:
            png_info = {"error": str(e)}

    # 计算压缩率
    compression_ratio = 0.0
    if (input_analysis and input_analysis["file_info"] and output_info and
        isinstance(input_analysis["file_info"]["size_bytes"], (int, float)) and
        isinstance(output_info["size_bytes"], (int, float))):
        compression_ratio = _calculate_file_compression_ratio(
            output_info["size_bytes"],
            input_analysis["file_info"]["size_bytes"]
        )

    return {
        "input_analysis": input_analysis,
        "output_path": result_path,
        "output_info": output_info,
        "png_info": png_info,
        "processing_time": processing_time,
        "compression_ratio": compression_ratio,
        "parameters": {
            "truncated_value": truncated_value,
            "downsample": downsample,
        },
    }


def process_tiff_cropping(
    input_path: str,
    output_path: str,
    xoff: int,
    yoff: int,
    xsize: int,
    ysize: int
) -> Dict[str, Any]:
    """处理TIFF裁切的完整流程

    Args:
        input_path: 输入TIFF文件路径
        output_path: 输出TIFF文件路径
        xoff: 裁切起始X坐标
        yoff: 裁切起始Y坐标
        xsize: 裁切宽度
        ysize: 裁切高度

    Returns:
        dict: 包含裁切结果的详细信息
    """
    start_time = datetime.datetime.now()

    # 获取输入文件信息
    input_analysis = analyze_tiff_comprehensive(input_path)

    # 验证裁切范围
    crop_validation: Dict[str, Union[bool, List[str]]] = {"valid": True, "errors": [], "warnings": []}

    if input_analysis and input_analysis.get("tiff_info") and input_analysis["tiff_info"]:
        tiff_info = input_analysis["tiff_info"]
        raster_x_size = tiff_info.get("RasterXSize") if isinstance(tiff_info, dict) else None
        raster_y_size = tiff_info.get("RasterYSize") if isinstance(tiff_info, dict) else None

        if isinstance(raster_x_size, int) and xoff + xsize > raster_x_size:
            crop_validation["valid"] = False
            errors = crop_validation["errors"]
            if isinstance(errors, list):
                errors.append("X轴裁切范围超出图像边界")

        if isinstance(raster_y_size, int) and yoff + ysize > raster_y_size:
            crop_validation["valid"] = False
            errors = crop_validation["errors"]
            if isinstance(errors, list):
                errors.append("Y轴裁切范围超出图像边界")

    # 计算裁切比例
    crop_pixels = xsize * ysize
    crop_ratio = 0.0
    if input_analysis and input_analysis.get("analysis") and input_analysis["analysis"]:
        analysis_data = input_analysis["analysis"]
        original_pixels = analysis_data.get("total_pixels") if isinstance(analysis_data, dict) else None
        if isinstance(original_pixels, (int, float)) and original_pixels > 0:
            crop_ratio = (crop_pixels / original_pixels) * 100

    # 执行裁切
    result_path = None
    if crop_validation["valid"]:
        result_path = cutiff(input_path, output_path, xoff, yoff, xsize, ysize)

    end_time = datetime.datetime.now()
    processing_time = (end_time - start_time).total_seconds()

    # 分析输出文件
    output_analysis = None
    if result_path and os.path.exists(result_path):
        output_analysis = analyze_tiff_comprehensive(result_path)

    # 计算处理效率
    pixels_per_second = crop_pixels / processing_time if processing_time > 0 else 0
    mb_per_second = 0.0
    if (output_analysis and output_analysis.get("file_info") and output_analysis["file_info"] and
        isinstance(output_analysis["file_info"], dict) and
        isinstance(output_analysis["file_info"].get("size_mb"), (int, float)) and
        processing_time > 0):
        size_mb = output_analysis["file_info"]["size_mb"]
        if isinstance(size_mb, (int, float)):
            mb_per_second = size_mb / processing_time

    return {
        "input_analysis": input_analysis,
        "output_path": result_path,
        "output_analysis": output_analysis,
        "crop_validation": crop_validation,
        "crop_info": {
            "xoff": xoff,
            "yoff": yoff,
            "xsize": xsize,
            "ysize": ysize,
            "crop_pixels": crop_pixels,
            "crop_ratio": crop_ratio,
        },
        "processing_time": processing_time,
        "performance": {
            "pixels_per_second": pixels_per_second,
            "mb_per_second": mb_per_second,
        },
    }


def create_display_functions():
    """创建显示函数模块"""
    from rich.console import Console

    console = Console()

    def display_tiff_basic_info(info):
        """显示TIFF文件基本信息"""
        console.print(
            "\n================ TIFF 信息 =================",
            style="bright_yellow bold",
        )
        emoji_map = {
            "RasterXSize": "🟦",
            "RasterYSize": "🟩",
            "RasterCount": "📊",
            "DataType": "🔢",
            "GeoTransform": "🧭",
            "Projection": "🌐",
            "GeographicBounds": "🗺️",
            "GeographicCenter": "📍",
        }

        for k, v in info.items():
            emoji = emoji_map.get(k, "➡️")
            if k in ["RasterXSize", "RasterYSize"]:
                color = "bright_cyan"
            elif k == "RasterCount":
                color = "bright_magenta"
            elif k == "DataType":
                color = "bright_green"
            elif k == "GeoTransform":
                color = "bright_blue"
            elif k == "Projection":
                color = "bright_yellow"
            elif k in ["GeographicBounds", "GeographicCenter"]:
                color = "bright_magenta"
            else:
                color = "white"

            console.print(f"{emoji} {k:14}: ", style=f"{color} bold", end="")

            if k == "Projection":
                _display_projection_info(console, str(v))
            elif k == "DataType":
                _display_datatype_info(console, int(v))
            elif k == "GeoTransform":
                _display_geotransform_info(console, eval(str(v)))
            elif k == "GeographicBounds":
                _display_bounds_info(console, v)
            elif k == "GeographicCenter":
                _display_center_info(console, v)
            else:
                console.print(f"{v}", style="white")

        console.print(
            "============================================\n",
            style="bright_yellow bold",
        )

    def _display_projection_info(console, proj):
        """显示投影信息"""
        keywords = [
            "PROJCS",
            "GEOGCS",
            "DATUM",
            "SPHEROID",
            "PRIMEM",
            "PROJECTION",
            "PARAMETER",
            "UNIT",
            "AXIS",
            "AUTHORITY",
        ]
        i = 0
        while i < len(proj):
            next_keyword = None
            next_pos = len(proj)

            for keyword in keywords:
                pos = proj.find(keyword, i)
                if pos != -1 and pos < next_pos:
                    next_pos = pos
                    next_keyword = keyword

            bracket_pos = min(
                [pos for pos in [proj.find("[", i), proj.find("]", i)] if pos != -1]
                + [len(proj)]
            )

            if bracket_pos < next_pos:
                if bracket_pos > i:
                    console.print(proj[i:bracket_pos], end="")
                console.print(proj[bracket_pos], style="bright_cyan", end="")
                i = bracket_pos + 1
            elif next_keyword:
                if next_pos > i:
                    console.print(proj[i:next_pos], end="")
                console.print(next_keyword, style="bright_yellow bold", end="")
                i = next_pos + len(next_keyword)
            else:
                console.print(proj[i:], end="")
                break
        console.print("")

    def _display_datatype_info(console, datatype):
        """显示数据类型信息"""
        datatype_map = {
            0: "Unknown (未知)",
            1: "Byte (8-bit unsigned integer, 无符号8位整数)",
            2: "UInt16 (16-bit unsigned integer, 无符号16位整数)",
            3: "Int16 (16-bit signed integer, 有符号16位整数)",
            4: "UInt32 (32-bit unsigned integer, 无符号32位整数)",
            5: "Int32 (32-bit signed integer, 有符号32位整数)",
            6: "Float32 (32-bit floating point, 32位浮点数)",
            7: "Float64 (64-bit floating point, 64位浮点数)",
            8: "CInt16 (Complex Int16, 复数16位整数)",
            9: "CInt32 (Complex Int32, 复数32位整数)",
            10: "CFloat32 (Complex Float32, 复数32位浮点数)",
            11: "CFloat64 (Complex Float64, 复数64位浮点数)",
        }
        datatype_desc = datatype_map.get(datatype, f"Unknown type {datatype}")
        console.print(f"{datatype} ({datatype_desc})", style="white")

    def _display_geotransform_info(console, geo_params):
        """显示地理变换信息"""
        param_names = [
            "X原点坐标 (左上角X坐标)",
            "像素宽度 (X方向分辨率)",
            "X倾斜 (通常为0)",
            "Y原点坐标 (左上角Y坐标)",
            "Y倾斜 (通常为0)",
            "像素高度 (Y方向分辨率，通常为负值)",
        ]
        console.print()
        param_colors = [
            "bright_cyan",
            "bright_green",
            "bright_black",
            "bright_magenta",
            "bright_black",
            "bright_yellow",
        ]
        for i, (param, desc, color) in enumerate(
            zip(geo_params, param_names, param_colors)
        ):
            console.print(f"      [{i}] ", style="white", end="")
            console.print(f"{param:15.3f}", style=f"{color} bold", end="")
            console.print(f" - {desc}", style="bright_white")

    def _display_bounds_info(console, bounds):
        """显示边界信息"""
        if bounds:
            console.print()
            console.print(f"      西边界: {bounds['west']:10.6f}°", style="bright_cyan")
            console.print(f"      东边界: {bounds['east']:10.6f}°", style="bright_cyan")
            console.print(
                f"      南边界: {bounds['south']:10.6f}°", style="bright_green"
            )
            console.print(
                f"      北边界: {bounds['north']:10.6f}°", style="bright_green"
            )
        else:
            console.print("无法获取地理边界信息", style="bright_black")

    def _display_center_info(console, center):
        """显示中心点信息"""
        if center:
            console.print()
            console.print(
                f"      经度: {center['longitude']:10.6f}°", style="bright_yellow"
            )
            console.print(
                f"      纬度: {center['latitude']:10.6f}°", style="bright_yellow"
            )
        else:
            console.print("无法获取地理中心信息", style="bright_black")

    def display_conversion_results(results):
        """显示转换结果"""
        input_analysis = results["input_analysis"]

        # 显示处理开始信息
        console.print(
            "\n🎯 =============== TIFF转PNG处理开始 ===============",
            style="bright_yellow bold",
        )

        # 显示输入文件信息
        if input_analysis["file_info"]:
            file_info = input_analysis["file_info"]
            tiff_info = input_analysis["tiff_info"]

            console.print(
                f"📂 输入文件路径: {tiff_info.get('FilePath', 'N/A')}",
                style="bright_cyan",
            )
            console.print(
                f"📏 输入文件大小: {file_info['size_bytes']:,} 字节 ({file_info['size_mb']:.2f} MB)",
                style="bright_green",
            )
            console.print(
                f"📅 文件创建时间: {file_info['created_time']}", style="bright_blue"
            )
            console.print(
                f"🔄 文件修改时间: {file_info['modified_time']}", style="bright_magenta"
            )

        # 显示TIFF详细信息
        console.print("\n📊 正在分析输入TIFF文件...", style="bright_white bold")
        _display_detailed_tiff_info(console, input_analysis)

        # 显示处理结果
        console.print(
            f"\n✅ PNG转换完成! 耗时: {results['processing_time']:.3f} 秒",
            style="bright_green bold",
        )
        console.print(f"💾 输出文件: {results['output_path']}", style="bright_cyan")

        # 显示输出文件信息
        if results["output_info"]:
            output_info = results["output_info"]
            console.print(
                f"📏 输出文件大小: {output_info['size_bytes']:,} 字节 ({output_info['size_mb']:.2f} MB)",
                style="bright_green",
            )
            console.print(
                f"📦 压缩率: {results['compression_ratio']:.1f}%",
                style="bright_magenta",
            )

        # 显示PNG信息
        if results["png_info"] and "error" not in results["png_info"]:
            png_info = results["png_info"]
            console.print("🖼️  输出PNG详细信息:", style="bright_yellow bold")
            console.print(
                f"   📐 尺寸: {png_info['size'][0]} × {png_info['size'][1]} 像素",
                style="cyan",
            )
            console.print(f"   🎨 模式: {png_info['mode']}", style="green")
            console.print(f"   📊 格式: {png_info['format']}", style="blue")

        console.print(
            "🎯 =============== TIFF转PNG处理完成 ===============\n",
            style="bright_yellow bold",
        )

    def _display_detailed_tiff_info(console, analysis):
        """显示详细的TIFF信息"""
        tiff_info = analysis["tiff_info"]
        distance_area = analysis["distance_area"]
        coordinates = analysis["coordinates"]
        analysis_data = analysis["analysis"]

        console.print("🖼️  输入TIFF详细信息:", style="bright_yellow bold")
        console.print(
            f"   🟦 图像尺寸: {tiff_info['RasterXSize']} × {tiff_info['RasterYSize']} 像素",
            style="bright_cyan",
        )
        console.print(
            f"   📐 总像素数: {analysis_data['total_pixels']:,} 个像素",
            style="bright_green",
        )
        console.print(
            f"   📊 波段数量: {tiff_info['RasterCount']} 个波段", style="bright_magenta"
        )
        console.print(
            f"   🔢 数据类型: {analysis_data['datatype_info'][1]}", style="bright_red"
        )

        # 地理信息
        geo = tiff_info.get("GeoTransform")
        if geo and geo != (0.0, 1.0, 0.0, 0.0, 0.0, 1.0):
            console.print("   🗺️  地理坐标信息:", style="bright_blue")
            console.print(
                f"      📍 左上角坐标: ({geo[0]:.6f}, {geo[3]:.6f})", style="cyan"
            )
            console.print(
                f"      📏 像素分辨率: {geo[1]:.6f} × {abs(geo[5]):.6f}", style="green"
            )

            if distance_area["x_span_km"] is not None:
                console.print(
                    f"      🗺️  覆盖范围: {distance_area['x_span_km']:.3f}千米 × {distance_area['y_span_km']:.3f}千米",
                    style="yellow",
                )

            if coordinates["bounds_str"]:
                console.print(
                    f"      🌐 经纬度边界: {coordinates['bounds_str']}",
                    style="bright_cyan",
                )
            if coordinates["center_str"]:
                console.print(
                    f"      📍 中心位置: {coordinates['center_str']}",
                    style="bright_green",
                )

    def display_cropping_results(results):
        """显示裁切结果"""
        input_analysis = results["input_analysis"]
        crop_info = results["crop_info"]

        # 显示处理开始信息
        console.print(
            "\n✂️ =============== TIFF裁切处理开始 ===============",
            style="bright_yellow bold",
        )

        # 显示裁切参数
        console.print("📐 裁切参数信息:", style="bright_cyan bold")
        console.print(f"   📍 起始位置 (X偏移): {crop_info['xoff']} 像素", style="blue")
        console.print(f"   📍 起始位置 (Y偏移): {crop_info['yoff']} 像素", style="blue")
        console.print(f"   📏 裁切宽度: {crop_info['xsize']} 像素", style="magenta")
        console.print(f"   📏 裁切高度: {crop_info['ysize']} 像素", style="magenta")
        console.print(
            f"   📊 裁切像素总数: {crop_info['crop_pixels']:,} 个像素", style="yellow"
        )
        console.print(f"   📊 裁切比例: {crop_info['crop_ratio']:.2f}%", style="yellow")

        # 显示验证结果
        validation = results["crop_validation"]
        if validation["valid"]:
            console.print("✅ 裁切范围验证通过", style="bright_green")
        else:
            console.print("⚠️  警告: 裁切范围验证失败!", style="bright_red bold")
            for error in validation["errors"]:
                console.print(f"   ❌ {error}", style="red")

        # 显示处理结果
        if results["output_path"]:
            console.print(
                f"\n✅ TIFF裁切完成! 耗时: {results['processing_time']:.3f} 秒",
                style="bright_green bold",
            )
            console.print(f"💾 输出文件: {results['output_path']}", style="bright_cyan")

            # 显示输出分析
            if results["output_analysis"]:
                output_analysis = results["output_analysis"]
                console.print("\n🎯 输出TIFF详细分析:", style="bright_yellow bold")
                console.print(
                    f"   📏 输出文件大小: {output_analysis['file_info']['size_bytes']:,} 字节 ({output_analysis['file_info']['size_mb']:.2f} MB)",
                    style="green",
                )
                console.print(
                    f"   🟦 输出图像尺寸: {output_analysis['tiff_info']['RasterXSize']} × {output_analysis['tiff_info']['RasterYSize']} 像素",
                    style="cyan",
                )

                # 处理效率
                performance = results["performance"]
                console.print(
                    f"   ⚡ 处理效率: {performance['pixels_per_second']:,.0f} 像素/秒, {performance['mb_per_second']:.2f} MB/秒",
                    style="bright_green",
                )

        console.print(
            "✂️ =============== TIFF裁切处理完成 ===============\n",
            style="bright_yellow bold",
        )

    def display_comprehensive_info(analysis):
        """显示综合信息"""
        file_info = analysis["file_info"]
        tiff_info = analysis["tiff_info"]
        analysis_data = analysis["analysis"]
        distance_area = analysis["distance_area"]
        coordinates = analysis["coordinates"]

        # 标题
        console.print(
            "\n📊 ============== TIFF图像详细分析报告 ==============",
            style="bright_yellow bold",
        )

        # 文件基本信息
        if file_info:
            console.print("\n📁 文件系统信息:", style="bright_cyan bold")
            console.print(f"   📂 文件路径: {tiff_info.get('FilePath', 'N/A')}", style="cyan")
            console.print(f"   📝 文件名: {tiff_info.get('FileName', 'N/A')}", style="green")
            console.print(
                f"   📏 文件大小: {file_info['size_bytes']:,} 字节 ({file_info['size_mb']:.2f} MB)",
                style="blue",
            )
            console.print(
                f"   📅 创建时间: {file_info['created_time']}", style="magenta"
            )
            console.print(
                f"   🔄 修改时间: {file_info['modified_time']}", style="yellow"
            )
            console.print(
                f"   👁  访问时间: {file_info['accessed_time']}", style="bright_black"
            )

        # 图像基本属性
        console.print("\n🖼️  图像基本属性:", style="bright_green bold")
        console.print(f"   🟦 图像宽度: {tiff_info['RasterXSize']} 像素", style="cyan")
        console.print(f"   🟩 图像高度: {tiff_info['RasterYSize']} 像素", style="green")
        console.print(
            f"   📊 总像素数: {analysis_data['total_pixels']:,} 个像素", style="blue"
        )
        console.print(
            f"   📊 波段数量: {tiff_info['RasterCount']} 个波段", style="magenta"
        )
        console.print(
            f"   📏 纵横比: {analysis_data['aspect_ratio']:.3f} ({analysis_data['aspect_type']})",
            style="yellow",
        )
        console.print(
            f"   🔢 数据类型: {analysis_data['datatype_info'][0]} - {analysis_data['datatype_info'][1]}",
            style="red",
        )
        console.print(
            f"   📈 数值范围: {analysis_data['datatype_info'][2]}", style="bright_red"
        )
        console.print(
            f"   💾 内存占用: {analysis_data['datatype_info'][3]}", style="bright_blue"
        )

        # 驱动信息
        console.print("\n🔧 驱动信息:", style="bright_magenta bold")
        console.print(f"   📦 驱动名称: {tiff_info.get('DriverShortName', 'N/A')}", style="magenta")
        console.print(f"   📝 驱动描述: {tiff_info.get('DriverLongName', 'N/A')}", style="cyan")

        # 地理信息
        geotransform = tiff_info.get("GeoTransform")
        if geotransform and geotransform != (0.0, 1.0, 0.0, 0.0, 0.0, 1.0):
            console.print("\n🗺️  地理坐标信息:", style="bright_blue bold")
            console.print(
                f"   📍 左上角坐标: ({geotransform[0]:.6f}, {geotransform[3]:.6f})", style="cyan"
            )
            console.print(
                f"   📏 像素分辨率: {geotransform[1]:.6f} × {abs(geotransform[5]):.6f}", style="green"
            )

            if distance_area.get("x_span_km") is not None:
                console.print(
                    f"   🗺️  覆盖范围: {distance_area['x_span_km']:.3f}千米 × {distance_area['y_span_km']:.3f}千米",
                    style="yellow",
                )
                console.print(
                    f"   📐 覆盖面积: {distance_area['area_km2']:.3f} 平方千米", style="bright_yellow"
                )

            if coordinates.get("bounds_str"):
                console.print(
                    f"   🌐 经纬度边界: {coordinates['bounds_str']}", style="bright_cyan"
                )
            if coordinates.get("center_str"):
                console.print(
                    f"   📍 中心位置: {coordinates['center_str']}", style="bright_green"
                )

            # 投影信息
            projection = tiff_info.get("Projection")
            if projection:
                import re
                console.print("\n🌐 投影坐标系信息:", style="bright_yellow bold")
                # 提取关键投影信息
                if "PROJCS" in projection:
                    projcs_match = re.search(r'PROJCS\["([^"]+)"', projection)
                    if projcs_match:
                        console.print(f"   📊 投影名称: {projcs_match.group(1)}", style="yellow")

                if "GEOGCS" in projection:
                    geogcs_match = re.search(r'GEOGCS\["([^"]+)"', projection)
                    if geogcs_match:
                        console.print(f"   🌍 地理坐标系: {geogcs_match.group(1)}", style="green")

                if "DATUM" in projection:
                    datum_match = re.search(r'DATUM\["([^"]+)"', projection)
                    if datum_match:
                        console.print(f"   📐 大地基准: {datum_match.group(1)}", style="cyan")

        # 波段详细分析
        if tiff_info.get("BandInfo"):
            console.print("\n📈 波段详细分析:", style="bright_red bold")
            for band_info in tiff_info["BandInfo"]:
                console.print(f"\n   📊 波段 {band_info['BandNumber']}:", style="bright_white bold")
                if band_info.get("MinValue") is not None:
                    console.print(f"      📉 最小值: {band_info['MinValue']:.4f}", style="blue")
                    console.print(f"      📈 最大值: {band_info['MaxValue']:.4f}", style="red")
                    console.print(f"      📊 平均值: {band_info['MeanValue']:.4f}", style="green")
                    console.print(f"      📏 标准差: {band_info['StdDev']:.4f}", style="yellow")

                    # 计算数值范围和变异系数
                    value_range = band_info['MaxValue'] - band_info['MinValue']
                    console.print(f"      🎯 数值范围: {value_range:.4f}", style="magenta")

                    if band_info['MeanValue'] != 0:
                        cv = (band_info['StdDev'] / abs(band_info['MeanValue'])) * 100
                        cv_desc = "低变异" if cv < 50 else ("中变异" if cv < 100 else "高变异")
                        console.print(f"      📊 变异系数: {cv:.2f}% ({cv_desc})", style="bright_magenta")

                if band_info.get("NoDataValue") is not None:
                    console.print(f"      🚫 无效值: {band_info['NoDataValue']}", style="bright_black")

                # 颜色解释
                color_interp_map = {
                    0: "未定义", 1: "灰度", 2: "调色板", 3: "红色", 4: "绿色", 5: "蓝色", 6: "Alpha"
                }
                color_interp = color_interp_map.get(band_info.get("ColorInterpretation", 0), "未知")
                console.print(f"      🎨 颜色解释: {color_interp}", style="bright_cyan")

        # 内存和存储分析
        console.print("\n💾 内存和存储分析:", style="bright_red bold")
        console.print(
            f"   📊 未压缩数据大小: {analysis_data['uncompressed_size']:,} 字节 ({analysis_data['uncompressed_size'] / (1024 * 1024):.2f} MB)",
            style="red",
        )
        if file_info:
            console.print(
                f"   📦 实际文件大小: {file_info['size_bytes']:,} 字节 ({file_info['size_mb']:.2f} MB)",
                style="green",
            )
            console.print(
                f"   🗜️  压缩效率: {analysis_data['compression_ratio']:.1f}% 压缩",
                style="blue",
            )

        console.print(
            "\n📊 ============== 分析报告完成 ==============\n",
            style="bright_yellow bold",
        )

    return {
        "display_tiff_basic_info": display_tiff_basic_info,
        "display_conversion_results": display_conversion_results,
        "display_cropping_results": display_cropping_results,
        "display_comprehensive_info": display_comprehensive_info,
    }
