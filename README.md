# Background Vocal Generator

本项目是一个用于生成背景和声（Background Vocals，简称 BV）初稿的制作工具。

项目以包含主旋律与和弦的 MIDI 轨道为输入，先通过脚本完成 MIDI 解析、格式校验、和弦谱转换、结果检查和 MIDI 导出，再由 Codex 根据主旋律、和弦谱与项目编曲规则完成和声设计。主旋律中的歌词事件可以随旋律进入中间文件，并在和声轨与最终 MIDI 中保留。

最终可以生成以下 11 条背景人声轨道。项目提供多种职责明确的声部，便于作者在后续制作中选择、删减、重新配唱和混音，而不是要求所有轨道始终同时使用：

- `LeadDouble`：主旋律叠唱轨，用于加强主唱的厚度、稳定度和中心感，也可作为齐唱或重点句叠唱素材。
- `HHA`、`HHB`：具有旋律轮廓的高声部和声。跟随主旋律运动，负责增加明亮度、张力和展开感。两条声部会尽量保持独立，避免长期重复。
- `HHF`：相对平稳的高声部和声。更多保留共同音和重复音，为变化较多的高声部提供稳定性，用于较克制的段落。
- `LHA`、`LHB`：具有旋律轮廓的低声部和声。负责补充厚度、温暖感、重量感。
- `LHF`：相对平稳的低声部和声。承担较稳定的下方支撑，连接主旋律型和声与 Pad 声部。
- `VocalPad1` 至 `VocalPad4`：四部和声风格的 Pad / Choir 支撑声部。主要用于建立持续的和声背景、连接段落、扩大空间感，通过平稳声部进行避免机械式平行移动。

生成结果为需要继续编辑的和声草稿，而不是无需修改的最终成品。作者仍需结合歌词、演唱方式、音色、情绪与实际编曲进行细致的删减和调整。

## 制作原因

写和声是一项费时费力、重复性很高的工作。它不是纯粹的机械劳动，因此很难用传统 workflow 完整处理，但其中大约 90% 的工作又具有明显的机械性，需要反复核对旋律、和弦、音程、音域和时值。

在这一点上来说，和整理歌词词格有相似之处：并不困难，但非常消耗注意力，完成之后，往往已经没有太多精力继续处理作品中更重要的创作部分。

因此制作了这个工具，用于完成初步的和声设计与基础检查。虽然无法代替作者对作品的判断，但可以显著减少重复劳动，让作者把珍贵的时间和精力留给真正需要审美决策的部分。

## 工作方式

项目将整个过程分为两类：

- **确定性流程**：MIDI 解析、轨道与量化检查、JSON 输出、和弦谱转换、BV 校验、MIDI 导出，由脚本完成。
- **非确定性流程**：和弦标记复核、基础 BV 编写，由 Agent 根据技能规则完成。

完整数据流如下：

```text
包含 melody / chord / 可选 control 轨道的 MIDI
  ↓ script/midi_to_json.py
canonical.json + melody.json + chord.json
  ↓ =hx 和弦整理模式
note.txt
  ↓ =hs 和声整理模式
bv_raw.json
  ↓ script/validate_bv_music.py
校验与音乐性评分报告
  ↓ script/bv_raw_to_midi.py
bv.mid
```

## 环境要求

- Python 3。
- 支持读取本项目 `AGENTS.md` 与项目技能文件的 Codex 环境。
- Python 脚本仅使用标准库，无需安装第三方依赖。

请在项目根目录执行本文中的命令。

> 你可以选用 Codex 之外的其他 Agent，但经过一系列基本测试，Codex 的表现最为稳定出色。如果使用其他工具或模型，请放弃最终审核分数低于 80 的结果。

## 输入 MIDI 要求

输入 MIDI 必须包含以下非空音乐轨道，轨道名大小写不敏感：

