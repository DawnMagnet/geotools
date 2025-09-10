import typer

from .funcs import (
    analyze_tiff_comprehensive,
    create_display_functions,
    process_tiff_conversion,
    process_tiff_cropping,
)

# 创建显示函数模块
display_funcs = create_display_functions()


def tiff2png_cli():
    def main(
        input_tif: str,
        output_png: str,
        truncated_value: int = typer.Option(1, help="量化截断百分比"),
        downsample: int = typer.Option(1, help="降采样倍数 (如2表示缩小为1/2)"),
        show_info: bool = typer.Option(True, help="处理完成后显示详细信息"),
    ):
        """将tiff通过量化转换为png - 超详细版本"""
        # 执行转换流程
        results = process_tiff_conversion(
            input_tif, output_png, truncated_value, downsample
        )

        # 显示结果
        if show_info:
            display_funcs["display_conversion_results"](results)

    typer.run(main)


def cutiff_cli():
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
        # 执行裁切流程
        results = process_tiff_cropping(input_tif, output_tif, xoff, yoff, xsize, ysize)

        # 显示结果
        if show_info:
            display_funcs["display_cropping_results"](results)

    typer.run(main)


def tiffinfo_cli():
    def main(input_tif: str):
        """查看TIFF图像超详细信息 - 终极版本"""
        # 使用新的综合分析函数
        analysis = analyze_tiff_comprehensive(input_tif)

        # 显示综合信息
        display_funcs["display_comprehensive_info"](analysis)

    typer.run(main)
