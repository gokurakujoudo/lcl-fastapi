# `lcl-fastapi` 需求说明

## 1. 项目定位

`lcl-fastapi` 是一个面向 Python 3.14+ 服务端应用的轻量 FastAPI 基础框架。

项目必须以 `lcl-fastapi` 作为 PyPI Distribution Name，并以 `lcl_fastapi` 作为 Python import package name。

项目必须基于 `lclang==1.0.10` 提供配置、日志、CLI 基础能力和 Snowflake ID 生成能力。

项目必须让下游项目只关注业务 Router、业务配置和业务生命周期，而不需要自行处理 Uvicorn、Gunicorn、日志初始化、Request ID、Swagger 静态资源和基础部署配置。

第一版必须保持功能简单、职责明确，并避免重复实现 `lclang` 已经提供的基础能力。

项目采用 MIT License。本需求说明记录经用户确认的业务契约；工程与交付要求由根 `AGENTS.md` 管理，实施顺序见 `docs/development-plan.md`。这些文件中的要求和计划不得被描述为已经完成的功能。

实现阶段范围为实现、测试、文档、独立下游示例和开发 PR。用户随后已授权审阅并 squash 合并 PR，再通过 CI 发布首个 0.1.0 版本至 PyPI 和 GitHub Release；发布流程见 docs/releasing.md。

---

# 2. 核心功能

`lcl-fastapi` 第一版必须提供以下能力。

1. 项目必须使用 `.lclcfg` 作为服务配置的唯一正式来源。
2. 项目必须使用 `lclang.logger` 管理框架和业务日志。
3. 项目必须使用 `lclang 1.0.10` 提供的 `SnowflakeGenerator` 生成 Request ID。
4. 项目必须使用 FastAPI 提供 REST API 和 OpenAPI。
5. 项目必须在没有互联网和外部 CDN 的环境中正常提供 Swagger UI。
6. 项目必须在 Windows 上使用 Uvicorn 运行。
7. 项目必须在 Linux 上使用 Gunicorn 运行 FastAPI ASGI 应用。
8. 服务必须支持通过配置控制监听地址、端口、worker 数量和生成完整 URL 时使用的外部 origin 元数据。
9. 服务默认必须只监听 `127.0.0.1`，并允许外部 Nginx 提供 HTTPS 和端口转发。
10. 项目必须提供 HTTP 健康检查接口，并通过 `psutil` 返回当前服务和服务器状态。
11. Linux 健康检查结果必须包含当前 Gunicorn master PID。
12. 项目必须提供本机 CLI 查询当前服务状态。
13. 项目必须提供本机 CLI 查询 live worker 最近登记的实际日志文件绝对路径，采用第 15、18 节规定的最终一致性。
14. 项目必须提供安全的服务关闭接口以及相应的本机 CLI。
15. 项目必须提供根据 `.lclcfg` 生成 Nginx 配置文件的 CLI。
16. 项目必须提供根据 `.lclcfg` 生成 systemd unit 文件的 CLI。
17. Nginx 和 systemd CLI 只能生成文件，不得执行系统部署操作。

---

# 3. 不实现的功能

第一版不得实现 SSO、OAuth2、JWT、Bearer Token、用户登录、RBAC 或其他 Auth 功能。

第一版不得实现独立的 Management Service 或 Management Port。

第一版不得实现 HTTP 日志查询接口。

第一版不得实现日志 SSE、WebSocket 或其他日志流式推送能力。

第一版不得实现服务热重启。

第一版不得实现配置动态 reload。

第一版不得实现配置快照或配置文件监听。worker 重建读取配置文件的规则见第 20 节。

第一版只支持单机单服务运行，不处理同一机器上的多个服务实例或跨实例协调。

第一版不得实现 HTTP restart API。

第一版不得自行实现 Snowflake 算法。

第一版不得通过 CLI 自动安装或 reload Nginx。

第一版不得通过 CLI 自动安装、enable、start、stop 或 restart systemd service。

---

# 4. 依赖基线

项目必须运行于 Python 3.14 或更高版本。

核心依赖应保持精简。

```toml
[project]
name = "lcl-fastapi"
requires-python = ">=3.14"

dependencies = [
    "lclang==1.0.10",
    "fastapi>=0.141,<0.142",
    "uvicorn>=0.52,<0.53",
    "winloop==0.6.3; sys_platform == 'win32'",
    "psutil>=7.2,<8",
    "gunicorn>=26,<27; sys_platform == 'linux'",
]
```

项目必须使用 `lclang.cli.CliEntrance` 实现 CLI，不得使用 `argparse`、Click、Typer 或其他替代参数解析器。CLI 参数语法必须遵守 `lclang==1.0.10` 的正式接口，详见第 22 节。

项目不得为了生成 Nginx 或 systemd 文件而引入大型模板框架。

---

# 5. 下游应用接口

下游项目必须能够通过一个最小公开接口创建服务。

```python
from lcl_fastapi import LclFastAPI

service = LclFastAPI()


@service.get("/hello")
async def hello() -> dict[str, str]:
    return {
        "message": "hello",
    }
```

下游也必须能够使用普通 FastAPI `APIRouter`。

```python
from fastapi import APIRouter
from lcl_fastapi import LclFastAPI

service = LclFastAPI()

router = APIRouter()


@router.get("/items")
async def items():
    return []


service.include_router(router, prefix="/api/v1")
```

`LclFastAPI` 应尽可能保持 FastAPI 原有接口习惯。

框架不得提供 `api.prefix` 配置。业务路径完全由装饰器路径、`APIRouter` 和 `include_router(..., prefix=...)` 控制；上述两个例子的业务地址分别是 `/hello` 和 `/api/v1/items`。

下游必须能够使用 `LclFastAPI(lifespan=...)` 提供 FastAPI 风格的异步 lifespan。框架资源先启动，业务 lifespan 随后进入；业务 lifespan 先退出，框架日志随后 flush 并关闭。业务启动失败、取消和正常退出都必须清理已经取得的资源。