- `melody`：主旋律，只允许单音旋律，不允许音符重叠；可包含与音符起点对齐的 lyric meta event。
- `chord`：和弦音；同一个和弦内的音符必须同时开始、同时结束。
- `control`：可选，用于标记段落和禁止生成 BV 的区间。

其他非空轨道会被忽略。

主要限制：

- 每个 MIDI 文件不超过 100 KB。
- 作品不超过 250 小节，按速度表计算不超过 10 分钟。
- 音符需对齐当前拍号下的 `1/32 小节` 网格或三连音网格。
- 输入音符保留范围为 `C0` 至 `C6`。
- 必须至少存在一个有效 melody 音符和一个有效和弦。
- 至少一个有效和弦必须与 melody 音符发生时间重叠。
- 支持速度变化、拍号变化和 Standard MIDI format 0 / 1。
- 不保留 velocity、CC、pitch bend 和歌声参数；`melody` 轨道的 lyric meta event 会绑定到对应音符。
- lyric meta event 默认必须是 UTF-8，因此支持汉字；旧 MIDI 可通过 `--lyric-encoding gb18030|big5|latin-1` 显式指定编码。
- 每条 lyric meta event 必须与一个 melody 音符的起点完全一致；同一起点的多条 lyric event 会按顺序拼接。
- 使用角色参数时，各角色文件可以使用不同 ticks-per-quarter；项目 tempo 与拍号只取自 `--melody` 文件。

### 可选 control 轨道

`control` 轨道使用低音区音符标记段落。可使用 MIDI `0-4` 或 `12-16`：

| 音符 | 段落 | 用途 |
| --- | --- | --- |
| C | `verse` | 主歌，和声较克制、亲密 |
| C# | `pre_chorus` | 预副歌，增加推进感 |
| D | `chorus` | 副歌，和声更开阔、完整 |
| D# | `bridge` | 桥段，允许更明显的对比 |
| E | `no_bv` | 不生成和声与 Pad；`LeadDouble` 仍保留 |

控制音的起点表示段落开始，下一个控制音的起点表示当前段落结束。任意两个控制音自身的覆盖区间不能重叠。

## 快速开始

以下示例假设输入文件为 `krz-demo.mid`，作品名将自动使用文件名 `krz-demo`。

### 1. 将 MIDI 解析为 JSON

```bash
python3 script/midi_to_json.py krz-demo.mid
```

如果 melody、chord、control 位于不同文件中，可显式指定角色来源：

```bash
python3 script/midi_to_json.py \
  --melody krz-demo-melody.mid \
  --chord krz-demo-chord.mid \
  --control krz-demo-chord.mid \
  --work-name krz-demo
```

角色参数模式必须同时提供 `--melody` 和 `--chord`，`--control` 可选，并且不能再提供位置参数。每个角色文件先忽略所有空轨道；过滤后只有一个轨道时直接使用该轨，有多个轨道时必须存在唯一的同名角色轨道。

输出：

```text
output/krz-demo/
  canonical.json
  melody.json
  chord.json
```

- `canonical.json`：完整解析结果。
- `melody.json`：供和声整理使用的主旋律、header 与可选 control 数据。
- `chord.json`：供和弦谱整理使用的和弦数据。

脚本首先过滤 `C0..C6` 之外的输入音符；过滤完成后，再在必要时对剩余的整条 melody 使用统一的整数八度移调，使其进入 `G0..F5` 工作音域，为上下和声预留空间。移调只作用于输出 JSON 中的 melody，不会修改输入 MIDI 或 chord 音高。带有歌词的 melody 音符如果位于保留音域之外，脚本会报错而不是静默过滤，以免歌词与音符错位。

### 2. 生成并复核和弦谱

运行转换脚本：

```bash
python3 script/chord_json_to_note.py output/krz-demo
```

在 Codex 中引入本项目目录，开启新对话中执行：

```text
=hx process output/krz-demo/chord.json
```

Codex 会按 `skill/chord-chart/SKILL.md` 的规则调用确定性转换脚本、复核和弦命名歧义，并生成：

