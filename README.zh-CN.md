# Inko 手写 Skill

**[English → README.md](README.md)**

**1.5.0（2026-10-06）：skill 不再抄写「模型能写什么」。** 正文里各模型能写哪些不常见的字（希腊字母、拼音声调、上下标、`½`、罗马数字、`⑪`、`「」`、`°` …）
和 Logic 公式能用哪些 LaTeX 命令，改为从服务器实时读取——新命令 `inko.py charset` 和 `inko.py models --symbols`——模型学会新字，skill 不用更新也是对的；
网站上的排版助手从同一处取。`doctor`、`models`、`charset` 发现有更新版 skill 时会提示。同一天模型学会了 33 个希腊字母、拼音声调、上下标、`½`、罗马数字、`⑪`–`⑳` 等。

**1.4.0（2026-10-05）：跟进网站上的排版助手。** 英文手写重新训练过；长公式在等号 / 加减号处折行，不再整条缩小，生成的公式
和文字一样大；Logic 可以单独设常用字迹（`default-style 编号 --model logic-1`）；补上「哪些字写不了、怎么问用户」的说明；
数学题不再为模型 / 字迹是否兼容停下来问；新增 `inko.py delivered`（API 的交付确认）；去掉显式标识只需签署协议。

**1.3.0：只产出标准二维平面图片与 PDF。** 已移除拍照拟真、桌面场景、透视、纸张卷曲、扫描/复印效果和照片贴字功能。