公开配置读取接口为 `async get_config(key: str)`，从当前 worker 的 LCL Frame 解析指定配置项。业务 lifespan 和请求处理中均可使用；缺少配置项、解析失败、尚未进入 worker 生命周期时必须给出明确异常。不得建立第二份业务配置格式、环境变量覆盖层或动态配置监听机制。

创建 `LclFastAPI` 对象和导入业务模块不得启动 worker、打开日志文件或建立运行时 Frame。Frame 与 logger 的使用范围必须在文档中说明；模块顶层不得调用依赖这些资源的异步接口。

用户可以覆盖预定义 API。同一 HTTP method 和精确 path 的业务路由必须优先于框架路由，包括通过 `include_router` 注册的业务路由；实现不得因为 FastAPI 的注册顺序使覆盖无效。不同 method 的路由不视为覆盖。所有业务路由必须在服务启动前注册。

文档必须警告：覆盖 `/health`、Swagger/OpenAPI 或 `/_lcl/shutdown` 会改变相应默认能力；尤其覆盖关闭接口可能使 `stop` CLI 不再能够关闭服务。用户覆盖后的业务行为由用户负责，框架不得宣称仍满足被替换接口的默认保证。

```python
class LclFastAPI:

    def include_router(
        self,
        router: APIRouter,
        *,
        prefix: str = "",
        tags: list[str] | None = None,
    ) -> None:
        ...

    def get(self, path: str, **kwargs):
        ...

    def post(self, path: str, **kwargs):
        ...

    def put(self, path: str, **kwargs):
        ...

    def patch(self, path: str, **kwargs):
        ...

    def delete(self, path: str, **kwargs):
        ...
```

---

# 6. 配置模型

所有正式运行参数必须来自 `.lclcfg`。

CLI 不得提供独立的 host、port、workers 或其他正式运行参数覆盖选项。配置路径、JSON 输出开关和生成文件输出路径是命令控制选项，不构成服务运行配置来源。

`server.host` 只允许 `127.0.0.1` 和 `0.0.0.0`，默认必须为 `127.0.0.1`。第一版不支持仅绑定其他网卡地址或 IPv6。

`server.root_path` 保留此配置名称，但含义是对外 origin 元数据，例如 `https://api.example.com:8443`，不是 ASGI 挂载路径。允许空字符串，非空值必须由 `http` 或 `https` 协议、主机和可选端口组成，不得包含用户信息、路径前缀、查询参数或 fragment。它仅在需要生成完整 URL 时提供外部 origin；不得改变 Router 路径，不得传给 FastAPI、Uvicorn 或 ASGI scope 的 `root_path`。第一版的 ASGI `root_path` 必须为空。

相对文件路径以配置文件所在目录为基准，避免从不同工作目录执行本机 CLI 时指向不同运行态。LCL 配置属于受信任的程序配置，不是执行不可信输入的安全沙箱。

一个典型配置必须类似如下内容。

```text
__LCL_VERSION__: 1


# Application.

app.name: "example-service"
app.version: "1.0.0"
app.target: "example_service.app:service"


# Server.

server.host: "127.0.0.1"
server.port: 8080
server.workers: 4
server.root_path: "https://api.example.com"

server.backlog: 2048
server.keep_alive_seconds: 5
server.graceful_timeout_seconds: 30


# Runtime.

runtime.state_dir: "./run"

runtime.pid_file:
    f"{runtime.state_dir}/{app.name}.pid"

runtime.worker_state_dir:
    f"{runtime.state_dir}/workers"


# Health.

health.enabled: True
health.path: "/health"
health.sample_interval_seconds: 1

health.disk_paths: [
    ".",
    logger.file.default.directory,
]


# Swagger.

docs.enabled: True
docs.path: "/docs"
docs.openapi_path: "/openapi.json"


# Request ID.

request.id_header: "X-Request-ID"

snowflake.worker_id_base: 0
snowflake.worker_id_count: 64


# Logger.

logger.file.default.directory: "./logs"

logger.file.service.filename:
    f"{app.name}.{worker_pid}.log"

logger.level: "INFO"


# Nginx rendering.

nginx.server_name: "api.example.com"
nginx.listen_port: 443

nginx.ssl_certificate:
    "/etc/pki/tls/certs/example.crt"

nginx.ssl_certificate_key:
    "/etc/pki/tls/private/example.key"


# systemd rendering.

systemd.service_name: "example-service"
systemd.description: "Example REST Service"

systemd.user: "example"
systemd.group: "example"

systemd.working_directory:
    "/opt/example-service"

systemd.config_path:
    "/etc/example-service/service.lclcfg"

systemd.restart: "on-failure"
systemd.restart_seconds: 5
```

框架必须在实际 worker 启动以后向 LCL Frame 注入运行时变量。

```python
runtime_values = {
    "worker_pid": os.getpid(),
}
```

`worker_pid` 必须由框架提供，不得依赖用户手工配置。

日志配置必须直接使用 `lclang.logger` 的正式 schema。不得引入 `logger.log_dir`、`logger.filename` 等框架别名。`logger.file.default.directory` 提供默认目录，`logger.file.service.filename` 定义 service sink 的文件名，其余 rotation、flush 等选项遵循上游契约。

不同机器部署时，文档必须演示通过 LCL 的正式环境变量能力计算 `snowflake.worker_id_base`，并要求部署者分配不重叠的 ID 区间。不得让框架直接从环境变量绕过 `.lclcfg` 读取这些正式运行参数。

---

# 7. 服务运行模型

## 7.1 Linux

Linux 必须使用 Gunicorn 作为 master/worker process manager。

```text
lcl-fastapi serve
        │
        ▼
Gunicorn master
        │
        ├── worker
        ├── worker
        ├── worker
        └── worker
             │
             ▼
          FastAPI
```

