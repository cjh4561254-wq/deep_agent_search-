from utils.logger_utils import logger
import os
from dotenv import load_dotenv
from typing import  Literal
from langchain_core.tools import tool
from tavily import TavilyClient
from api.monitor import monitor

# 加载.env配置
load_dotenv()
# 创建tavily网络请求的客户端
_tavily_client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))

# 网络请求的工具
@tool
def internet_search(
        query:str,
        topic: Literal["general", "news", "finance"] = "general",
        max_results: int = 5,
        include_raw_content:bool = False #是否返回详细数据
):
    """
    进行网络搜索工具,可以查询数据库或者rag助手中不包含的数据
    :param query: 查询内容
    :param topic: 查询类别
    :param max_results: 查询返回的条数
    :param include_raw_content: 是否精简返回
    :return: 查询的返回结果
    """
    # 1. 做好日志记录
    logger.debug(f"开始进行{internet_search}工具的调用!查询内容:{query} , 查询数量:{max_results}")

    # 2. 向前端发送通知 给前端
    #    工具名必须用真实函数名 internet_search: 子智能体内部调用工具不会经过主智能体的流式上报,
    #    前端只能看到这一条自报事件, 用中文名会导致进度里显示的和实际工具对不上
    monitor.report_tool(tool_name="internet_search",args={"query":query,"topic":topic,"max_results":max_results,"include_raw_content":include_raw_content})

    # 3. 调用tavily
    #    网络异常/key失效/超时等都以异常形式抛出, 这里统一转成可读的字符串返回值,
    #    避免异常穿透 ToolNode 直接中断整条 agent 链路
    try:
        return _tavily_client.search(
            query = query,
            topic=topic,
            max_results=max_results,
            include_raw_content = include_raw_content
        )
    except Exception as e:
        logger.error(f"{internet_search}工具调用失败: {e}", exc_info=True)
        return f"网络搜索失败: {str(e)}"



