# Inko 手写 Skill

**[English → README.md](README.md)**

给你的 AI 助手一支笔。装上这个 Skill，Claude Code、Codex、Cursor、GitHub Copilot、Gemini CLI、OpenCode 等支持
`SKILL.md` 的 AI 助手，就能用 [Inko](https://inkotype.com) 把笔记、书信、作文、整道数学题的解答写成**真人手写**，
再处理成你真正要的样子：

- **正好写在你自己本子的横线上**（发一张照片就行，倾斜、纸面弯曲、透视都能跟上）
- 像**手机拍的照片**、像**扫描件**，或者直接出 **PDF**
- 把答案**填进练习卷的空白处**
- 换**墨水颜色、笔的类型、笔画粗细**，不用重新付费

AI 会像学生一样写解题步骤，只问你真正要紧的几件事（写多详细、哪种字迹、什么纸、什么笔、多少钱），确认后才开始写。

| 做完的数学作业 | 练习卷照片上填答案 | 方格纸笔记（手机拍照效果） |
|---|---|---|
| ![横线作业本上像学生写的解题步骤](docs/images/math-homework.jpg) | ![答案写在练习卷每题下面的空白处](docs/images/worksheet.jpg) | ![5 mm 方格纸上的物理笔记，放在书桌上拍](docs/images/notes-photo.jpg) |

| 写在你自己的本子上 | 像手机拍的照片 | 换笔、换颜色 |
|---|---|---|
| ![字贴在作业本照片的横线上](docs/images/on-your-paper.jpg) | ![生成的页面做成书桌上拍的照片](docs/images/desk-photo.jpg) | ![同一段字：原样、蓝色圆珠笔、铅笔、红色中性笔](docs/images/restyle.jpg) |

以上都是 AI 助手在测试中按这个 skill 做出来的成品，每张都保留了 Inko 的 AI 生成标识。

> 「帮我把这 5 道题做完，写在我的作业本上」+ 题目照片 + 作业本照片
> 「把 notes/第三章.md 写成手写 PDF，方格纸，蓝色圆珠笔」
> 「这封信写在米黄色信纸上，做成放在书桌上拍的照片」

## 怎么用（给用户）

1. 在 **https://inkotype.com** → 账户 → API key 创建一个 key。建议给 AI 单独建一个，并设置每日花费上限。
2. 把这个仓库链接和 key 发给你的 AI 助手，比如：
   「帮我安装这个手写 skill：https://github.com/pretend1111/inko-skill ，我的 Inko API key 是 ink_live_…」
3. 直接提要求。每次扣费前 AI 都会先告诉你价格（每字 ¥0.002，即 ¥2 / 千字，每次至少按 100 字计；报价和排版预览免费）。
   纯按量计费，从你的 Inko 余额里扣，没有会员、没有月费；新注册送 ¥3 余额，够先试一试。
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
| `SKILL.md` | 工作流程、规矩、什么时候问用户、哪个需求用哪个工具 |
| `references/` | 各场景做法、像学生一样写数学、符号表与替换、排版、选字迹、贴合自己的纸、后处理、API、排错 |
| `assets/layouts/` | 书信、横线作业、笔记、作文纸、填空答案的排版模板 |
| `scripts/inko.py` | API 客户端：自检、保存 key、查字迹（含收藏）、常用字迹、样张、报价、排版预览、生成、等待、免费重写…… |
| `scripts/ink.py` | 改墨水颜色 / 粗细 / 深浅 / 笔的质感，提取透明底手写层 |
| `scripts/paper.py` | 识别横线纸照片（行距、倾斜、已写的行）、找练习卷空白处、拉正照片、画各种纸 |
| `scripts/compose.py` | 把字贴到用户自己的纸上（逐行对齐横线）、练习卷上、任意图片的指定位置 |
| `scripts/photo.py` | 手机拍照 / 扫描件 / 复印件效果 |
| `scripts/pdf.py` | 图片合成按真实纸张尺寸的 PDF |

`inko.py` 只用 Python 标准库；图像脚本需要 Pillow 和 numpy（OpenCV 可选）。除 `inko.py` 调 API 外，全部在本地运行，
不会上传你的图片。

## 费用、key 与标识

- **不确认不扣费**：`inko.py generate` 不加 `--yes` 只报价；Skill 要求 AI 先征得你的同意（或在你给的预算内）。
  失败 / 取消的任务自动退款；每单可免费重写一次。
- **key** 只存在你的电脑上（用户配置目录或 `./.inko/key`），随时可以在网站上吊销。
- **AI 标识**：按照 GB 45438-2025，Inko 每页都带显式标识「AI生成 · Inko」和隐式元数据。这些脚本在所有衍生图片和
  PDF 上都保留隐式标识，并按规定大小重新加上显式标识；请不要去除。不带显式标识的页面只对买过专属字迹席位、并在网站上
  签了《AI 生成内容标识协议》的账户开放（隐式标识仍然保留）。
- 借条、收据、合同、证明、请假条、签名等文书会被拒绝（不扣费）。

## 开发

```bash
python tests/run_tests.py          # 离线：合成的本子照片、横线识别、贴字、拍照效果、PDF、标识检查
```

`evals/` 里是给 AI 助手的真实任务（数学作业照片、写到自己的本子上、笔记转手写 PDF + 照片、练习卷填答案），
带输入文件和按结果打分的脚本 `evals/grade.py`。

MIT 许可。Inko 及其 API 由 inkotype.com 提供，使用 API 需要账户并遵守其服务条款。