Gunicorn worker 数量必须由：

```text
server.workers
```

控制。

Gunicorn master PID 必须记录到：

```text
runtime.pid_file
```

每个 worker 必须分别建立自己的 LCL Frame、logger runtime、Snowflake generator 和 FastAPI lifespan。

Gunicorn 崩溃恢复创建的新 worker 必须重新从配置文件加载自己的配置；已有 worker 不主动重新读取。服务级监听设置由当前 master 的启动配置决定，worker 重建不改变 master 的监听地址、端口或进程管理设置。

Gunicorn 自带的重复 access log 必须默认关闭。

---

## 7.2 Windows

Windows 必须使用 Uvicorn。

Windows worker 通过 Uvicorn 正式支持的 `winloop:new_event_loop` 接口使用 Winloop；该依赖仅在 Windows 自动安装。进程管理与 worker 恢复仍由 Uvicorn 原生 multiprocess manager 负责。验收必须验证两个 worker 均可处理 HTTP、空闲采样持续更新、worker 恢复及优雅关闭。

```text
lcl-fastapi serve
        │
        ▼
Uvicorn
        │
        ├── worker
        ├── worker
        └── worker
             │
             ▼
          FastAPI
```

worker 数量必须由：

```text
server.workers
```

控制。

Windows 和 Linux 必须使用相同的业务 application、`.lclcfg` 语义、健康 API 和日志接口。

Windows 的服务 PID 必须是负责整个 Uvicorn 运行时的进程 PID；单 worker 模式可以与 worker PID 相同，多 worker 模式必须标识父进程。正常 worker 恢复可以由上游运行时处理，框架不另建 supervisor。

平台差异必须限制在 runtime 实现内部。

---

# 8. Worker 生命周期

每个 worker 必须独立加载自己的运行时环境。

整体生命周期应等价于：

```python
async def worker_main():

    loaded_config = await load_config()

    async with create_lcl_frame(
        loaded_config,
        values={
            "worker_pid": os.getpid(),
        },
    ) as frame:

        logger_config = await resolve_logger_config(frame)

        async with use_logger_handler(logger_config):

            worker_id = acquire_worker_id()

            snowflake = SnowflakeGenerator(
                worker_id=worker_id,
            )

            health_sampler = HealthSampler()

            await health_sampler.start()

            await publish_worker_state()

            try:
                await run_fastapi()
            finally:
                await health_sampler.stop()
                await remove_worker_state()
                release_worker_id(worker_id)
```

应用日志初始化必须发生在实际 worker 内部。

不同 worker 不得共享同一个 logger runtime。

上述伪代码表达资源所有权和清理顺序，不要求嵌套运行事件循环。真实实现必须在 worker 的 FastAPI lifespan 中组合框架和业务资源；CLI 的异步 scope 完全退出后再启动 Uvicorn 或 Gunicorn。业务 lifespan 运行时，`get_config`、`get_logger` 和已初始化的框架状态必须可用。

初始化任一阶段失败时，也必须释放此前已获得的租约、Frame、logger 和 sampler；不得仅在 `run_fastapi` 正常进入以后才建立清理保证。

---

# 9. Request ID

每一个 HTTP request 都必须尝试通过上游 generator 生成一个新的 Snowflake ID；预期发号失败适用本节明确的 503 例外。

框架必须直接使用 `lclang 1.0.10` 的 Snowflake generator。

```python
from lclang.utils import SnowflakeGenerator
```

每个 worker 必须在整个 worker 生命周期中保持一个 generator 实例。

```python
generator = SnowflakeGenerator(
    worker_id=worker_id,
)
```

每个 HTTP request 必须调用：

```python
request_id = str(
    generator.next_id()
)
```

框架不得重新实现 timestamp、sequence、bit composition、clock rollback 或 Snowflake locking。

如果 `next_id()` 因时钟回拨抛出 `RuntimeError`，或因 timestamp/sequence 耗尽抛出 `OverflowError`，框架必须直接返回 HTTP 503，JSON body 为 `{"detail": "Request ID generation unavailable"}`，不得进入业务路由。此时不得生成替代 ID、重试发号或改写上游算法；response 不得伪造 Request ID header，request state 不得保留旧请求的 ID。

预期发号失败仍必须恰好记录一条 access log，明确包含 `request_id=unavailable`、`id_generation_error` 异常类型和正常 access 字段。发送 503 失败或被取消时也必须清理并恢复 Request Context 与 Frame 绑定，不得泄露此前请求的 context。后续请求在上游恢复可发号状态后可以正常处理。

---

# 10. Snowflake Worker ID

虽然 Snowflake 算法由 `lclang` 提供，但是 `lcl-fastapi` 必须保证不同并发 worker 获得不同的 `worker_id`。

必须满足：

```text
0 <= worker_id <= 1023
```

配置必须支持定义可使用的范围。

```text
snowflake.worker_id_base: 0
snowflake.worker_id_count: 64
```

必须满足：

```text
worker_id_base >= 0

worker_id_base + worker_id_count <= 1024

worker_id_count >= server.workers
```

框架必须通过一个轻量的本机跨进程 lease 机制分配 worker ID。

```python
class WorkerIdLease:

    @classmethod
    def acquire(
        cls,
        *,
        state_dir: Path,
        worker_id_base: int,
        worker_id_count: int,
    ) -> "WorkerIdLease":
        ...

    @property
    def worker_id(self) -> int:
        ...

    def release(self) -> None:
        ...
```

该模块只负责分配唯一 worker ID，不得包含任何 Snowflake ID 生成逻辑。

第一版只保证单机单服务内，当前有效并发租约不分配相同的 worker ID。租约必须识别异常退出和 PID 复用，允许回收退出 worker 的 ID；不实现跨服务或跨主机协调。

