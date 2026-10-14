# 本地试玩真实计时 · 2026-10-14

最新完整链路：真实模型刷新到新卡片 **16.308 秒**，交互版 MarkItDown 缺少应用镜像时自动校验并导入本地包，点击启动到体验入口 **14.650 秒**（后端 ready **12.361 秒**）。包已在本机，不含网络下载或 Docker 安装；与下方预装镜像热启动样本条件不同。实际粘贴/上传/下载、玩过确认、停止与回收通过，最新回归 **232/232**。见 [完整链路报告](LOCAL_CHAIN_2026-10-14.md)。

本机实测：MarkItDown 0.1.8 基础 HTML 转 Markdown 试玩首次就绪 **96.249 秒**，再次启动 **83.806 秒**。两次均完成实际转换并校验输出。这是两个样本，不能推断其他工具、网络或用户机器的耗时。

同日追加优化对照：把依赖与测试入口预装到固定镜像后，三次就绪分别为 **4.615、3.010、3.190 秒**，每次均使用新工作区并校验实际转换结果。下面先说明优化样本，再保留原始安装基线。

## 预装镜像对照

| 项目 | 实测 |
| --- | ---: |
| 下载 wheels 的受限准备会话 | 35.313 秒 |
| 离线、非 root 构建镜像 | 15.114 秒 |
| 一次成功准备合计（含复制与检查） | 50.694 秒 |
| 准备好后的第 1 / 2 / 3 次就绪 | 4.615 / 3.010 / 3.190 秒 |
| 最终镜像展开大小 | 432,239,507 字节 |

准备阶段的 Docker 引擎已经启动，基础镜像已经存在。之前因找不到 BuildKit 插件失败的调试轮未计入这次成功准备时间。固定镜像没有发布到远端仓库，因此用户首次下载该镜像的耗时、压缩传输大小和其他机器的启动耗时尚未测量。

成功准备时仅下载 wheel，逐个记录文件哈希；镜像组装使用第一方 Dockerfile、非 root、无网络安装。应用及依赖位于只读镜像的 `/opt/viberadar/`，不从试玩后的用户工作区取缓存。每次运行仍保留新的会话工作区、断网和资源限制。

运行时仅对已在本地的固定摘要/内容 ID 镜像跳过远端拉取；可变标签仍会检查远端，缺失的本地构建镜像明确要求先准备。relay 也已固定摘要。该行为已加入回归测试。

追加原始证据：[镜像准备](../../.local/runtime-benchmark/prepared-image-20261014061438.json)、[三次启动](../../.local/runtime-benchmark/markitdown-prepared-20261014061610.json)、[真实隔离探针](../../.local/runtime-benchmark/prepared-isolation-probe.json)。回归测试 **222/222 通过**。

复跑：

```powershell
.venv\Scripts\python.exe tools/prepare_benchmark_image.py
# 使用命令返回的完整内容 ID，不使用可变标签：
.venv\Scripts\python.exe tools/benchmark_trial.py --tool markitdown --runs 3 --prepared-image sha256:57ff3d0bcef71f45a578ed5136edd9332314b702964199f9b5bdace0912d629f
```