```text
output/krz-demo/note.txt
```

> 如果 Agent 没有自动检测到规则，请检查配置，或在对话中给出明确提示，如：
> Read AGENTS.md and follow the project mode rules.
> =hx process output/krz-demo/chord.json

也可以只运行转换脚本，但脚本无法代替对特殊和弦、命名歧义和上下文的复核。

> 脚本生成的中间文件 note.txt 是可读的。如果不信任 Agent，你可以人工复核、增删，甚至按照文件格式手工完成和弦标记定义的工作。

### 3. 生成背景和声初稿

确认 `melody.json` 和 `note.txt` 已存在后，在 Codex 对话中执行：

```text
=hs process output/krz-demo
```

Codex 会按 `skill/background-vocals/SKILL.md` 的规则直接读取主旋律和和弦谱，编写 11 条 BV 轨道，并输出：

```text
output/krz-demo/bv_raw.json
```

其中六条高低和声轨会在 `no_bv` 以外逐个对应主旋律音符；`LeadDouble` 与六条和声轨会继承对应主旋律歌词，Vocal Pad 不携带歌词。

### 4. 校验和评分

```bash
python3 script/validate_bv_music.py output/krz-demo
```

脚本输出 JSON 报告，包括：

- `passed`：是否通过。
- `score`：音乐性软评分。
- `minScore`：最低通过分。
- `hardErrors`：必须修复的结构或规则错误。
- `warnings`：需要复核的音乐风险。
- `suggestions`：修改建议。

`=hs` 模式会在生成后自动运行该检查，并根据报告进行最多三轮修订。脚本只负责检查与评分，不会生成或改写和声。

### 5. 导出 MIDI

```bash
python3 script/bv_raw_to_midi.py output/krz-demo/bv_raw.json
```

默认输出：

```text
output/krz-demo/bv.mid
```

导出的 Standard MIDI format 1 文件包含 conductor 轨和全部 11 条 BV 轨，可导入 DAW 继续调整。`LeadDouble` 与六条逐主旋律对齐的和声轨会写入对应歌词事件，Vocal Pad 不写入歌词。

## 对话模式

项目通过对话模式区分普通开发任务与音乐处理任务：

| 模式 | 用途 |
| --- | --- |
| `=pt` | 默认普通模式，用于产品、开发、文档、脚本和调试工作 |
| `=hx` | 根据 `chord.json` 生成并复核 `note.txt` |
| `=hs` | 根据 `melody.json` 与 `note.txt` 生成并校验 `bv_raw.json` |

使用 `=hx` 或 `=hs` 时，应同时给出具体作品名、文件或目录，例如：

```text
=hx generate a chord chart for krz-demo
=hs generate background vocals for krz-demo
```

## 脚本参数

所有用户入口脚本均支持 `-h` / `--help`，用于显示命令帮助。

### `script/midi_to_json.py`

```bash
python3 script/midi_to_json.py [INPUT_MIDI] [--melody MIDI --chord MIDI [--control MIDI]] [--work-name NAME] [--lyric-encoding ENCODING] [--output-root DIR] [--tolerance BEATS]
```

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `INPUT_MIDI` | 与角色参数二选一 | 单个 MIDI；必须包含唯一非空 `melody`、`chord` 与最多一个 `control` |
| `--melody MIDI` | 角色模式必填 | melody 来源；忽略空轨后单轨直接使用，多轨选择唯一 `melody` 轨 |
| `--chord MIDI` | 角色模式必填 | chord 来源；忽略空轨后单轨直接使用，多轨选择唯一 `chord` 轨 |
| `--control MIDI` | 可选 | control 来源；忽略空轨后单轨直接使用，多轨选择唯一 `control` 轨 |
| `--work-name NAME` | 输入或 melody 文件名 | 安全的单目录作品名 |
| `--lyric-encoding ENCODING` | `utf-8` | MIDI lyric payload 编码；可选 `utf-8`、`gb18030`、`big5`、`latin-1` |
| `--output-root DIR` | `output` | 输出根目录；实际输出目录为 `DIR/<作品名>/` |
| `--tolerance BEATS` | `0.01` | 量化、重叠与和弦齐尾检查的误差宽容度，单位为 beats；必须在 `0..0.05` 之间 |