跨机器部署的 worker ID 区间由用户通过 LCL 环境变量配置并自行保持不重叠。并发租约隔离不等于无限期的历史 Request ID 全局唯一保证；同一 worker ID 在崩溃或快速重启后复用时，生成器的实际保证以 `lclang==1.0.10` 的既有契约为准，框架不得自行修改算法来扩大此承诺。

---

# 11. Request Context

框架必须使用纯 ASGI middleware 管理 Request Context。

Request Context 至少必须包含：

```python
@dataclass(frozen=True, slots=True)
class RequestContext:
    request_id: str
    method: str
    path: str
    started_ns: int
```

框架必须提供：

```python
def get_request_context() -> RequestContext | None:
    ...
```

发号成功时，Request ID 必须同时保存到：

```text
ContextVar
request.state.request_id
HTTP response header
```

默认 response header 必须为：

```text
X-Request-ID
```

客户端自己发送的 `X-Request-ID` 不得代替服务器生成的值。

---

# 12. Logger

项目必须统一使用 `lclang.logger`。

下游必须能够通过简单接口获得 logger。

```python
from lcl_fastapi import get_logger

async def process():
    logger = await get_logger(__name__)
    logger.info("processing")
```

接口应类似：

```python
async def get_logger(
    prefix: str = "",
    emit_level: int = 0,
):
    ...
```

框架必须自动将当前 Request ID 加入 request-scoped 日志。

Request ID 必须由框架 wrapper 在每次实际 emit 时读取当前 Request Context 并注入，不得虚构上游不存在的 logger context 配置键，也不得在创建或缓存 logger 时固定某一次请求的 ID。

业务代码不得需要显式传入：

```python
request_id=request.state.request_id
```

日志 wrapper 必须正确处理 `emit_level`，使日志中的 file、function 和 line 指向业务调用位置，而不是框架 wrapper。

---

# 13. HTTP Access Log

每一个 HTTP request 完成后，框架必须记录一条统一 access log。

至少必须包含：

```text
request_id
method
path
status_code
duration_ms
worker_pid
```

例如：

```text
request_id=193847561923
method=GET
path=/api/v1/items
status_code=200
duration_ms=3.71
worker_pid=28146
```

框架必须避免 Uvicorn 或 Gunicorn 再输出一份无法关联 Request ID 的重复 access log。

框架不得自动记录 Authorization、Cookie、完整 request body 或其他敏感信息。

---

# 14. Worker 独立日志文件

默认情况下，每个 worker 必须写入自己的日志文件。

文件名必须由 `.lclcfg` 中的 LCL 表达式决定。

```text
logger.file.service.filename:
    f"{app.name}.{worker_pid}.log"
```

例如：

```text
example-service.28146.log
example-service.28147.log
example-service.28148.log
example-service.28149.log
```

如果 `lclang.logger` 对实际文件增加 permanent segment、时间戳、sequence 或 rotation suffix，则这些实际文件名由 `lclang.logger` 管理。

框架不得让多个 worker 默认同时写入同一个日志文件。

---

# 15. Worker Runtime State

每个 worker 必须提供轻量本机 runtime state，以支持 `status` 和 `logs` CLI。

默认目录必须为：

```text
<runtime.state_dir>/workers/
```

每个 worker 使用自己的 PID 作为状态文件名。

```text
workers/28146.json
```

状态至少必须包含：

```json
{
  "pid": 28146,
  "process_create_time": 1789021234.125,
  "snowflake_worker_id": 3,
  "log_files": [
    "/var/log/example/example-service.28146.000001.log"
  ]
}
```

如果当前 active log segment 发生变化，worker 必须在下一次运行态刷新时更新 `log_files`。active log path 查询采用最终一致性，正常情况下的最大刷新延迟不得超过 `health.sample_interval_seconds` 加一次指标采样和状态写入耗时；不得承诺轮转后瞬时可见。即使关闭健康 HTTP 接口，也必须维持此运行态刷新能力。

状态文件必须使用 atomic replace 更新。

CLI 必须通过 `psutil` 验证 PID 和 process create time，因此不得把旧进程遗留的状态文件误判为当前 worker。

worker state 必须关联当前服务运行生命周期，防止仅因某个旧 PID 仍存在就被计为当前 worker。读取缺失、损坏或 stale 的状态必须给出明确结果，不得把它作为有效 worker。

---

# 16. 健康检查 API

框架必须提供健康检查 API。

默认接口为：

```http
GET /health
```

实际路径由：

```text
health.path
```

配置。

如果：

```text
health.enabled: False
```

则不得注册该接口。

健康检查必须使用 `psutil` 获取服务器和进程信息。

返回结果至少必须包含：

```json
{
  "status": "UP",
  "service": {
    "name": "example-service",
    "version": "1.0.0",
    "runtime": "gunicorn",
    "service_pid": 28140,
    "gunicorn_pid": 28140,
    "worker_pid": 28146,
    "configured_workers": 4,
    "running_workers": 4,
    "worker_pids": [
      28146,
      28147,
      28148,
      28149
    ],
    "uptime_seconds": 3600
  },
  "server": {
    "hostname": "server01",
    "cpu": {
      "logical_count": 16,
      "usage_percent": 22.1
    },
    "memory": {
      "total_bytes": 68719476736,
      "available_bytes": 44122972160,
      "usage_percent": 35.8
    },
    "disk": [
      {
        "path": "/",
        "total_bytes": 536870912000,
        "free_bytes": 310420684800,
        "usage_percent": 42.2
      }
    ]
  }
}
```

Linux 必须满足：

```text
runtime = "gunicorn"

service_pid = Gunicorn master PID

gunicorn_pid = Gunicorn master PID

worker_pid = 当前处理该请求的 worker PID
```

Windows 必须满足：

```text
runtime = "uvicorn"

gunicorn_pid = null

worker_pid = 当前处理该请求的 worker PID
```

