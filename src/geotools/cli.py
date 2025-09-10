import typer

from .funcs import cutiff, tiff2png, tiffinfo


def _format_coordinate_bounds(bounds):
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


def _format_coordinate_center(center):
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


def _calculate_projected_distance_and_area(info):
    """基于投影坐标系计算距离和面积

    Args:
        info: TIFF信息字典，包含投影和地理变换信息

    Returns:
        dict: 包含距离和面积信息的字典

    """
    import math

    from osgeo import osr

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


def _display_tiff_info(info):
    """显示TIFF文件信息的共享函数"""
    typer.secho(
        "\n================ TIFF 信息 =================",
        fg=typer.colors.BRIGHT_YELLOW,
        bold=True,
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
            color = typer.colors.BRIGHT_CYAN
        elif k == "RasterCount":
            color = typer.colors.BRIGHT_MAGENTA
        elif k == "DataType":
            color = typer.colors.BRIGHT_GREEN
        elif k == "GeoTransform":
            color = typer.colors.BRIGHT_BLUE
        elif k == "Projection":
            color = typer.colors.BRIGHT_YELLOW
        elif k in ["GeographicBounds", "GeographicCenter"]:
            color = typer.colors.BRIGHT_MAGENTA
        else:
            color = typer.colors.WHITE
        typer.secho(f"{emoji} {k:14}: ", fg=color, bold=True, nl=False)
        if k == "Projection":
            proj = str(v)
            # 逐段高亮输出，保持单行
            i = 0
            while i < len(proj):
                # 查找下一个关键字或括号
                next_keyword = None
                next_pos = len(proj)

                # 关键字列表
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

                for keyword in keywords:
                    pos = proj.find(keyword, i)
                    if pos != -1 and pos < next_pos:
                        next_pos = pos
                        next_keyword = keyword

                # 查找括号
                bracket_pos = min(
                    [pos for pos in [proj.find("[", i), proj.find("]", i)] if pos != -1]
                    + [len(proj)]
                )

                if bracket_pos < next_pos:
                    # 输出括号前的内容
                    if bracket_pos > i:
                        typer.secho(proj[i:bracket_pos], nl=False)
                    # 高亮括号
                    typer.secho(
                        proj[bracket_pos], fg=typer.colors.BRIGHT_CYAN, nl=False
                    )
                    i = bracket_pos + 1
                elif next_keyword:
                    # 输出关键字前的内容
                    if next_pos > i:
                        typer.secho(proj[i:next_pos], nl=False)
                    # 高亮关键字
                    typer.secho(
                        next_keyword,
                        fg=typer.colors.BRIGHT_YELLOW,
                        bold=True,
                        nl=False,
                    )
                    i = next_pos + len(next_keyword)
                else:
                    # 输出剩余内容
                    typer.secho(proj[i:], nl=False)
                    break

            typer.echo("")  # 换行
        elif k == "DataType":
            # GDAL数据类型映射表
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
            datatype_desc = datatype_map.get(int(v), f"Unknown type {v}")
            typer.secho(f"{v} ({datatype_desc})", fg=typer.colors.WHITE)
        elif k == "GeoTransform":
            # GeoTransform 是一个6元素元组: (x_origin, pixel_width, x_skew, y_origin, y_skew, pixel_height)
            # 通常 x_skew 和 y_skew 为 0，pixel_height 为负值
            geo_params = eval(str(v))  # 将字符串转换为元组
            param_names = [
                "X原点坐标 (左上角X坐标)",
                "像素宽度 (X方向分辨率)",
                "X倾斜 (通常为0)",
                "Y原点坐标 (左上角Y坐标)",
                "Y倾斜 (通常为0)",
                "像素高度 (Y方向分辨率，通常为负值)",
            ]
            typer.echo()  # 换行
            # 为不同类型的参数设置不同颜色
            param_colors = [
                typer.colors.BRIGHT_CYAN,  # X原点 - 青色
                typer.colors.BRIGHT_GREEN,  # 像素宽度 - 绿色
                typer.colors.BRIGHT_BLACK,  # X倾斜 - 灰色（通常为0）
                typer.colors.BRIGHT_MAGENTA,  # Y原点 - 洋红色
                typer.colors.BRIGHT_BLACK,  # Y倾斜 - 灰色（通常为0）
                typer.colors.BRIGHT_YELLOW,  # 像素高度 - 黄色
            ]
            for i, (param, desc, color) in enumerate(
                zip(geo_params, param_names, param_colors)
            ):
                typer.secho(f"      [{i}] ", fg=typer.colors.WHITE, nl=False)
                typer.secho(f"{param:15.3f}", fg=color, bold=True, nl=False)
                typer.secho(f" - {desc}", fg=typer.colors.BRIGHT_WHITE)
        elif k == "GeographicBounds":
            if v:
                typer.echo()  # 换行
                typer.secho(
                    f"      西边界: {v['west']:10.6f}°", fg=typer.colors.BRIGHT_CYAN
                )
                typer.secho(
                    f"      东边界: {v['east']:10.6f}°", fg=typer.colors.BRIGHT_CYAN
                )
                typer.secho(
                    f"      南边界: {v['south']:10.6f}°", fg=typer.colors.BRIGHT_GREEN
                )
                typer.secho(
                    f"      北边界: {v['north']:10.6f}°", fg=typer.colors.BRIGHT_GREEN
                )
            else:
                typer.secho("无法获取地理边界信息", fg=typer.colors.BRIGHT_BLACK)
        elif k == "GeographicCenter":
            if v:
                typer.echo()  # 换行
                typer.secho(
                    f"      经度: {v['longitude']:10.6f}°",
                    fg=typer.colors.BRIGHT_YELLOW,
                )
                typer.secho(
                    f"      纬度: {v['latitude']:10.6f}°", fg=typer.colors.BRIGHT_YELLOW
                )
            else:
                typer.secho("无法获取地理中心信息", fg=typer.colors.BRIGHT_BLACK)
        else:
            typer.secho(f"{v}", fg=typer.colors.WHITE)
    typer.secho(
        "============================================\n",
        fg=typer.colors.BRIGHT_YELLOW,
        bold=True,
    )


def tiff2png_cli():
    app = typer.Typer()

    @app.command()
    def main(
        input_tif: str,
        output_png: str,
        truncated_value: int = typer.Option(1, help="量化截断百分比"),
        downsample: int = typer.Option(1, help="降采样倍数 (如2表示缩小为1/2)"),
        show_info: bool = typer.Option(True, help="处理完成后显示详细信息"),
    ):
        """将tiff通过量化转换为png - 超详细版本"""
        import datetime
        import os

        from PIL import Image

        # 开始处理提示
        typer.secho(
            "\n🎯 =============== TIFF转PNG处理开始 ===============",
            fg=typer.colors.BRIGHT_YELLOW,
            bold=True,
        )

        # 显示输入文件详细信息
        input_stat = None
        if os.path.exists(input_tif):
            input_stat = os.stat(input_tif)
            typer.secho(f"📂 输入文件路径: {input_tif}", fg=typer.colors.BRIGHT_CYAN)
            typer.secho(
                f"📏 输入文件大小: {input_stat.st_size:,} 字节 ({input_stat.st_size / (1024 * 1024):.2f} MB)",
                fg=typer.colors.BRIGHT_GREEN,
            )
            typer.secho(
                f"📅 文件创建时间: {datetime.datetime.fromtimestamp(input_stat.st_ctime)}",
                fg=typer.colors.BRIGHT_BLUE,
            )
            typer.secho(
                f"🔄 文件修改时间: {datetime.datetime.fromtimestamp(input_stat.st_mtime)}",
                fg=typer.colors.BRIGHT_MAGENTA,
            )

        # 获取输入TIFF详细信息
        typer.secho(
            "\n📊 正在分析输入TIFF文件...", fg=typer.colors.BRIGHT_WHITE, bold=True
        )
        input_info = tiffinfo(input_tif)

        # 显示输入TIFF超详细信息
        typer.secho("🖼️  输入TIFF详细信息:", fg=typer.colors.BRIGHT_YELLOW, bold=True)
        typer.secho(
            f"   🟦 图像尺寸: {input_info['RasterXSize']} × {input_info['RasterYSize']} 像素",
            fg=typer.colors.BRIGHT_CYAN,
        )
        total_pixels = input_info["RasterXSize"] * input_info["RasterYSize"]
        typer.secho(
            f"   📐 总像素数: {total_pixels:,} 个像素", fg=typer.colors.BRIGHT_GREEN
        )
        typer.secho(
            f"   📊 波段数量: {input_info['RasterCount']} 个波段",
            fg=typer.colors.BRIGHT_MAGENTA,
        )

        # 数据类型详细说明
        datatype_map = {
            1: "Byte (8位无符号整数, 范围0-255)",
            2: "UInt16 (16位无符号整数, 范围0-65535)",
            3: "Int16 (16位有符号整数, 范围-32768到32767)",
            4: "UInt32 (32位无符号整数)",
            5: "Int32 (32位有符号整数)",
            6: "Float32 (32位浮点数)",
            7: "Float64 (64位浮点数)",
        }
        dt_desc = datatype_map.get(
            input_info["DataType"], f"未知类型 {input_info['DataType']}"
        )
        typer.secho(f"   🔢 数据类型: {dt_desc}", fg=typer.colors.BRIGHT_RED)

        # 地理坐标信息
        geo = input_info["GeoTransform"]
        if geo and geo != (0.0, 1.0, 0.0, 0.0, 0.0, 1.0):
            typer.secho("   🗺️  地理坐标信息:", fg=typer.colors.BRIGHT_BLUE)
            typer.secho(
                f"      📍 左上角坐标: ({geo[0]:.6f}, {geo[3]:.6f})",
                fg=typer.colors.CYAN,
            )
            typer.secho(
                f"      📏 像素分辨率: {geo[1]:.6f} × {abs(geo[5]):.6f}",
                fg=typer.colors.GREEN,
            )

            # 计算图像覆盖范围
            min_x, max_y = geo[0], geo[3]
            max_x = min_x + input_info["RasterXSize"] * geo[1]
            min_y = max_y + input_info["RasterYSize"] * geo[5]

            # 使用投影坐标系计算准确的距离
            distance_area_info = _calculate_projected_distance_and_area(input_info)

            if distance_area_info["x_span_km"] is not None:
                typer.secho(
                    f"      🗺️  覆盖范围: X({min_x:.6f} 到 {max_x:.6f}, {distance_area_info['x_span_km']:.3f}千米), Y({min_y:.6f} 到 {max_y:.6f}, {distance_area_info['y_span_km']:.3f}千米)",
                    fg=typer.colors.YELLOW,
                )
            else:
                typer.secho(
                    f"      🗺️  覆盖范围: X({min_x:.6f} 到 {max_x:.6f}), Y({min_y:.6f} 到 {max_y:.6f})",
                    fg=typer.colors.YELLOW,
                )

            # 显示经纬度边界信息
            if input_info.get("GeographicBounds"):
                bounds_str = _format_coordinate_bounds(input_info["GeographicBounds"])
                if bounds_str:
                    typer.secho(
                        f"      🌐 经纬度边界: {bounds_str}",
                        fg=typer.colors.BRIGHT_CYAN,
                    )
            if input_info.get("GeographicCenter"):
                center_str = _format_coordinate_center(input_info["GeographicCenter"])
                if center_str:
                    typer.secho(
                        f"      📍 中心位置: {center_str}",
                        fg=typer.colors.BRIGHT_GREEN,
                    )

        # 波段统计信息
        if input_info.get("BandInfo"):
            typer.secho("   📈 波段统计信息:", fg=typer.colors.BRIGHT_YELLOW)
            for band in input_info["BandInfo"]:
                if band["MinValue"] is not None:
                    typer.secho(
                        f"      波段{band['BandNumber']}: 最小值={band['MinValue']:.2f}, "
                        f"最大值={band['MaxValue']:.2f}, 平均值={band['MeanValue']:.2f}, "
                        f"标准差={band['StdDev']:.2f}",
                        fg=typer.colors.WHITE,
                    )

        # 开始转换过程
        typer.secho(
            f"\n🔄 开始转换处理 (截断百分比: {truncated_value}%)...",
            fg=typer.colors.BRIGHT_WHITE,
            bold=True,
        )
        start_time = datetime.datetime.now()

        result = tiff2png(input_tif, output_png, truncated_value, downsample)

        end_time = datetime.datetime.now()
        processing_time = (end_time - start_time).total_seconds()

        # 转换完成信息
        typer.secho(
            f"✅ PNG转换完成! 耗时: {processing_time:.3f} 秒",
            fg=typer.colors.BRIGHT_GREEN,
            bold=True,
        )
        typer.secho(f"💾 输出文件: {result}", fg=typer.colors.BRIGHT_CYAN)

        # 输出PNG文件详细信息
        if os.path.exists(result):
            output_stat = os.stat(result)
            typer.secho(
                f"� 输出文件大小: {output_stat.st_size:,} 字节 ({output_stat.st_size / (1024 * 1024):.2f} MB)",
                fg=typer.colors.BRIGHT_GREEN,
            )

            # 压缩率计算
            if input_stat is not None:
                compression_ratio = (1 - output_stat.st_size / input_stat.st_size) * 100
                typer.secho(
                    f"📦 压缩率: {compression_ratio:.1f}% (原文件大小的 {output_stat.st_size / input_stat.st_size * 100:.1f}%)",
                    fg=typer.colors.BRIGHT_MAGENTA,
                )

            # 使用PIL获取PNG详细信息
            try:
                with Image.open(result) as img:
                    typer.secho(
                        "🖼️  输出PNG详细信息:", fg=typer.colors.BRIGHT_YELLOW, bold=True
                    )
                    typer.secho(
                        f"   📐 尺寸: {img.size[0]} × {img.size[1]} 像素",
                        fg=typer.colors.CYAN,
                    )
                    typer.secho(f"   🎨 模式: {img.mode}", fg=typer.colors.GREEN)
                    typer.secho(f"   📊 格式: {img.format}", fg=typer.colors.BLUE)
                    if hasattr(img, "info"):
                        typer.secho(f"   ℹ️  PNG信息: {img.info}", fg=typer.colors.WHITE)
            except Exception as e:
                typer.secho(f"⚠️  无法读取PNG详细信息: {e}", fg=typer.colors.YELLOW)

        typer.secho(
            "🎯 =============== TIFF转PNG处理完成 ===============\n",
            fg=typer.colors.BRIGHT_YELLOW,
            bold=True,
        )

    app()


def cutiff_cli():
    app = typer.Typer()

    @app.command()
    def main(
        input_tif: str,
        output_tif: str,
        xoff: int,
        yoff: int,
        xsize: int,
        ysize: int,
        show_info: bool = typer.Option(True, help="处理完成后显示超详细信息"),
    ):
        """给定坐标裁切tiff - 超详细版本"""
        import datetime
        import os

        # 开始处理提示
        typer.secho(
            "\n✂️ =============== TIFF裁切处理开始 ===============",
            fg=typer.colors.BRIGHT_YELLOW,
            bold=True,
        )

        # 显示裁切参数详细信息
        typer.secho("📐 裁切参数信息:", fg=typer.colors.BRIGHT_CYAN, bold=True)
        typer.secho(f"   📂 输入文件: {input_tif}", fg=typer.colors.CYAN)
        typer.secho(f"   💾 输出文件: {output_tif}", fg=typer.colors.GREEN)
        typer.secho(f"   📍 起始位置 (X偏移): {xoff} 像素", fg=typer.colors.BLUE)
        typer.secho(f"   📍 起始位置 (Y偏移): {yoff} 像素", fg=typer.colors.BLUE)
        typer.secho(f"   📏 裁切宽度: {xsize} 像素", fg=typer.colors.MAGENTA)
        typer.secho(f"   📏 裁切高度: {ysize} 像素", fg=typer.colors.MAGENTA)
        typer.secho(
            f"   🎯 裁切区域: ({xoff}, {yoff}) 到 ({xoff + xsize}, {yoff + ysize})",
            fg=typer.colors.RED,
        )
        typer.secho(
            f"   📊 裁切像素总数: {xsize * ysize:,} 个像素", fg=typer.colors.YELLOW
        )

        # 获取原始文件信息
        input_stat = None
        input_info = None
        crop_pixels = xsize * ysize

        if os.path.exists(input_tif):
            input_stat = os.stat(input_tif)
            input_info = tiffinfo(input_tif)

            typer.secho(
                "\n🖼️  原始TIFF文件信息:", fg=typer.colors.BRIGHT_YELLOW, bold=True
            )
            typer.secho(
                f"   📏 原始文件大小: {input_stat.st_size:,} 字节 ({input_stat.st_size / (1024 * 1024):.2f} MB)",
                fg=typer.colors.GREEN,
            )
            typer.secho(
                f"   🟦 原始图像尺寸: {input_info['RasterXSize']} × {input_info['RasterYSize']} 像素",
                fg=typer.colors.CYAN,
            )

            # 计算裁切比例
            original_pixels = input_info["RasterXSize"] * input_info["RasterYSize"]
            crop_pixels = xsize * ysize
            crop_ratio = (crop_pixels / original_pixels) * 100
            typer.secho(
                f"   📊 裁切比例: {crop_ratio:.2f}% ({crop_pixels:,}/{original_pixels:,} 像素)",
                fg=typer.colors.YELLOW,
            )

            # 验证裁切范围是否有效
            if (
                xoff + xsize > input_info["RasterXSize"]
                or yoff + ysize > input_info["RasterYSize"]
            ):
                typer.secho(
                    "⚠️  警告: 裁切区域超出原始图像范围!",
                    fg=typer.colors.BRIGHT_RED,
                    bold=True,
                )
                typer.secho(
                    f"   原始范围: (0, 0) 到 ({input_info['RasterXSize']}, {input_info['RasterYSize']})",
                    fg=typer.colors.RED,
                )
                typer.secho(
                    f"   请求范围: ({xoff}, {yoff}) 到 ({xoff + xsize}, {yoff + ysize})",
                    fg=typer.colors.RED,
                )
            else:
                typer.secho("✅ 裁切范围验证通过", fg=typer.colors.BRIGHT_GREEN)

        # 开始裁切过程
        typer.secho("\n🔄 开始裁切处理...", fg=typer.colors.BRIGHT_WHITE, bold=True)
        start_time = datetime.datetime.now()

        result = cutiff(input_tif, output_tif, xoff, yoff, xsize, ysize)

        end_time = datetime.datetime.now()
        processing_time = (end_time - start_time).total_seconds()

        # 裁切完成信息
        typer.secho(
            f"✅ TIFF裁切完成! 耗时: {processing_time:.3f} 秒",
            fg=typer.colors.BRIGHT_GREEN,
            bold=True,
        )
        typer.secho(f"💾 输出文件: {result}", fg=typer.colors.BRIGHT_CYAN)

        # 输出文件详细分析
        if os.path.exists(result):
            output_stat = os.stat(result)
            output_info = tiffinfo(result)

            typer.secho(
                "\n🎯 输出TIFF详细分析:", fg=typer.colors.BRIGHT_YELLOW, bold=True
            )
            typer.secho(
                f"   📏 输出文件大小: {output_stat.st_size:,} 字节 ({output_stat.st_size / (1024 * 1024):.2f} MB)",
                fg=typer.colors.GREEN,
            )
            typer.secho(
                f"   🟦 输出图像尺寸: {output_info['RasterXSize']} × {output_info['RasterYSize']} 像素",
                fg=typer.colors.CYAN,
            )
            typer.secho(
                f"   📊 波段数量: {output_info['RasterCount']} 个波段",
                fg=typer.colors.MAGENTA,
            )

            # 文件大小比较
            if input_stat is not None:
                size_ratio = (output_stat.st_size / input_stat.st_size) * 100
                size_reduction = input_stat.st_size - output_stat.st_size
                typer.secho(
                    f"   📦 文件大小比较: {size_ratio:.1f}% of 原始大小",
                    fg=typer.colors.BLUE,
                )
                typer.secho(
                    f"   💾 节省空间: {size_reduction:,} 字节 ({size_reduction / (1024 * 1024):.2f} MB)",
                    fg=typer.colors.GREEN,
                )

            # 地理坐标转换验证
            if output_info["GeoTransform"] and output_info["GeoTransform"] != (
                0.0,
                1.0,
                0.0,
                0.0,
                0.0,
                1.0,
            ):
                geo = output_info["GeoTransform"]
                typer.secho("   🗺️  地理坐标信息 (已更新):", fg=typer.colors.BRIGHT_BLUE)
                typer.secho(
                    f"      📍 新的左上角坐标: ({geo[0]:.6f}, {geo[3]:.6f})",
                    fg=typer.colors.CYAN,
                )
                typer.secho(
                    f"      📏 像素分辨率: {geo[1]:.6f} × {abs(geo[5]):.6f}",
                    fg=typer.colors.GREEN,
                )

                # 计算裁切后的覆盖范围
                min_x, max_y = geo[0], geo[3]
                max_x = min_x + output_info["RasterXSize"] * geo[1]
                min_y = max_y + output_info["RasterYSize"] * geo[5]

                # 使用投影坐标系计算准确的距离
                distance_area_info = _calculate_projected_distance_and_area(output_info)

                if distance_area_info["x_span_km"] is not None:
                    typer.secho(
                        f"      🗺️  新的覆盖范围: X({min_x:.6f} 到 {max_x:.6f}, {distance_area_info['x_span_km']:.3f}千米), Y({min_y:.6f} 到 {max_y:.6f}, {distance_area_info['y_span_km']:.3f}千米)",
                        fg=typer.colors.YELLOW,
                    )
                else:
                    typer.secho(
                        f"      🗺️  新的覆盖范围: X({min_x:.6f} 到 {max_x:.6f}), Y({min_y:.6f} 到 {max_y:.6f})",
                        fg=typer.colors.YELLOW,
                    )

                # 显示经纬度边界信息
                if output_info.get("GeographicBounds"):
                    bounds_str = _format_coordinate_bounds(
                        output_info["GeographicBounds"]
                    )
                    if bounds_str:
                        typer.secho(
                            f"      🌐 经纬度边界: {bounds_str}",
                            fg=typer.colors.BRIGHT_CYAN,
                        )
                if output_info.get("GeographicCenter"):
                    center_str = _format_coordinate_center(
                        output_info["GeographicCenter"]
                    )
                    if center_str:
                        typer.secho(
                            f"      📍 中心位置: {center_str}",
                            fg=typer.colors.BRIGHT_GREEN,
                        )

            # 波段统计分析
            if output_info.get("BandInfo"):
                typer.secho("   � 输出波段统计分析:", fg=typer.colors.BRIGHT_YELLOW)
                for i, band in enumerate(output_info["BandInfo"]):
                    if band["MinValue"] is not None:
                        value_range = band["MaxValue"] - band["MinValue"]
                        typer.secho(
                            f"      波段{band['BandNumber']}: 范围=[{band['MinValue']:.2f}, {band['MaxValue']:.2f}] "
                            f"(跨度={value_range:.2f}), 平均={band['MeanValue']:.2f}, 标准差={band['StdDev']:.2f}",
                            fg=typer.colors.WHITE,
                        )

            # 处理效率统计
            pixels_per_second = (
                crop_pixels / processing_time if processing_time > 0 else 0
            )
            mb_per_second = (
                (output_stat.st_size / (1024 * 1024)) / processing_time
                if processing_time > 0
                else 0
            )
            typer.secho(
                f"   ⚡ 处理效率: {pixels_per_second:,.0f} 像素/秒, {mb_per_second:.2f} MB/秒",
                fg=typer.colors.BRIGHT_GREEN,
            )

        typer.secho(
            "✂️ =============== TIFF裁切处理完成 ===============\n",
            fg=typer.colors.BRIGHT_YELLOW,
            bold=True,
        )

    app()


def tiffinfo_cli():
    app = typer.Typer()

    @app.command()
    def main(input_tif: str):
        """查看TIFF图像超详细信息 - 终极版本"""
        import datetime
        import os

        # 标题
        typer.secho(
            "\n📊 ============== TIFF图像详细分析报告 ==============",
            fg=typer.colors.BRIGHT_YELLOW,
            bold=True,
        )

        # 文件基本信息
        if os.path.exists(input_tif):
            file_stat = os.stat(input_tif)
            typer.secho("\n📁 文件系统信息:", fg=typer.colors.BRIGHT_CYAN, bold=True)
            typer.secho(f"   📂 文件路径: {input_tif}", fg=typer.colors.CYAN)
            typer.secho(
                f"   📝 文件名: {os.path.basename(input_tif)}", fg=typer.colors.GREEN
            )
            typer.secho(
                f"   📏 文件大小: {file_stat.st_size:,} 字节 ({file_stat.st_size / (1024 * 1024):.2f} MB)",
                fg=typer.colors.BLUE,
            )
            typer.secho(
                f"   📅 创建时间: {datetime.datetime.fromtimestamp(file_stat.st_ctime)}",
                fg=typer.colors.MAGENTA,
            )
            typer.secho(
                f"   🔄 修改时间: {datetime.datetime.fromtimestamp(file_stat.st_mtime)}",
                fg=typer.colors.YELLOW,
            )
            typer.secho(
                f"   👁️  访问时间: {datetime.datetime.fromtimestamp(file_stat.st_atime)}",
                fg=typer.colors.WHITE,
            )

        # 获取详细TIFF信息
        info = tiffinfo(input_tif)

        # 图像基本属性
        typer.secho("\n🖼️  图像基本属性:", fg=typer.colors.BRIGHT_GREEN, bold=True)
        typer.secho(f"   🟦 图像宽度: {info['RasterXSize']} 像素", fg=typer.colors.CYAN)
        typer.secho(
            f"   🟩 图像高度: {info['RasterYSize']} 像素", fg=typer.colors.GREEN
        )
        total_pixels = info["RasterXSize"] * info["RasterYSize"]
        typer.secho(f"   📐 总像素数: {total_pixels:,} 个像素", fg=typer.colors.BLUE)
        typer.secho(
            f"   📊 波段数量: {info['RasterCount']} 个波段", fg=typer.colors.MAGENTA
        )

        # 图像纵横比和分辨率类别
        aspect_ratio = info["RasterXSize"] / info["RasterYSize"]
        typer.secho(
            f"   📏 纵横比: {aspect_ratio:.3f} ({'横版' if aspect_ratio > 1 else '竖版' if aspect_ratio < 1 else '正方形'})",
            fg=typer.colors.YELLOW,
        )

        # 数据类型详细解释
        datatype_map = {
            1: ("Byte", "8位无符号整数", "0-255", "1 byte/pixel"),
            2: ("UInt16", "16位无符号整数", "0-65,535", "2 bytes/pixel"),
            3: ("Int16", "16位有符号整数", "-32,768 到 32,767", "2 bytes/pixel"),
            4: ("UInt32", "32位无符号整数", "0-4,294,967,295", "4 bytes/pixel"),
            5: (
                "Int32",
                "32位有符号整数",
                "-2,147,483,648 到 2,147,483,647",
                "4 bytes/pixel",
            ),
            6: ("Float32", "32位浮点数", "IEEE 754 单精度", "4 bytes/pixel"),
            7: ("Float64", "64位浮点数", "IEEE 754 双精度", "8 bytes/pixel"),
            8: ("CInt16", "复数16位整数", "复数对", "4 bytes/pixel"),
            9: ("CInt32", "复数32位整数", "复数对", "8 bytes/pixel"),
            10: ("CFloat32", "复数32位浮点", "复数对", "8 bytes/pixel"),
            11: ("CFloat64", "复数64位浮点", "复数对", "16 bytes/pixel"),
        }
        dt_info = datatype_map.get(
            info["DataType"], ("Unknown", "未知类型", "未知", "未知")
        )
        typer.secho(f"   🔢 数据类型: {dt_info[0]} - {dt_info[1]}", fg=typer.colors.RED)
        typer.secho(f"   📊 数值范围: {dt_info[2]}", fg=typer.colors.WHITE)
        typer.secho(f"   💾 内存占用: {dt_info[3]}", fg=typer.colors.BRIGHT_BLACK)

        # 驱动信息
        if "DriverShortName" in info:
            typer.secho("\n🔧 驱动信息:", fg=typer.colors.BRIGHT_BLUE, bold=True)
            typer.secho(
                f"   📦 驱动名称: {info['DriverShortName']}", fg=typer.colors.BLUE
            )
            typer.secho(
                f"   📝 驱动描述: {info['DriverLongName']}", fg=typer.colors.CYAN
            )

        # 地理坐标系统详细信息
        geo = info["GeoTransform"]
        if geo and geo != (0.0, 1.0, 0.0, 0.0, 0.0, 1.0):
            typer.secho("\n🗺️  地理坐标系统:", fg=typer.colors.BRIGHT_BLUE, bold=True)
            typer.secho(
                f"   📍 左上角坐标: ({geo[0]:.6f}, {geo[3]:.6f})", fg=typer.colors.CYAN
            )
            typer.secho(f"   📏 X方向分辨率: {geo[1]:.6f}", fg=typer.colors.GREEN)
            typer.secho(
                f"   📏 Y方向分辨率: {abs(geo[5]):.6f} ({'向下' if geo[5] < 0 else '向上'})",
                fg=typer.colors.GREEN,
            )
            typer.secho(f"   🔄 X旋转/倾斜: {geo[2]:.6f}", fg=typer.colors.YELLOW)
            typer.secho(f"   🔄 Y旋转/倾斜: {geo[4]:.6f}", fg=typer.colors.YELLOW)

            # 计算覆盖范围
            min_x, max_y = geo[0], geo[3]
            max_x = min_x + info["RasterXSize"] * geo[1]
            min_y = max_y + info["RasterYSize"] * geo[5]
            typer.secho("   🗺️  覆盖范围:", fg=typer.colors.BRIGHT_MAGENTA)

            # 使用投影坐标系计算准确的距离和面积
            distance_area_info = _calculate_projected_distance_and_area(info)

            x_span = abs(max_x - min_x)
            y_span = abs(max_y - min_y)

            if distance_area_info["x_span_km"] is not None:
                typer.secho(
                    f"      X轴: {min_x:.6f} 到 {max_x:.6f} (跨度: {x_span:.6f} {distance_area_info['unit_name']}, {distance_area_info['x_span_km']:.3f} 千米)",
                    fg=typer.colors.MAGENTA,
                )
                typer.secho(
                    f"      Y轴: {min_y:.6f} 到 {max_y:.6f} (跨度: {y_span:.6f} {distance_area_info['unit_name']}, {distance_area_info['y_span_km']:.3f} 千米)",
                    fg=typer.colors.MAGENTA,
                )
            else:
                typer.secho(
                    f"      X轴: {min_x:.6f} 到 {max_x:.6f} (跨度: {x_span:.6f} 单位)",
                    fg=typer.colors.MAGENTA,
                )
                typer.secho(
                    f"      Y轴: {min_y:.6f} 到 {max_y:.6f} (跨度: {y_span:.6f} 单位)",
                    fg=typer.colors.MAGENTA,
                )

            # 显示经纬度边界信息
            if info.get("GeographicBounds"):
                bounds_str = _format_coordinate_bounds(info["GeographicBounds"])
                if bounds_str:
                    typer.secho(
                        f"      🌐 经纬度边界: {bounds_str}",
                        fg=typer.colors.BRIGHT_CYAN,
                    )
            if info.get("GeographicCenter"):
                center_str = _format_coordinate_center(info["GeographicCenter"])
                if center_str:
                    typer.secho(
                        f"      📍 中心位置: {center_str}",
                        fg=typer.colors.BRIGHT_GREEN,
                    )

            # 计算地面覆盖面积（基于投影坐标系）
            area = abs((max_x - min_x) * (max_y - min_y))
            if distance_area_info["area_km2"] is not None:
                typer.secho(
                    f"   📐 覆盖面积: {area:,.0f} 平方{distance_area_info['unit_name']} ({distance_area_info['area_km2']:.3f} 平方千米)",
                    fg=typer.colors.RED,
                )
            else:
                typer.secho(
                    f"   📐 覆盖面积: {area:,.0f} 平方单位", fg=typer.colors.RED
                )

        # 投影信息
        if info.get("Projection") and info["Projection"].strip():
            proj = info["Projection"]
            typer.secho("\n🌐 投影信息:", fg=typer.colors.BRIGHT_GREEN, bold=True)

            # 解析投影关键信息
            proj_keywords = {
                "PROJCS": "投影坐标系",
                "GEOGCS": "地理坐标系",
                "DATUM": "基准面",
                "SPHEROID": "椭球体",
                "PRIMEM": "本初子午线",
                "UNIT": "单位",
                "PROJECTION": "投影方法",
                "AUTHORITY": "权威机构",
            }

            for keyword, desc in proj_keywords.items():
                if keyword in proj:
                    # 简单提取关键字后的内容
                    start = proj.find(keyword)
                    if start != -1:
                        bracket_start = proj.find("[", start)
                        if bracket_start != -1:
                            bracket_end = proj.find("]", bracket_start)
                            if bracket_end != -1:
                                content = proj[bracket_start + 1 : bracket_end]
                                if "," in content:
                                    main_value = content.split(",")[0].strip('"')
                                else:
                                    main_value = content.strip('"')
                                typer.secho(
                                    f"   🎯 {desc}: {main_value}", fg=typer.colors.GREEN
                                )

        # 波段详细分析
        if info.get("BandInfo"):
            typer.secho("\n📈 波段详细分析:", fg=typer.colors.BRIGHT_YELLOW, bold=True)
            for i, band in enumerate(info["BandInfo"]):
                typer.secho(
                    f"\n   📊 波段 {band['BandNumber']}:",
                    fg=typer.colors.YELLOW,
                    bold=True,
                )

                if band["MinValue"] is not None:
                    value_range = band["MaxValue"] - band["MinValue"]
                    typer.secho(
                        f"      📉 最小值: {band['MinValue']:.4f}", fg=typer.colors.CYAN
                    )
                    typer.secho(
                        f"      📈 最大值: {band['MaxValue']:.4f}", fg=typer.colors.RED
                    )
                    typer.secho(
                        f"      📊 平均值: {band['MeanValue']:.4f}",
                        fg=typer.colors.GREEN,
                    )
                    typer.secho(
                        f"      📏 标准差: {band['StdDev']:.4f}", fg=typer.colors.BLUE
                    )
                    typer.secho(
                        f"      🎯 数值范围: {value_range:.4f}", fg=typer.colors.MAGENTA
                    )

                    # 数据分布分析
                    cv = (
                        (band["StdDev"] / band["MeanValue"]) * 100
                        if band["MeanValue"] != 0
                        else 0
                    )
                    typer.secho(
                        f"      📊 变异系数: {cv:.2f}% ({'低变异' if cv < 15 else '中变异' if cv < 30 else '高变异'})",
                        fg=typer.colors.YELLOW,
                    )

                if band.get("NoDataValue") is not None:
                    typer.secho(
                        f"      🚫 无效值: {band['NoDataValue']}", fg=typer.colors.RED
                    )

                # 颜色解释
                color_interp_map = {
                    0: "未定义",
                    1: "灰度",
                    2: "调色板索引",
                    3: "红色通道",
                    4: "绿色通道",
                    5: "蓝色通道",
                    6: "透明度通道",
                    7: "色调",
                    8: "饱和度",
                    9: "亮度",
                    10: "青色",
                    11: "洋红色",
                    12: "黄色",
                    13: "黑色",
                }
                color_desc = color_interp_map.get(
                    band.get("ColorInterpretation", 0), "未知"
                )
                typer.secho(
                    f"      🎨 颜色解释: {color_desc}", fg=typer.colors.BRIGHT_BLUE
                )

        # 内存和存储分析
        if "DataType" in info and "RasterCount" in info:
            bytes_per_pixel = {
                1: 1,
                2: 2,
                3: 2,
                4: 4,
                5: 4,
                6: 4,
                7: 8,
                8: 4,
                9: 8,
                10: 8,
                11: 16,
            }
            bpp = bytes_per_pixel.get(info["DataType"], 1)
            total_bytes = total_pixels * info["RasterCount"] * bpp

            typer.secho("\n💾 内存和存储分析:", fg=typer.colors.BRIGHT_RED, bold=True)
            typer.secho(
                f"   📊 未压缩数据大小: {total_bytes:,} 字节 ({total_bytes / (1024 * 1024):.2f} MB)",
                fg=typer.colors.RED,
            )

            if os.path.exists(input_tif):
                file_size = os.path.getsize(input_tif)
                compression_ratio = (
                    (1 - file_size / total_bytes) * 100 if total_bytes > 0 else 0
                )
                typer.secho(
                    f"   📦 实际文件大小: {file_size:,} 字节 ({file_size / (1024 * 1024):.2f} MB)",
                    fg=typer.colors.GREEN,
                )
                typer.secho(
                    f"   🗜️  压缩效率: {compression_ratio:.1f}% 压缩",
                    fg=typer.colors.BLUE,
                )

        typer.secho(
            "\n📊 ============== 分析报告完成 ==============\n",
            fg=typer.colors.BRIGHT_YELLOW,
            bold=True,
        )

    app()
