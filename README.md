# AI Reception（桌面安装版：模仿微信桌面三栏 + 任务右侧栏 + 员工互动 + SMS模拟，本地知识库）

你要的不是 bat，而是 **像微信桌面版那样：下载一个安装包 .exe → 安装 → 双击运行**。

我这仓库已经内置 **GitHub Actions 自动打包 Windows 安装程序**：
- 你把代码上传到 GitHub（main 分支）
- GitHub 会自动编译出：`AIReception_Setup.exe`
- 你直接下载这个安装包安装运行（不需要 bat / 不需要本地装 Python）

---

## ✅核心特性
- 微信桌面风格：左竖栏图标 + 中间三栏（会话列表/聊天/右侧任务栏）
- “短信=聊天”：不分收件箱/发件箱
- 全本地：数据库/知识库都保存在本机（默认：`%LOCALAPPDATA%\AIReception\user_data`）
- 识别员工手机号：员工短信自动归类为员工会话；“请假”短信会生成请假请求
- SMS only（测试期用模拟）：聊天页点 **“模拟收到短信”** 注入测试短信
- 排班派单：右侧任务栏一键 HOLD / CONFIRM / DONE（含冲突保护）

---

## 你要的“微信式安装运行”（最简单）
### A) 直接得到安装包（推荐）
1. 把这个项目上传到 GitHub（main 分支）
2. 打开 GitHub 项目的 **Actions**
3. 看到 **Build Windows Installer** 成功后，下载 Artifact：`AIReception_Windows`
4. 解压 Artifact，双击 `AIReception_Setup.exe` 安装
5. 安装后从桌面图标打开（像微信一样）

> 卸载：控制面板卸载即可  
> 数据/知识库：默认保存在 `%LOCALAPPDATA%\AIReception\user_data`（卸载不会删）

---

## 开发者本地运行（可选）
如果你就是要在源码里直接跑：
- 需要安装 Python 3.10+
- 运行 `run_debug.bat` 看日志

（但你说你不要 bat，所以你正常只走 A 方案）