---

# 17. Health Sampler

CPU 等指标不得通过阻塞式采样直接发生在 `/health` request 内。

框架必须维护轻量后台 sampler。

```python
class HealthSampler:

    async def start(self) -> None:
        ...

    async def stop(self) -> None:
        ...

    def snapshot(self) -> HealthSnapshot:
        ...
```

采样周期必须由：

```text
health.sample_interval_seconds
```

控制。

HTTP `/health` 应主要读取最近一次 snapshot。

某一个磁盘或系统指标无法获得时，不得因此让整个 health endpoint 返回 HTTP 500。

局部错误必须标记为 unavailable，并记录 warning。

首次采样尚不可用或部分指标无权限读取时必须返回明确的不可用字段；服务仍可处理请求时不得仅因监控指标缺失就变成 HTTP 500。warning 必须避免在每次请求中重复刷屏。关闭健康路由不会关闭 `status`、`logs` 和 `stop` 所需的运行态维护。

---

# 18. 当前日志文件查询

第一版不得提供 HTTP 日志 API。

当前日志文件必须通过本机 CLI 查询。

```bash
lcl-fastapi logs -o config service.lclcfg
```

CLI 必须返回所有当前 live worker 最近登记的实际日志文件绝对路径。轮转后的更新遵循第 15 节的最终一致性，不保证命令执行时刻的瞬时精确视图。

例如：

```text
/var/log/example/example-service.28146.000001.log
/var/log/example/example-service.28147.000001.log
/var/log/example/example-service.28148.000001.log
/var/log/example/example-service.28149.000001.log
```

CLI 必须支持：

```bash
lcl-fastapi logs \
    -o config service.lclcfg \
    -o json
```

CLI 必须根据 live worker state 返回 active path，而不得通过目录中的 `mtime` 猜测当前日志文件。

已经退出 worker 的日志文件不得作为当前 active log 返回。

JSON 输出必须包含 `paths`、`observed_at` 和 `stale`：`paths` 是去重后的绝对路径列表；`observed_at` 是参与结果的 worker 日志视图中最早的采集 Unix 秒数，无视图时为 `null`；`stale` 表示仍存活 worker 的日志视图存在超期情况。已经退出的 worker 不得因为此标记重新进入结果。普通文本输出只逐行列出路径。

`stale` 的超期判定为观测年龄超过配置采样间隔；该标记不表示 worker 已停止。一次刷新还需要指标采样和状态写入时间，CLI 本身也需要验证进程身份，因此正常工作的 worker 可能短暂返回 `stale: true`，不得承诺每次 CLI 调用都返回 `false`。

---

# 19. 服务关闭

框架必须保留一个内部 HTTP shutdown endpoint。

默认接口为：

```http
POST /_lcl/shutdown
```

该接口不得进入 OpenAPI。

该接口不得出现在 Swagger。

Nginx 配置生成器必须默认显式阻断该 URL。

服务启动时必须生成随机 control token。

```text
<runtime.state_dir>/control.token
```

本机 CLI 必须使用该 token 调用 shutdown API。

```http
POST /_lcl/shutdown

X-LCL-Control-Token: <token>
```

token 不匹配时必须返回拒绝响应。

核心逻辑应类似：

```python
async def shutdown(request: Request):

    supplied = request.headers.get(
        "X-LCL-Control-Token"
    )

    if supplied is None or not secrets.compare_digest(
        supplied,
        runtime.control_token,
    ):
        raise HTTPException(status_code=403)

    request_shutdown_after_response()

    return Response(status_code=202)
```

shutdown 必须先返回 HTTP response，然后 graceful shutdown 整个服务。

Linux 必须最终终止 Gunicorn master。

Windows 必须最终终止 Uvicorn runtime。

FastAPI lifespan 和 `lclang.logger` 必须能够正常完成退出和日志 flush。

control token 和 runtime identity 必须属于本次完整服务启动，不由各 worker 分别生成。token 文件应仅允许服务账户读取，并在当前实例清理时移除；不得把 token 写入业务日志、access log 或公开健康响应。

关闭请求缺少 token 和 token 错误都必须返回 403。HTTP 202 表示已接受关闭请求；本机 `stop` 必须继续等待已验证的服务进程退出，在 graceful timeout 内不能完成时返回明确失败，不得向未知 PID 补发终止信号。

以上是框架默认关闭 API 的契约。用户显式覆盖同一路由时适用第 5 节的覆盖规则和警告。

---

# 20. 不支持 Restart

框架不得提供：

```text
POST /restart
lcl-fastapi restart
```

框架不得监听 `.lclcfg` 文件变化。

框架不得创建配置快照。完整服务启动时读取服务级设置；每个实际 worker 启动或崩溃重建时分别读取当前 `.lclcfg` 并建立自己的 Frame。已有 worker 不因配置文件修改而自动更新。

```text
start
  ↓
load .lclcfg
  ↓
run
  ↓
stop
  ↓
start again
  ↓
load latest .lclcfg
```

不建议在服务运行中修改配置文件，否则旧 worker 和重建后的 worker 可能读取不同配置。要可靠、统一地应用新配置，必须由外部运维系统执行完整 restart。master 的监听地址、端口和 worker 管理设置不会因单个 worker 重建而改变。

`status`、`logs` 和 `stop` 使用给定配置定位运行态，再依据当前运行态中的身份、地址和端口操作当前服务；不得用后来修改的端口误操作另一个服务。改变运行态目录后，需要使用仍能定位原实例状态的配置才能管理原实例。

---

# 21. Offline Swagger

FastAPI 默认外部 CDN Swagger 资源不得直接使用。

`lcl-fastapi` wheel 必须携带 Swagger UI static files。

例如：

```text
lcl_fastapi/
└── static/
    └── swagger/
        ├── swagger-ui-bundle.js
        ├── swagger-ui.css
        └── favicon-32x32.png
```

