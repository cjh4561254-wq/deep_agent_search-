import uuid
import asyncio
import re
import uvicorn
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List
import shutil
from utils.logger_utils import logger
from agent.main_agent import run_deep_agent
from api.monitor import manager, monitor
from api.context import set_thread_context

# 项目根目录路径
_project_root = Path(__file__).resolve().parent.parent

app = FastAPI(title="DeepAgents API")

# 挂载输出目录，以便前端访问生成的静态文件
# 假设输出目录位于项目根目录下的 output
_output_dir = _project_root / "output"
_output_dir.mkdir(exist_ok=True)
app.mount("/outputs", StaticFiles(directory=_output_dir), name="outputs")

# 定义上传目录 upload
_upload_dir = _project_root / "upload"
_upload_dir.mkdir(exist_ok=True)

# 配置 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class TaskRequest(BaseModel):
    query: str
    thread_id: str = None


def _safe_thread_id(thread_id: str) -> str:
    """
    会话 ID 清洗 (Thread ID Sanitizer)。

    目标：thread_id 会被直接拼进 `session_{thread_id}` 目录名，
    若前端传入 `../../xxx` 之类的值，就会把目录写到 upload / output 之外。
    这里只保留 UUID 场景需要的字符 (字母、数字、下划线、中划线、点)。
    """
    return re.sub(r"[^0-9A-Za-z_.\-]", "", thread_id or "").strip(".") or ""


def _safe_filename(raw_name: str) -> str:
    """
    文件名清洗 (Filename Sanitizer)。

    目标：上传接口的 filename 完全由客户端控制，`../../xx.txt` 会造成任意路径写文件。
    这里剥掉所有目录部分，只保留最终文件名。
    """
    return (raw_name or "").replace("\\", "/").split("/")[-1]


async def _run_agent_safely(query: str, thread_id: str):
    """
    后台任务的安全包装 (Agent Runner Guard)。

    目标：
    1. 先把 thread_id 写入 ContextVar，保证 monitor 在任何时刻都知道
       "这条事件应该推给哪一条 WebSocket"（即便 Agent 自身尚未完成上下文绑定）。
    2. 兜底捕获 run_deep_agent 抛出的异常，向前端下发 error 事件。
       Agent 在进入自身 try 之前仍可能失败（创建会话目录、拷贝上传文件等），
       若不做兜底，前端收不到任何事件，界面会一直停在"运行中"。

    Args:
        query (str): 用户指令。
        thread_id (str): 会话 ID，同时作为 WebSocket 的分发键。
    """
    # 1. [上下文] 绑定会话；协程任务的上下文是独立拷贝，任务结束自动销毁，无需手动 reset
    set_thread_context(thread_id)
    try:
        # 2. [执行] 正常链路：过程事件由 agent / tools 自行上报
        await run_deep_agent(query, thread_id)
    except Exception as e:
        # 3. [兜底] 保持事件契约: type=monitor_event / event=error / 顶层 message
        logger.error(f"[Server] 任务执行异常 (thread_id={thread_id}): {e}")
        monitor._emit("error", f"任务执行失败: {e}")


@app.on_event("startup")
async def startup_event():
    """
    服务启动时，获取当前运行的事件循环，并绑定到 WebSocket 管理器。
    确保后台线程能通过 run_coroutine_threadsafe 准确投递消息。
    """
    loop = asyncio.get_running_loop()
    manager.set_loop(loop)
    logger.debug(f"[Server] WebSocket Manager bound to loop: {id(loop)}")


@app.post("/api/task")
async def run_task(request: TaskRequest):
    """
    智能体任务启动接口 (Run Agent Task)。

    目标：
    1. 接收用户的自然语言指令。
    2. 在后台异步启动 Agent 执行逻辑。
    3. 返回会话 ID，供前端通过 WebSocket 订阅实时进度。

    执行步骤：
    1. 获取或生成 thread_id。
    2. 触发异步任务 (asyncio.create_task)。
    3. 立即返回响应，不阻塞 HTTP 线程。

    Args:
        request (TaskRequest): 包含用户 query 和可选 thread_id 的请求体。
    """
    # 1. [ID 初始化] 清洗前端传来的会话 ID (防止 ../ 穿越拼进 output 目录名)
    thread_id = _safe_thread_id(request.thread_id) or str(uuid.uuid4())

    # 2. [后台执行] 异步运行 Agent，不阻塞主线程
    # 注意：包一层 _run_agent_safely，负责上下文绑定与异常兜底，保证前端一定能收到终态事件
    asyncio.create_task(_run_agent_safely(request.query, thread_id))

    # 3. [立即响应]
    return {"status": "started", "thread_id": thread_id}