这是基础 HTML 转换的预装测试镜像，尚未作为公开配方发行，也没有完成五个精选配方的完整核验。Docker 的固定摘要与构建缓存机制见 [固定摘要文档](https://docs.docker.com/reference/cli/docker/image/pull/#pull-an-image-by-digest-immutable-identifier)、[构建缓存文档](https://docs.docker.com/build/cache/)。

## 体积实测

同日用 `tools/measure_artifact_size.py` 实际导出固定镜像并 gzip 压缩：

| 项目 | 字节数 | 十进制体积 |
| --- | ---: | ---: |
| 镜像展开大小 | 432,239,507 | 432.24 MB |
| `docker image save` 输出 | 108,354,560 | 108.35 MB |
| gzip 后的镜像归档 | 107,779,619 | 107.78 MB |
| 当次 Git 可见源码、前端、配置、文档和测试 ZIP 快照 | 386,372 | 386.37 KB |

镜像导出并压缩用了 3.417 秒。Docker 的导出已包含压缩层，因此 gzip 只进一步减少了一小部分体积；不能把展开大小当作实际归档下载量。

源码 ZIP 排除了 `.local`、Python 环境、Docker 镜像和运行数据，不是完整桌面安装包；当前没有测得包含 Python 运行环境的最终安装包体积。MarkItDown 的依赖没有加入 VibeRadar 主程序的 Python 依赖清单。

镜像应独立按需分发和缓存，主程序只带配方与启动逻辑，避免把全部工具镜像装进主程序包。没有 Docker 的用户还需环境安装；本次官方 Docker 安装包另为 636,811,696 字节。实际网络传输耗时仍未测量。

证据：[体积记录](../../.local/runtime-benchmark/package-size.json)、[镜像归档](../../.local/runtime-benchmark/markitdown-prepared.tar.gz)、[源码快照](../../.local/runtime-benchmark/viberadar-source.zip)。这些本地文件没有被发布。

## 测量条件

- Windows 11 专业版 build 26200，Intel Core Ultra 7 265K，约 32 GB 内存。
- WSL 2.6.3 已预先安装；此次没有测量 WSL 的安装时间。
- 本次安装 Docker Desktop（D 盘），Docker Engine 29.8.2。
- 基础镜像 `python:3.13-slim`；实测摘要 `sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f`。
- 容器非 root、只读根目录、1 CPU、512 MiB、128 PID，仅挂载本会话工作区；应用只接入 isolated internal 网络。
- MarkItDown 安装基础包，通过测试适配页面展示真实 HTML 转换结果；没有测试 PDF、Office、OCR 等可选依赖，也没有调用模型。

## 环境准备
| 阶段 | 实测时间 | 结果 |
| --- | ---: | --- |
| 官方安装包下载（636,811,696 字节） | 95.188 秒 | 完成，Docker Inc 数字签名有效 |
| 第一次默认位置安装 | 89.618 秒 | 失败；`.docker` 为指向 D 盘的目录联接，无法跨盘创建硬链接 |
| 改为同盘安装 | 29.374 秒 | 成功，安装位置 `D:/DevCache/DockerDesktop` |
| 一次成功的引擎启动至检测可用 | 5.739 秒 | 成功；包含轮询间隔，首次启动调试期间的中断另行保留在日志中 |

这些是分阶段计时，不是首次配置的端到端总时间；排查、修改和交互间隔没有被相加。不能据此宣称每位用户首次配置只需要固定分钟数。

## 试玩等待

计时从调用创建会话开始，到程序健康检查通过并进入 ready。清理时间单独计量，不包含在 ready 内。

| 测试 | 缓存条件 | 就绪时间 | 拉取镜像命令耗时 | 安装依赖阶段 | 清理时间 |
| --- | --- | ---: | ---: | ---: | ---: |
| Python 基础容器，第 1 次成功启动 | 镜像已保留，无依赖安装 | 8.705 秒 | 见原始命令记录 | 不需要 | 1.884 秒 |
| Python 基础容器，第 2 次 | 镜像已保留，无依赖安装 | 9.019 秒 | 见原始命令记录 | 不需要 | 1.402 秒 |
| MarkItDown，第 1 次成功启动 | 测试镜像事先移除；新工作区 | 96.249 秒 | 16.722 秒 | 71.193 秒 | 8.700 秒 |
| MarkItDown，第 2 次 | 镜像已保留；新工作区；依赖重新安装 | 83.806 秒 | 6.289 秒 | 69.808 秒 | 6.199 秒 |

安装阶段包含受限网络下载、安装过程及状态轮询。拉取镜像列是各 pull 命令的总时间，包含镜像已有时检查远端清单的时间。各列之外还有网络、relay、应用创建与健康检查开销。

原始安装配方每个新会话重新安装依赖，因此第二次等待仍然较长。预装原型的结果见上方追加对照；原始配方并没有被自动转换为预装镜像，未提供固定构建产物的工具仍需安装。

## 实测发现与修复

- internal 网络无法直接发布应用端口：改为在受限 relay 上发布 loopback 入口，入口固定转发到本会话应用，支持 HTTP/SSE/WebSocket；应用保留独立 isolated internal 网络。
- 本机 TUN 代理给 PyPI 返回 `198.18.*` Fake-IP：只对该测试网段使用固定 Cloudflare HTTPS DNS 查询，再次验证答案必须为公网 IP；其他私网解析仍拒绝，不连接 Fake-IP。安装白名单和运行期禁用包源保持有效。[Cloudflare DNS JSON 文档](https://developers.cloudflare.com/1.1.1.1/encryption/dns-over-https/make-api-requests/dns-json/)
- Docker logs 的 stderr 原先丢失：已保留应用 stderr，便于定位依赖错误。
- MarkItDown 安装临时文件超过 128 MiB `/tmp`：测试配方把安装临时文件放入本会话工作区。
- 已运行的 Web 进程 PATH 不随安装更新：Docker CLI 可通过安装注册表记录定位，已实测旧 PATH 情况下找到 D 盘安装并连接引擎。

保留失败样本：修复前基础容器 23.071 秒后因入口不可用失败；MarkItDown 的两次 Fake-IP 安装失败分别为 33.517 秒和 18.064 秒；修复 DNS 后因临时空间不足的失败为 44.867 秒。成功计时没有把这些失败伪装成可用结果。

## 真实隔离探针与回归

第一方真实探针通过：只读/非 root/挂载/资源约束符合预期，直接外网连接被阻断，运行期包源被阻断。新增宿主检查使用一个临时 benign HTTP 监听器：relay 能访问该监听器作为阳性对照，应用在显式绕过代理后仍不能访问宿主。全部测试容器、测试网络与会话工作区已清理。

自动化回归 **217/217 通过**，包括 HTTP/SSE 固定转发、WebSocket 握手及帧、Fake-IP 处理和私网答案拒绝。此探针与计时不构成五个精选配方或全面红队验收，现有五个候选仍是未核验 guided 草稿。

原始记录保存在 `.local/runtime-benchmark/`：

- [环境计时](../../.local/runtime-benchmark/environment-timing.json)
- [Python 成功样本](../../.local/runtime-benchmark/python-20261013181844.json)
- [MarkItDown 成功样本](../../.local/runtime-benchmark/markitdown-20261013182406.json)
- [真实隔离探针](../../.local/runtime-benchmark/isolation-probe.json)

复跑命令：

```powershell
.venv\Scripts\python.exe tools/benchmark_trial.py --tool python --runs 2
.venv\Scripts\python.exe tools/benchmark_trial.py --tool markitdown --runs 2 --cold-image
.venv\Scripts\python.exe tools/qa_try_runtime.py
```

`--cold-image` 会不带 force 删除指定测试镜像；被其他容器引用时 Docker 应拒绝，避免影响已有容器。测试配方只注册到隔离测试库，不改变随项目提供的配方核验状态。