框架必须关闭 FastAPI 默认 Swagger 页面。

```python
FastAPI(
    docs_url=None,
    redoc_url=None,
)
```

然后自行注册配置指定的：

```text
/docs
/openapi.json
```

生成的 Swagger HTML 只能访问当前服务本地 static URL。

服务完全断网后必须仍然能够打开 Swagger、加载 OpenAPI schema 并执行 REST request。

第一版不要求提供 ReDoc。

`docs.enabled: False` 时不得注册框架的 Swagger 页面、OpenAPI endpoint 和 Swagger 静态资源路由；用户显式注册的同名业务路由不受影响。离线资源必须包含正确的上游许可证与版本出处，wheel 和源码分发包都必须携带运行必需资源。

---

# 22. CLI

CLI 入口必须统一为：

```text
lcl-fastapi
```

第一版只需要提供：

```text
lcl-fastapi serve
lcl-fastapi status
lcl-fastapi logs
lcl-fastapi stop
lcl-fastapi nginx render
lcl-fastapi systemd render
```

所有命令都必须使用 `.lclcfg`。

CLI 必须使用正式 `CliEntrance` 的 `-o name value` 语法：`-o config service.lclcfg` 指定服务配置；`status` 和 `logs` 可使用 `-o json`；两个 render 命令可使用 `-o output 文件名`。不得提供旧式 `-c`、`--config`、`--json` 或自定义参数解析器。不得接受这些命令选项以外的运行参数覆盖；不适用于该命令的 `json` 或 `output` 选项必须拒绝。

`config` 是普通的配置路径参数。必须由命令 handler 使用正式 LCL 加载接口独立加载服务文件，不得把服务配置文件当作 `CliEntrance` 自身的 CLI Frame。CLI 自身日志必须仅写 stderr，不得污染 render 或 JSON 的 stdout，也不得为查询命令创建服务日志文件。

CLI 的 `-h` / `--help` 和 `-v` / `--version` 遵循上游正式能力。入口适配可以为上游传入其接受的 Python script argv，但不得另建一套 parser。`serve` handler 只准备启动参数，必须在 `CliEntrance` 的 async scope 和事件循环退出后启动平台运行时。

---

## 22.1 `serve`

```bash
lcl-fastapi serve \
    -o config service.lclcfg
```

该命令必须根据平台自动选择 Uvicorn 或 Gunicorn。

```python
def launch_runtime(settings):

    if sys.platform == "win32":
        run_uvicorn(settings)

    elif sys.platform.startswith("linux"):
        run_gunicorn(settings)

    else:
        raise UnsupportedPlatformError()
```

下游用户不得需要自己执行 `uvicorn` 或 `gunicorn`。

---

## 22.2 `status`

```bash
lcl-fastapi status \
    -o config service.lclcfg
```

该命令必须通过 pidfile、worker state 和 `psutil` 查询本机服务。

示例输出：

```text
Service       example-service
Status        RUNNING
Runtime       gunicorn
Service PID   28140
Gunicorn PID  28140
Workers       4 / 4
Worker PIDs   28146, 28147, 28148, 28149
Uptime        01:04:13
```

必须支持：

```bash
lcl-fastapi status \
    -o config service.lclcfg \
    -o json
```

---

## 22.3 `logs`

```bash
lcl-fastapi logs \
    -o config service.lclcfg
```

该命令只返回第 15、18 节规定的 live worker 最近发布的实际日志路径视图，遵守最终一致性和 stale 标记。

必须支持：

```bash
lcl-fastapi logs \
    -o config service.lclcfg \
    -o json
```

---

## 22.4 `stop`

```bash
lcl-fastapi stop \
    -o config service.lclcfg
```

该命令必须从 runtime state 获得 control token，并通过 `127.0.0.1` 调用内部 shutdown API。

```python
async def stop(config):

    runtime = inspect_runtime(config)

    token = read_control_token(
        runtime.state_dir
    )

    response = await post(
        host="127.0.0.1",
        port=runtime.port,
        path="/_lcl/shutdown",
        headers={
            "X-LCL-Control-Token": token,
        },
    )

    require_status(response, 202)
```

CLI 不得向无法验证身份的未知 PID 发送终止信号。

---

# 23. Nginx 配置生成

CLI 必须支持：

```bash
lcl-fastapi nginx render \
    -o config service.lclcfg
```

默认必须输出到 stdout。

必须支持：

```bash
lcl-fastapi nginx render \
    -o config service.lclcfg \
    -o output example.conf
```

生成器只能生成文件。

生成器不得：

```text
安装配置
执行 nginx -t
reload Nginx
restart Nginx
修改系统目录
```

Nginx 配置必须将外部请求代理到：

```text
127.0.0.1:<server.port>
```

并设置必要的 forwarded headers。

```nginx
proxy_set_header Host              $host;
proxy_set_header X-Real-IP         $remote_addr;
proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_set_header X-Forwarded-Host  $host;
```

生成器必须明确阻断：

```text
/_lcl/shutdown
```

例如：

```nginx
location = /_lcl/shutdown {
    return 404;
}
```

`server.root_path` 是外部 origin，不能生成路径 prefix 或 rewrite。框架第一版不提供外部挂载前缀；Nginx 必须阻断根路径的 `/_lcl/shutdown`。

`nginx.server_name`、`nginx.listen_port` 和证书配置必须保留，决定实际生成的 Nginx 监听、域名与 TLS 配置。外部 origin 可描述上层代理、NAT 或公网端口映射，因此不得强制把 origin 的端口当作 Nginx 监听端口或后端 `server.port`。文档必须说明公网 origin 与实际监听字段可能不同及其部署责任。

生成完整 URL 时可以使用已配置的外部 origin；本机 `stop` 必须继续访问 `127.0.0.1`，不得使用对外 origin。未配置 origin 时，渲染器仍依据自身 Nginx 字段工作。