示例：

```bash
python3 script/midi_to_json.py krz-demo.mid --output-root output --tolerance 0.005
python3 script/midi_to_json.py --melody krz-demo-melody.mid --chord krz-demo-chord.mid --work-name krz-demo
python3 script/midi_to_json.py krz-demo.mid --lyric-encoding gb18030
```

如果目标 JSON 已存在，脚本会询问是否覆盖，只有输入 `y` 才会继续。

### `script/chord_json_to_note.py`

```bash
python3 script/chord_json_to_note.py CHORD_JSON_OR_DIR [-o PATH] [--force]
```

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `CHORD_JSON_OR_DIR` | 必填 | `chord.json` 文件，或包含该文件的作品目录 |
| `-o PATH` / `--output PATH` | 同目录 `note.txt` | 指定输出文本路径 |
| `--force` | 关闭 | 不询问，直接覆盖已有 `note.txt` |

示例：

```bash
python3 script/chord_json_to_note.py output/krz-demo --output output/krz-demo/chords.txt
python3 script/chord_json_to_note.py output/krz-demo --force
```

脚本会补齐所有小节和 `N.C.`，并标记 BPM 与拍号变化。无法可靠表示的小节中途 BPM 变化会被拒绝。

> 本项目未经过极复杂的频繁变拍、变速测试，不建议导入这类 MIDI 文件。如果一定要处理类似情况，建议在导出 MIDI 时抹除这些内容，在本项目流程结束导入时重新读取工程的自动化信息。

### `script/validate_bv_music.py`

```bash
python3 script/validate_bv_music.py WORK_DIR_OR_BV_RAW_JSON [--min-score SCORE]
```

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `WORK_DIR_OR_BV_RAW_JSON` | 必填 | 作品目录，或具体的 `bv_raw.json` 路径 |
| `--min-score SCORE` | `80` | 最低通过分，使用整数；低于该分数时返回失败状态 |

示例：

```bash
python3 script/validate_bv_music.py output/krz-demo --min-score 85
```

硬性校验会检查 JSON 结构、轨道、字段、音域、量化、`LeadDouble`、六条和声轨的逐音符对齐和上下方向。软评分会提示轮廓塌缩、非乐句边界大跳、上下表达型和声线长期重复、表达型和声偏离优先间距、间距过宽、明显非和弦音，以及 Pad 的声部完整度、交叉、相邻声部间距、长期重复、非乐句边界大跳、僵硬平行运动、音域、密度和整体编曲平衡等问题。

### `script/bv_raw_to_midi.py`

```bash
python3 script/bv_raw_to_midi.py BV_RAW_JSON [-o PATH] \
  [--ticks-per-beat NUMBER] [--velocity NUMBER] [--tolerance BEATS] [--allow-standalone]
```

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `BV_RAW_JSON` | 必填 | 待导出的 `bv_raw.json` |
| `-o PATH` / `--output PATH` | 同目录 `bv.mid` | 指定输出 MIDI 路径 |
| `--ticks-per-beat NUMBER` | `480` | 导出 MIDI 的时间分辨率，即每个四分音符包含的 tick 数；必须为 `1..32767` 的整数 |
| `--velocity NUMBER` | `80` | 写入所有 MIDI note 的 velocity，必须为 `1..127` 的整数 |
| `--tolerance BEATS` | `0.01` | 导出前量化与旋律对齐检查的误差宽容度，单位为 beats；必须在 `0..0.05` 之间 |
| `--allow-standalone` | 关闭 | 仅用于无 source melody、无歌词的旧项目；跳过 melody 对齐与歌词继承 |

示例：

