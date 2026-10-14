# 本地发现到体验链路验收 · 2026-10-14

已跑通一个已核验工具的完整链路：空 Feed → 点击刷新 → 真实模型生成新卡片 → 一键启动 → 自动校验并导入本地镜像包 → 打开工具 → 实际输入并转换 → 下载结果 → 确认玩过 → 停止并回收。

本次固定发现输入为真实的 Microsoft MarkItDown 仓库，没有预先插入产品或卡片。模型使用用户配置的 DeepSeek Flash；采集管线、模型请求、浏览器、数据库、Docker 和转换均真实执行。该验收不证明任意新发现的项目都能自动安装，未知项目仍需要核验配方。

## 实测结果

| 环节 | 结果 |
| --- | --- |
| 刷新按钮到新卡片可见 | 16.308 秒 |
| 刷新新增 | 1 条原始输入、1 张卡片；状态 analyzed，无刷新错误 |
| 真实模型调用 | 分类、指南、兴趣评估共 3 次；输入 2,388 tokens，输出 1,143 tokens |
| 分类与指南 | 分类 source=llm；指南 prompt_version=guide-v2、model_version=deepseek-flash，通过结构与引用校验 |
| 启动按钮到可点击体验入口 | 14.650 秒；测试前移除应用镜像，包含校验本地包、导入镜像和浏览器状态轮询 |
| 后端会话到健康检查 ready | 12.361 秒；与浏览器入口计时起止点不同，不能相加 |
| 实际操作 | 粘贴自定义中文 HTML、上传另一份 UTF-8 HTML、下载 Markdown，均与预期内容一致 |
| 移动布局 | 360px 无横向溢出，已查看实际截图 |
| 玩过与停止 | ready 实际持续至少 30 秒后确认成功；停止后虚拟密钥吊销 |
| 资源回收 | 本会话应用/relay 容器、网络、工作区均已移除，回收事件已落库 |
| 自动化回归 | 232 项通过，1 项既有 Starlette 弃用警告；git diff --check 通过 |

浏览器端完整验收 11 项通过。兴趣维度在证据不足时允许 null，保留复核标记，不人为改成高分或置顶。费用账本使用本机配置价格；token 数不等同于实际账单核验。

本机已有 Docker Desktop 和基础 relay 镜像，本地应用包已经准备好。**14.650 秒不包含 Docker 安装或用户首次从网络下载配方包的时间。** 本样例压缩镜像包为 107,784,696 字节（约 107.78 MB），按需保存于数据目录，没有加入 Git 或主程序源码包。

## 本轮修复

- 无发布时间的刚采集条目曾落在刷新截止时刻之后，导致首次刷新跳过；现在仅将该类 first_seen 时间限制到本轮截止时刻，真实未来发布时间仍保留。
- 产品 slug 为 microsoft-markitdown、配方 slug 为 markitdown；现在按规范化仓库身份关联，避免误绑同名项目。
- 缺少本地应用镜像时，核对冻结配方的包 SHA-256，导入后再核对完整镜像 ID；缺失或篡改包明确失败。
- Windows 已安装但停止的 Docker Desktop 可以在读者启动时唤起，引擎不再随测试 Web 进程退出。
- 短 JSON 任务为 DeepSeek Flash 显式关闭默认思考，并加入 JSON 输出指令；截断或空结果记录为失败。仅对官方 DeepSeek Flash 生效。[DeepSeek 思考模式](https://api-docs.deepseek.com/guides/thinking_mode/)、[JSON 输出文档](https://api-docs.deepseek.com/guides/json_mode/)
- 已知管线任务只清除模型多余的 type=json_object 格式标记，其他额外字段仍接受严格校验；明确中文 steps 为对象数组的契约。
- 兴趣评估移到证据富化之后。GitHub 仓库正文仅取 README，防止导航栏的登录/付费信息污染门槛；平台标记按英文单词边界匹配。
- relay 拒绝请求时有界读取请求体，修复 Windows TCP reset 导致 401 响应偶尔丢失的问题。

## 本地入口与复跑

当前验收入口：[http://127.0.0.1:8765/](http://127.0.0.1:8765/)。首页有明确验收标识；可从 MarkItDown 卡片点击“一键启动”。测试会话已停止，重新启动使用新工作区。

```powershell
.venv\Scripts\python.exe tools/run_local_chain.py --fresh
# 在该服务的 /admin/llm 配置真实模型，配置会跨 --fresh 保留
.venv\Scripts\python.exe tools/qa_local_chain.py
```

`--fresh` 仅创建新的隔离验收 SQLite，保留本地模型配置和镜像包。构建样例前需按真实计时报告准备 MarkItDown 基础镜像。完整模式拒绝未配置真实模型或预先存在卡片，不用降级模板冒充推荐成功。脚本只移除指定测试镜像且不用 force；被其他容器使用时应拒绝。

原始证据在被 Git 忽略的本地目录：

- [完整链路记录](../../.local/local-chain/evidence/full-chain.json)
- [生成的新卡片](../../.local/local-chain/evidence/01-new-card.png)
- [启动就绪页面](../../.local/local-chain/evidence/02-ready.png)
- [真实文件转换](../../.local/local-chain/evidence/03-real-conversion.png)
- [360px 页面](../../.local/local-chain/evidence/04-mobile.png)

这是一份第一方 HTML 试玩适配页面，真正调用 MarkItDown 0.1.8；不是其官方 Web 产品，也未测试 PDF、Office、OCR 等可选能力。五个既有候选配方仍为 guided 草稿，#84 的五个精选配方验收和全面红队检查未因此完成。