---

# 24. systemd 配置生成

CLI 必须支持：

```bash
lcl-fastapi systemd render \
    -o config service.lclcfg
```

以及：

```bash
lcl-fastapi systemd render \
    -o config service.lclcfg \
    -o output example.service
```

生成器只能生成 unit 文件。

生成器不得执行任何 `systemctl` 命令。

生成结果必须以统一入口启动服务。

```ini
[Service]

WorkingDirectory=/opt/example-service

ExecStart=/opt/example-service/.venv/bin/lcl-fastapi \
    serve \
    -o config /etc/example-service/service.lclcfg

Restart=on-failure
```

systemd 仅属于可选部署环境，不得成为 `lcl-fastapi` 运行的必要条件。

---

# 25. Runtime State

运行时状态目录建议采用：

```text
run/
├── example-service.pid
├── runtime.json
├── control.token
├── worker-id.lock
├── worker-ids/
│   ├── 0.json
│   ├── 1.json
│   └── ...
└── workers/
    ├── 28146.json
    ├── 28147.json
    └── ...
```

所有状态都只能用于当前进程生命周期协调和本机 CLI 查询。

状态文件不得作为持久业务数据。

JSON 状态更新必须使用 atomic replace。

CLI 必须能够识别并忽略异常退出留下的 stale state。

---

# 26. 推荐代码结构

```text
lcl-fastapi/
├── pyproject.toml
├── README.md
├── LICENSE
│
├── src/
│   └── lcl_fastapi/
│       ├── __init__.py
│       ├── service.py
│       ├── config.py
│       ├── context.py
│       │
│       ├── logging.py
│       │
│       ├── middleware/
│       │   └── request_context.py
│       │
│       ├── runtime/
│       │   ├── common.py
│       │   ├── state.py
│       │   ├── worker_id.py
│       │   ├── windows.py
│       │   └── linux.py
│       │
│       ├── health/
│       │   ├── models.py
│       │   ├── sampler.py
│       │   └── router.py
│       │
│       ├── docs/
│       │   └── swagger.py
│       │
│       ├── static/
│       │   └── swagger/
│       │
│       ├── render/
│       │   ├── nginx.py
│       │   └── systemd.py
│       │
│       └── cli/
│           ├── main.py
│           ├── serve.py
│           ├── status.py
│           ├── logs.py
│           ├── stop.py
│           ├── nginx.py
│           └── systemd.py
│
├── examples/
│   └── minimal/
│
├── docs/
│   ├── quick-start.md
│   ├── configuration.md
│   ├── logging.md
│   ├── health.md
│   ├── windows.md
│   ├── linux.md
│   ├── nginx.md
│   └── systemd.md
│
└── tests/
```

项目不得再包含自研：

```text
snowflake.py
auth/
management/
supervisor/
log_stream/
restart/
```

---

# 27. README 和示例项目

包未来正式发布到 PyPI 后，用户应能够执行：

```bash
pip install lcl-fastapi
```

本次开发不发布 PyPI。当前验收必须先构建 wheel，再在隔离环境通过 `pip install <wheel 路径>` 安装并运行下游示例；不得以尚未发布的包名安装成功作为本次 PR 的前置条件，也不得因此擅自发布。

然后编写：

```python
from lcl_fastapi import (
    LclFastAPI,
    get_logger,
)

service = LclFastAPI()


@service.get("/hello")
async def hello():

    logger = await get_logger(__name__)

    logger.info("hello")

    return {
        "message": "hello",
    }
```

并执行：

```bash
lcl-fastapi serve \
    -o config service.lclcfg
```

README 必须至少说明以下内容。

1. README 必须说明 `lcl-fastapi` 的项目定位。
2. README 必须提供最小可运行示例。
3. README 必须提供 `.lclcfg` 示例。
4. README 必须说明 Windows 使用 Uvicorn。
5. README 必须说明 Linux 使用 Gunicorn。
6. README 必须说明 Nginx HTTPS 部署关系。
7. README 必须说明 Request ID 与 logger 的关联方式。
8. README 必须说明 `/health`。
9. README 必须说明 `status`、`logs` 和 `stop` CLI。
10. README 必须明确说明不支持热重启，配置变化需要完整重启服务。

README 和正式公开使用文档必须使用英文，并说明单机单服务限制、重建 worker 会读取新配置、业务路由优先、日志路径最终一致以及 `server.root_path` 的 origin 语义。

仓库必须包含两个下游示例：最小服务，以及组合 Router prefix、业务配置、lifespan 和日志的服务。每个示例必须分别由一个全新的子 Agent 根据公开文档和已经构建的 wheel 编写并验证，用于检验文档是否足以指导下游使用。示例 Agent 不得依靠提前提供的内部实现说明替代文档；发现缺口后必须反馈，由负责文档和实现的 Agent 修正并重新验证。

示例不得进入 wheel。示例的配置、运行步骤和观察结果必须自包含，不依赖账户、生产凭据或外部网络服务。

---

# 28. 测试要求

项目至少必须覆盖以下测试。

Request middleware 测试必须验证每个 request 得到不同的 Snowflake ID。

测试必须验证 Snowflake ID 来自 `lclang SnowflakeGenerator`，而不是框架自研算法。

测试必须用真实上游 generator 和受控时钟验证 clock rollback 与 sequence exhaustion 返回 503、不生成替代 ID、不进入业务路由、恰好记录一次失败 access log，并在正常发送、发送失败和取消后恢复上下文。上游恢复后后续请求必须可正常发号。

测试必须验证并发 worker 不会取得相同的 Snowflake `worker_id`。

测试必须验证客户端提供的 `X-Request-ID` 不会覆盖服务端 ID。

测试必须验证 Request ID 被写入 response Header。

测试必须验证 Request ID 被写入 `request.state`。

测试必须验证 Request ID 自动进入 logger context。