```bash
python3 script/bv_raw_to_midi.py output/krz-demo/bv_raw.json \
  --output output/krz-demo/bv_960.mid \
  --ticks-per-beat 960 \
  --velocity 72 \
  --tolerance 0.005
```

如果输出 MIDI 已存在，脚本会询问是否覆盖，只有输入 `y` 才会继续。默认导出必须在同目录找到包含 melody 的 `melody.json` 或 `canonical.json`，以确定性校验并重建歌词。只有完全无歌词的旧项目可以显式使用 `--allow-standalone`。

`--ticks-per-beat` 不会改变速度、音符时值或演奏结果，只决定 JSON 中的 beat 时间在 MIDI 文件内用多细的整数刻度表示。默认值 `480` 可以准确表示本项目常用的 `1/32_bar` 与三连音网格，通常不需要修改。以下情况可以手动设置：

- 目标 DAW、硬件或后续处理工具要求特定的 PPQ / ticks-per-beat。
- 需要与已有 MIDI 工程使用相同的时间分辨率。
- 后续还要在 DAW 中进行更细的人工 timing 编辑，可使用 `960` 等更高值。
- 为兼容只接受较低 PPQ 的旧设备或软件，需要降低该值。

### `script/project_common.py`

该文件保存多个脚本共用的项目限制与时间线计算逻辑，不是命令行入口，没有可选参数，不需要单独执行。

## 输出目录

标准作品目录结构如下：

```text
output/<作品名>/
  canonical.json
  melody.json
  chord.json
  note.txt
  bv_raw.json
  bv.mid
```

| 文件 | 作用 |
| --- | --- |
| `canonical.json` | 完整 MIDI 解析结果 |
| `melody.json` | 主旋律、header 与可选 control 数据 |
| `chord.json` | 和弦音数据 |
| `note.txt` | 按小节整理的文本和弦谱 |
| `bv_raw.json` | 11 条背景人声轨道的结构化数据 |
| `bv.mid` | 可导入 DAW 的最终 MIDI |

## 边界

- 本项目生成的是和声设计初稿，**不能替代作者的音乐判断与人工审核**。
- 和弦谱脚本只能确定性识别其支持的 pitch-class 集合，不支持过度复杂的高叠和弦、复合和弦，请注意简化输入 MIDI 的和弦表现。特殊和弦与命名歧义仍需人工复核。
- 和声编写属于创作性步骤，不使用固定的和弦音映射脚本自动生成。
- Python 校验器可以发现结构错误和常见音乐风险，但不能完整判断歌词语义、音色适配、演唱难度和作品情绪。
- 输入与中间数据统一以 beats 为时间单位，网格标记固定为 `1/32_bar`。

## 测试

运行全部测试：

```bash
python3 -m unittest discover -s test
```

## 项目规则文件

- 对话模式规则：`rule/modes.md`
- 和弦谱整理规则：`skill/chord-chart/SKILL.md`
- 背景和声整理规则：`skill/background-vocals/SKILL.md`


## 数据与权利说明

本项目的本地 Python 脚本**不会**主动收集、上传或传输输入 MIDI、歌词、中间文件或生成结果。

和弦复核与背景和声编写需要用户手动使用 Agent。使用云端 Agent 时，作品内容是否被传输、保存或用于其他处理，取决于所选服务、账户设置和部署方式，不属于本项目能够控制的范围。对未公开作品、商业项目或其他敏感内容有保密要求的用户，应谨慎使用 Agent 功能，优先选择符合自身要求的本地部署或隐私配置；必要时可在提交前自行进行匿名化或适当的加噪处理，并自行评估其对分析结果的影响。

使用 Agent 功能前，用户必须确认自己对所提交的作曲、旋律、和弦、歌词及相关素材拥有必要权利或已取得合法授权。用户应自行承担输入内容、生成结果及后续使用所涉及的版权和授权责任。

## 许可证

本项目使用 GNU General Public License v3.0，详见 `LICENSE`。