用 [Inko](https://inkotype.com) 将笔记、书信、作文、数学解答写成自然笔迹的平面页面：

- 标准 PNG 图片与 PDF，白纸、米黄纸、方格纸、横线纸、作文纸、田字格
- 自定义排版、标题、文本框与行距
- 修改墨色、笔型、粗细和字间距，保留可编辑笔迹包
- 可以读取题目照片作为输入，但结果只交付独立的平面答题页

> 「把这三道数学题写成横线纸上的手写解答，给我 PNG 和 PDF」
> 「把 notes.md 写成方格纸笔记，用蓝色圆珠笔」
> 「把这封信写在米黄色纸上，输出标准图片」

Logic 仍沿用 1.2.1 的最新符号规则：`\eta` 写成 n，`\wedge` / `\vee` 支持；详见[数学说明](inko-handwriting/references/writing-math.md)。

## 怎么用（给用户）

1. 在 **https://inkotype.com** → 账户 → API key 创建一个 key。建议给 AI 单独建一个，并设置每日花费上限。
2. 把这个仓库链接和 key 发给你的 AI 助手，比如：
   「帮我安装这个手写 skill：https://github.com/pretend1111/inko-skill ，我的 Inko API key 是 ink_live_…」
3. 直接提要求。每次扣费前 AI 都会先告诉你价格（每字 ¥0.002，即 ¥2 / 千字，每次至少按 100 字计；报价和排版预览免费）。
   纯按量计费，从你的 Inko 余额里扣，没有会员、没有月费；用 QQ 邮箱注册送 ¥3 余额，够先试一试。
4. 可选：在网站的字迹库里设一款**常用字迹**、收藏几款喜欢的。之后 AI 默认用你的常用字迹写，除非你另外指定；需要它来挑时，先从你的收藏里挑。跟它说「以后都用这个字迹」，它会帮你把常用字迹改过去。
5. 可选：用**你自己的字**（专属字迹）。在网站上买一个专属字迹席位（¥19.9，一次买断、永久有效，一个席位放一款专属字迹），在网站上录入字样后，AI 就能用它来写（Lyric 1）。

## 给 AI 助手：安装步骤

在用户的项目目录里执行。不要把 API key 回显给用户，也不要把它写进项目文件或提交记录。

1. **下载**（放在临时目录即可）：
   ```bash
   git clone --depth 1 https://github.com/pretend1111/inko-skill.git inko-skill-src
   ```
   没有 git：下载并解压 `https://github.com/pretend1111/inko-skill/archive/refs/heads/main.zip`。
2. **安装**（需要 Python 3.9+；缺 Pillow / numpy 时会自动 pip 安装）：
   ```bash
   python inko-skill-src/install.py                 # 装到当前项目
   python inko-skill-src/install.py --scope user    # 或者：装给用户的所有项目
   ```
   它会识别你是哪种 AI 助手，把 `inko-handwriting/` 复制到 `.claude/skills/`（Claude Code）或 `.agents/skills/`
   （Codex、Cursor、GitHub Copilot、Gemini CLI、OpenCode、goose、Amp）；可用 `--agent claude|agents|both` 指定。
   输出 JSON，含 `installed_to` 和下一步。之后可以删掉 `inko-skill-src`。
3. **保存 key**（从标准输入传入，不出现在命令参数里）：
   ```bash
   printf '%s' 'ink_live_…' | python <installed_to>/scripts/inko.py auth
   ```
   PowerShell：`'ink_live_…' | python <installed_to>\scripts\inko.py auth`。key 会先经 API 验证，再存到用户配置目录
   （`~/.config/inko/key`，Windows 为 `%APPDATA%\inko\key`）；加 `--project` 则存到 `./.inko/key`（已自动 git 忽略）。
   用户也可以自己设置环境变量 `INKO_API_KEY`。
4. **自检**：`python <installed_to>/scripts/inko.py doctor`，看到 `"ok": true`、key 有效、余额。
5. **开始**：现在就读 `<installed_to>/SKILL.md` 并照做——有的助手要重开会话才会列出新 Skill，但你不必等。
   告诉用户已装好、key 存在哪里，并建议在网站上给这个 key 设每日花费上限。

以后更新：重复第 1–2 步即可（覆盖安装的副本，不动 key）。

## 里面有什么

| 文件 | 作用 |
|---|---|
| `SKILL.md`、`references/` | 标准平面输出流程、数学符号、排版、字迹、API 与排错 |
| `scripts/inko.py` | 自检、字迹、报价、排版预览、生成与下载 |
| `scripts/scene.py` | 可编辑笔迹包：移动、间距、笔型、浏览器编辑器、平面导出 |
| `scripts/ink.py` | 修改墨色、粗细、深浅，提取透明手写层 |
| `scripts/paper.py make` | 绘制标准二维纸张背景，不识别或处理照片 |
| `scripts/compose.py drift` | 可选的二维行位置微调，不改变纸张几何 |
| `scripts/pdf.py` | 平面图片合成 PDF |

图像脚本使用 Pillow 和 numpy，本地处理，不上传图片。更新安装会替换旧 Skill 目录，移除旧版脚本，不动 API key。

## 费用、key 与标识

- **不确认不扣费**：`inko.py generate` 不加 `--yes` 只报价；Skill 要求 AI 先征得你的同意（或在你给的预算内）。
  失败 / 取消的任务自动退款；每单可免费重写一次。
- **key** 只存在你的电脑上（用户配置目录或 `./.inko/key`），随时可以在网站上吊销。
- **AI 标识**：按照 GB 45438-2025，Inko 每页都带显式标识「AI生成 · Inko」和隐式元数据。这些脚本在所有衍生图片和
  PDF 上都保留隐式标识，并按规定大小重新加上显式标识；请不要去除。不带显式标识的页面只对在网站上
  签了《AI 生成内容标识协议》的账户开放（隐式标识仍然保留）。
- 借条、收据、合同、证明、请假条、签名等文书会被拒绝（不扣费）。

## 开发

```bash
python tests/run_tests.py          # 离线：平面页面、墨色、排版、PDF、标识与功能边界
python tests/test_logic_symbols.py # Logic 符号契约
```

`evals/` 包含数学题输入与笔记转标准平面图片/PDF 的任务和评分脚本。

MIT 许可。Inko API 需要账户并遵守服务条款。
