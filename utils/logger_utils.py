# 日志配置的工具模块

import logging
import colorlog # uv add colorlog

# 初始化全局日志对象
logger = logging.getLogger()


def configure_project_logging():
    # 幂等保护：本模块导入时已配置一次，main.py 启动时还会再调用。
    # 若不加判断会重复 addHandler，导致每一行日志被输出两遍。
    if getattr(logger, "_project_logging_configured", False):
        return

    # 设置日志的默认的级别
    logger.setLevel(logging.DEBUG)

    # 加载彩色日志处理器
    handler = colorlog.StreamHandler()
    # 定义日志输出的格式
    handler.setFormatter(colorlog.ColoredFormatter(
        '%(log_color)s%(asctime)s - %(filename)s:%(lineno)d - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        log_colors={
            'DEBUG': 'cyan',
            'INFO': 'green',  # INFO 显示为绿色
            'WARNING': 'yellow',
            'ERROR': 'red',
            'CRITICAL': 'bold_red',
        }
    ))

    # 应用日志配置信息
    logger.addHandler(handler)
    # 标记已配置，后续重复调用直接跳过
    logger._project_logging_configured = True


# 导入本模块即完成配置，保证单独运行 agent/tools 脚本时也有日志输出
configure_project_logging()
