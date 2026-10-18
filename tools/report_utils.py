# 工具层事件上报的公共逻辑 (前端契约适配)
# ================================================================================
# 前端 ui/src/App.vue 对 tool_start 事件的契约:
#   1. 用 data.tool_name 渲染进度条 -> 所以 tool_name 必须等于"真实工具名"(函数名),
#      否则前端显示的名字和主智能体上报的那条不一致, 会出现两条语义不同的重复事件;
#   2. 当 data.args.filename 存在时, 拼出文件卡片地址
#      http://127.0.0.1:8020/outputs/<会话目录相对路径>/<args.filename>
#      -> 所以 filename 必须是"磁盘上真实存在的最终文件名(含扩展名)", 且相对于会话目录,
#         即 generate_markdown 要等补全 .md 之后、convert_md_to_pdf 要等 PDF 生成之后再上报。
# ================================================================================
from pathlib import Path
from typing import Any, Dict, Optional

from api.monitor import monitor


def session_relative_name(file_path, session_dir: Optional[str]) -> str:
    """
    把文件的绝对路径换算成"相对于会话目录"的文件名。

    前端已经知道当前会话目录(session_created 事件里的 path), 卡片地址只需要会话目录以内的
    相对路径, 因此这里剥离会话目录前缀, 并统一使用 "/" 分隔符(Windows 下 Path 会给出反斜杠)。

    Args:
        file_path: 文件路径(通常是 resolve_path 之后的绝对路径, str 或 Path)
        session_dir: 当前会话目录(上下文注入, 可能为 None)

    Returns:
        str: 会话目录内的相对文件名, 如 "华润三九调研报告.md" / "sub_dir/库存清单.md";
             文件确实落在会话目录之外时, 退化为纯文件名(尽力而为, 不报错)
    """
    path = Path(file_path)
    if session_dir:
        try:
            resolved = path.resolve()
            session_path = Path(session_dir).resolve()
            # 在会话目录内 -> 返回相对名; 不在(如绝对路径指向其他盘/其他目录) -> 用文件名兜底
            if resolved.is_relative_to(session_path):
                return str(resolved.relative_to(session_path)).replace("\\", "/")
        except Exception:
            pass
    return path.name


def report_file_tool_start(tool_name: str, file_path, session_dir: Optional[str] = None,
                           extra_args: Optional[Dict[str, Any]] = None):
    """
    文件类工具产出(或读取)文件之后的统一上报入口。

    必须在文件"真实落盘之后"调用, 这样 args.filename 指向的文件前端立刻就能访问到。

    Args:
        tool_name: 真实工具名(与 @tool 装饰的函数名保持一致)
        file_path: 磁盘上的最终文件绝对路径(含扩展名)
        session_dir: 当前会话目录, 用于换算相对文件名
        extra_args: 需要附带上报的其它入参(如 instruction), 不允许覆盖 filename 键
    """
    args: Dict[str, Any] = {"filename": session_relative_name(file_path, session_dir)}
    if extra_args:
        # 只补充非契约键, 保证 filename 不被覆盖
        args.update({k: v for k, v in extra_args.items() if k != "filename"})
    monitor.report_tool(tool_name, args)