测试必须验证不同 `worker_pid` 产生不同默认日志文件名。

测试必须验证 `lcl-fastapi logs` 只返回 live worker 的 active log files。

测试必须验证日志 rollover 后返回新的 active file path。

测试必须验证 `/health` 返回 CPU、内存和磁盘状态。

Linux 集成测试必须验证 `/health` 返回正确的 Gunicorn master PID。

Windows 测试必须验证 `gunicorn_pid` 为 `null`。

测试必须验证 Offline Swagger 页面不引用外部 CDN。

测试必须验证 shutdown endpoint 不进入 OpenAPI。

测试必须验证无正确 control token 时无法关闭服务。

测试必须验证 `lcl-fastapi stop` 可以 graceful shutdown 服务。

测试必须验证 Nginx renderer 阻断 shutdown endpoint。

测试必须验证 Nginx 和 systemd render 不产生任何系统副作用。

测试必须验证 CLI 使用正式 `CliEntrance` 语法，拒绝 argparse 风格参数和运行参数覆盖，且 JSON/render stdout 不混入日志。

测试必须验证 LCL 正式 logger schema、业务 `get_config`、业务 lifespan 的初始化和退出顺序、启动失败清理以及请求 context 的并发隔离。

测试必须验证 direct decorator 和 `include_router` 注册的同 method/path 业务路由都能覆盖预定义 API，不受内部注册顺序影响。

测试必须验证 `server.root_path` 作为 origin 被校验但不进入 ASGI `root_path`，不会更改业务路径，也不会把公网端口强加给 Nginx 或后端监听。

测试必须验证 worker 重建可读取修改后的文件、现有 worker 不主动 reload，以及单次服务生命周期内 master 监听设置不随之变化。

两平台真实进程集成测试必须覆盖多 worker 启动、worker 恢复、PID identity 验证和 graceful shutdown；不能仅用 mock 声称支持 Windows 或 Linux。工程门禁必须遵循 `AGENTS.md`，包括 100% 生产分支覆盖、strict mypy、文档执行和包资源检查；确实只能在特定平台执行的分支必须由对应平台的实际测试证据覆盖，不得通过排除或虚构 skip 降低要求。

---

# 29. 第一版验收标准

两个独立下游示例项目必须纳入 GitHub Actions 自动化验收。CI 必须先从当前提交隔离构建 wheel，随后在 Windows/Uvicorn 和 Linux/Gunicorn 上分别为每个示例建立独立虚拟环境，安装该 wheel 和示例项目，不得通过 PYTHONPATH 导入仓库源码替代安装。四个独立任务必须验证服务可用性、业务及内建各 HTTP API、全部 CLI 命令和 graceful shutdown，保留执行报告与失败诊断；最终 PR 最新提交的四个任务均须通过。示例项目不得进入 wheel。

只有同时满足以下条件，`lcl-fastapi` 第一版才视为完成。

下游项目能够通过 `pip install <构建的 wheel 路径>` 安装框架；PyPI 发布属于后续独立请求，不属于本次验收。

下游项目能够通过一个 `LclFastAPI` 对象定义业务服务。

下游项目能够通过一份 `.lclcfg` 控制运行参数。

Windows 能够使用 Uvicorn 正常运行。

Linux 能够使用 Gunicorn 正常运行。

服务默认只监听 `127.0.0.1`。

Swagger 在完全隔离网络环境中能够正常使用。

正常发号的 HTTP request 能够获得由 `lclang 1.0.10 SnowflakeGenerator` 产生的 Request ID；上游预期发号失败必须满足第 9 节的明确 503、无伪造 ID 和单条失败 access log 例外。

每个并发 worker 都使用不同的 Snowflake worker ID。

Request ID 会自动进入 response Header、request state 和 logger context。

每个 worker 默认写入独立日志文件。

默认日志文件名通过 LCL 和 `worker_pid` 决定。

`/health` 能够返回服务、worker、Gunicorn PID 和服务器资源状态。

`lcl-fastapi status` 能够在本机查询当前服务。

`lcl-fastapi logs` 能够返回 live worker 最近发布的 active log file 绝对路径，符合约定的最终一致性、更新时间和 stale 语义。

`lcl-fastapi stop` 能够安全关闭整个服务。

Nginx CLI 只能生成配置文件。

systemd CLI 只能生成配置文件。

项目不存在 Management Service、Auth、Restart、Config Reload 或 Log Streaming。

两个全新示例 Agent 均能够仅依据公开文档安装 wheel、编写并运行各自示例，且 wheel 中不存在下游示例文件。

开发 PR 的最新 head 通过实际适用检查，提交实现、测试和文档一起供用户审阅；不合并 PR，不发布版本。任何不能运行的平台检查或外部权限限制必须如实列为未完成项，不得宣称全部验收通过。

---

# 30. 最终职责边界

`lclang 1.0.10` 负责 `.lclcfg`、CLI 基础能力、正式日志能力和 Snowflake ID 生成算法。

`lcl-fastapi` 负责 FastAPI application assembly、跨平台运行时、Snowflake worker ID 分配、Request Context、Request ID 传播、健康检查、active log path 登记、本机运维 CLI、Offline Swagger、Nginx 配置生成和 systemd unit 生成。

FastAPI 负责 REST API 和 OpenAPI。

Uvicorn 负责 Windows ASGI 服务运行。

Gunicorn 负责 Linux master/worker 进程管理。

`psutil` 负责跨平台的服务进程和服务器状态查询。

Nginx 负责实际部署环境中的外部 HTTP/HTTPS、TLS 和 reverse proxy。

systemd 可以负责 Linux 部署环境中的服务生命周期管理。

下游项目只负责业务 Router、业务 lifespan、业务 `.lclcfg` 配置和业务代码。

以上职责边界构成 `lcl-fastapi` 第一版的完整开发基线。