@app.post("/api/upload")
async def upload_files(files: List[UploadFile] = File(...), thread_id: str = Form(...)):
    """
    文件上传接口 (File Upload)。

    目标：
    1. 接收用户上传的一个或多个文件。
    2. 保存到 `upload/session_{thread_id}` 目录。
    3. 供 Agent 在后续任务中读取和分析。

    Args:
        files (List[UploadFile]): 文件对象列表。
        thread_id (str): 关联的任务会话 ID。
    """
    # 1. [目录准备] 确保上传目录存在 (thread_id 需清洗，否则会变成目录名字段的穿越入口)
    target_dir = _upload_dir / f"session_{_safe_thread_id(thread_id)}"
    target_dir.mkdir(parents=True, exist_ok=True)

    saved_files = []
    # 2. [保存] 遍历并写入文件
    for file in files:
        # 关键安全处理：文件名由客户端控制，必须剥离目录部分 (否则 ../../x.txt 可写任意路径)
        safe_name = _safe_filename(file.filename)
        if not safe_name:
            continue
        file_path = target_dir / safe_name
        # 使用二进制模式写入，支持各种文件格式 (图片、PDF、文本等)
        # shutil.copyfileobj 高效复制文件流，避免一次性加载大文件到内存
        with file_path.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        saved_files.append(safe_name)

    # 3. [响应] 返回成功保存的文件列表
    return {"status": "uploaded", "files": saved_files}


@app.get("/api/download")
async def download_file(path: str):
    """
    文件下载接口 (File Download)。

    目标：
    1. 根据绝对路径下载文件。
    2. 严格的安全检查，防止越权访问。

    Args:
        path (str): 文件的绝对路径 (通常从 list_files 接口获取)。
    """
    # 1. [安全检查] 路径解析与越权校验
    try:
        abs_path = Path(path).resolve()
        output_abs = _output_dir.resolve()

        # 必须确保请求的文件在 output 目录下
        if not abs_path.is_relative_to(output_abs):
            return {"error": "拒绝访问: 只能下载输出目录下的文件"}
    except Exception:
        return {"error": "无效的路径参数"}
    # 2. [存在性检查]
    if not abs_path.exists():
        return {"error": "文件不存在"}

    # 3. [响应] 返回文件流 (浏览器自动触发下载)
    return FileResponse(abs_path, filename=abs_path.name)


@app.get("/api/files")
async def list_files(path: str):
    """
    文件列表查询接口 (File Explorer)。

    目标：
    1. 列出指定目录下的所有生成文件。
    2. 提供文件元数据（大小、时间、下载链接）。
    3. 严格的安全检查，防止路径遍历攻击。

    Args:
        path (str): 目标目录的绝对路径 (必须在 output 目录下)。
    """
    # 1. [调试] 打印请求路径
    print(f"[DEBUG] 请求文件列表: {path}")

    try:
        # 2. [解析] 获取绝对路径对象
        abs_path = Path(path).resolve()
        output_abs = _output_dir.resolve()

        # 3. [安全] 检查路径是否越界 (Path Traversal Check)
        if not abs_path.is_relative_to(output_abs):
            print(f"[ERROR] 拒绝访问: {abs_path} 不在 {output_abs} 目录下")
            return {"error": "拒绝访问: 只能访问输出目录下的文件"}

    except Exception as e:
        print(f"[ERROR] 路径解析失败: {e}")
        return {"error": f"路径无效: {e}"}

    # 4. [检查] 目录是否存在
    if not abs_path.exists():
        return {"error": "目录不存在"}

    files = []
    try:
        # 5. [遍历] 递归查找所有文件
        for file_path in abs_path.rglob("*"):
            if file_path.is_file():
                # 计算相对路径，生成下载 URL
                stat = file_path.stat()
                files.append({
                    "name": file_path.name,
                    "type": "file",
                    "path": str(file_path),
                    # "url": f"/outputs/{url_path}",
                    "size": stat.st_size,
                    "mtime": stat.st_mtime
                })

    except Exception as e:
        print(f"[ERROR] 遍历文件失败: {e}")
        return {"error": str(e)}

    # 6. [排序] 按修改时间倒序排列 (最新的在前)
    files.sort(key=lambda x: x.get("mtime", 0), reverse=True)
    print(f"[DEBUG] 找到 {len(files)} 个文件")
    return {"files": files}


@app.websocket("/ws/{thread_id}")
async def websocket_endpoint(websocket: WebSocket, thread_id: str):
    """
    WebSocket 实时通讯核心接口 (Real-time Communication)。

    目标：
    1. 建立长连接，实现服务端与前端的双向通信。
    2. 绑定 `thread_id`，实现会话级消息隔离。
    3. 维持心跳 (Keep-Alive)，防止连接超时。

    执行步骤：
    1. 握手：接受 WebSocket 连接请求。
    2. 注册：将连接实例绑定到 `monitor.manager`，关联 `thread_id`。
    3. 循环：进入消息监听循环，处理前端发送的心跳或指令。
    4. 异常：捕获断开连接异常，清理资源。

    Args:
        websocket (WebSocket): WebSocket 连接实例。
        thread_id (str): 当前会话的唯一标识。
    """
    # 1. [注册] 建立连接并绑定到管理器
    await manager.connect(websocket, thread_id)

    try:
        # 2. [循环] 保持连接活跃
        while True:
            # 3. [监听] 接收前端消息 (通常是 ping 心跳)
            data = await websocket.receive_text()

            # 4. [响应] 回复 pong 消息
            await websocket.send_json({
                "type": "pong",
                "message": f"服务端已收到: {data}"
            })

    except WebSocketDisconnect:
        # 5. [清理] 客户端主动断开
        manager.disconnect(websocket, thread_id)
        print(f"[WebSocket] 客户端已断开: {thread_id}")

    except Exception as e:
        # 6. [异常] 发生错误时断开
        print(f"[WebSocket] 连接异常: {e}")
        manager.disconnect(websocket, thread_id)


def start_server():
    uvicorn.run(app, host="0.0.0.0", port=8000)
