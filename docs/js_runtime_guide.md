# JS Runtime 使用说明

## 为什么会用到 JS runtime

在解析部分 YouTube 视频时，`yt-dlp` 可能需要借助 JavaScript runtime 来执行播放器脚本、处理签名或 `nsig` 相关逻辑。  
如果系统里完全没有可用的运行时，某些视频可能出现解析失败、无可播放格式或下载失败。

## 程序支持哪些运行时

程序会自动检测以下运行时：

- `node`
- `deno`
- `bun`
- `quickjs`
- `qjs`

默认设置为：

```text
JS runtime = auto
```

也就是说，只要系统里安装了上述任意一种，程序就会优先自动使用。

## 用户没有安装 Node.js 时怎么办

最推荐的方案是安装 Node.js LTS。

### 方案一：安装 Node.js LTS

1. 打开官网：<https://nodejs.org/>
2. 下载并安装 `LTS` 版本
3. 安装完成后重新启动应用
4. 保持设置中的 `JS runtime` 为 `auto`

如果一切正常，程序会自动检测到 `node`，无需额外配置。

### 方案二：手动指定 Node.js

如果已经安装了 Node.js，但程序没有自动识别：

1. 打开系统终端执行：

```powershell
node -v
```

2. 如果命令可以返回版本号，说明 Node.js 已安装
3. 在应用设置中将 `JS runtime` 改为手动路径或可执行名称

常见路径示例：

```text
node:C:\Program Files\nodejs\node.exe
```

## 不安装 Node.js 的替代方案

如果不想安装 Node.js，也可以安装以下任意一种：

- Deno：<https://deno.com/>
- Bun：<https://bun.sh/>
- QuickJS：需自行安装并加入系统 PATH

安装后重启应用，并保持 `JS runtime = auto`，程序会自动尝试检测。

## 如何判断是不是 JS runtime 问题

如果出现以下现象，通常值得优先检查 JS runtime：

- 某些 YouTube 视频始终解析失败
- 同一视频在浏览器能看，但程序里报格式不可用或无法提取
- 日志中出现与 `nsig`、signature、player script、JavaScript runtime 相关的提示

这时建议按顺序排查：

1. 更新 `yt-dlp.exe`
2. 安装 Node.js LTS
3. 重新启动应用
4. 检查设置中的 `JS runtime`
5. 查看 `logs/yt-dlp.log`

## 推荐建议

对普通用户，最省心的做法就是：

- 安装 Node.js LTS
- 保持应用中的 `JS runtime = auto`
- 不要手动改动，除非日志明确提示未检测到可用运行时
