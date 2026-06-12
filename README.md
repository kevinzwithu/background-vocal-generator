# Background Vocal Generator

本项目是一个用于生成背景和声（Background Vocals，简称 BV）初稿的制作工具。

项目以包含主旋律与和弦的 MIDI 轨道为输入，先通过脚本完成 MIDI 解析、格式校验、和弦谱转换、结果检查和 MIDI 导出，再由 Codex 根据主旋律、和弦谱与项目编曲规则完成和声设计。主旋律中的歌词事件可以随旋律进入中间文件，并在和声轨与最终 MIDI 中保留。

最终可以生成以下 11 条背景人声轨道。项目提供多种职责明确的声部，便于作者在后续制作中选择、删减、重新配唱和混音：

- `LeadDouble`：主旋律叠唱轨，用于加强主唱的厚度、稳定度和中心感，也可作为齐唱或重点句叠唱素材。
- `HHA`、`HHB`：具有旋律轮廓的高声部和声。跟随主旋律运动，负责增加明亮度、张力和展开感。两条声部会尽量保持独立，避免长期重复。
- `HHF`：相对平稳的高声部和声。更多保留共同音和重复音，为变化较多的高声部提供稳定性，用于较克制的段落。
- `LHA`、`LHB`：具有旋律轮廓的低声部和声。负责补充厚度、温暖感、重量感。
- `LHF`：相对平稳的低声部和声。承担较稳定的下方支撑，连接主旋律型和声与 Pad 声部。
- `VocalPad1` 至 `VocalPad4`：四部和声风格的 Pad / Choir 支撑声部。主要用于建立持续的和声背景、连接段落、扩大空间感，通过平稳声部进行避免机械式平行移动。

生成结果为和声草稿，作者可结合歌词、演唱方式、音色、情绪与实际编曲继续删减和调整。

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
bv_plan.json
  ↓ script/bv_raw_to_midi.py --render-plan
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

- `melody`：单音主旋律，音符互不重叠，可包含与音符起点对齐的 lyric meta event。
- `chord`：和弦音，同一个和弦内的音符必须同时开始、同时结束。
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
- 输出保留音高、起止时间和 melody 歌词，省略 velocity、CC、pitch bend 和歌声参数。`melody` 轨道的 lyric meta event 会绑定到对应音符。
- lyric meta event 默认使用 UTF-8，支持汉字。旧 MIDI 可通过 `--lyric-encoding gb18030|big5|latin-1` 显式指定编码。
- 每条 lyric meta event 必须与一个 melody 音符的起点完全一致。同一起点的多条 lyric event 会按顺序拼接。
- 使用角色参数时，各角色文件可以使用不同 ticks-per-quarter。项目 tempo 与拍号取自 `--melody` 文件。

### 可选 control 轨道

`control` 轨道使用低音区音符标记段落。可使用 MIDI `0-4` 或 `12-16`：

| 音符 | 段落 | 用途 |
| --- | --- | --- |
| C | `verse` | 主歌，和声较克制、亲密 |
| C# | `pre_chorus` | 预副歌，增加推进感 |
| D | `chorus` | 副歌，和声更开阔、完整 |
| D# | `bridge` | 桥段，允许更明显的对比 |
| E | `no_bv` | 和声与 Pad 静音，保留 `LeadDouble` |

控制音的起点表示段落开始，下一个控制音的起点表示当前段落结束。各控制音自身的覆盖区间应互不重叠。

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

角色参数模式必须同时提供 `--melody` 和 `--chord`，`--control` 可选，与位置参数互斥。每个角色文件先忽略所有空轨道。过滤后只有一个轨道时直接使用该轨，有多个轨道时选择唯一的同名角色轨道。

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

脚本先过滤 `C0..C6` 之外的输入音符。需要调整音域时，再对剩余的整条 melody 使用统一的整数八度移调，使其进入 `G0..F5` 工作音域，为上下和声预留空间。移调结果写入输出 JSON 中的 melody，输入 MIDI 和 chord 音高保持原样。带有歌词的越界 melody 音符会触发错误，便于核对歌词与旋律的对应关系。

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

也可以直接运行转换脚本，再由 Agent 或作者结合上下文复核特殊和弦与命名歧义。

> `note.txt` 是可读的文本和弦谱。你可以人工复核、增删，也可以按文件格式手工编写和弦标记。

### 3. 生成背景和声初稿

确认 `melody.json` 和 `note.txt` 已存在后，在 Codex 对话中执行：

```text
=hs process output/krz-demo
```

如需使用允许声部按乐句休止的 v2，在对话中执行：

```text
=hs process output/krz-demo --evaluation-version v2
```

Codex 会按 `skill/background-vocals/SKILL.md` 的规则读取主旋律和和弦谱，先编写和声计划，再展开为 BV 数据：

```text
output/krz-demo/bv_plan.json
output/krz-demo/bv_raw.json
```

默认 v1 中，六条高低和声轨会在 `no_bv` 以外逐个对应主旋律音符。`LeadDouble` 与六条和声轨会继承对应主旋律歌词，Vocal Pad 不携带歌词。

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

`=hs` 模式默认使用 v1 评分，按报告修订 `bv_plan.json`，再展开和校验。选择 v2 时，校验命令为：

```bash
python3 script/validate_bv_music.py output/krz-demo --evaluation-version v2
```

v2 报告提供技术校验与音乐诊断，`score`、`minScore` 为 `null`。`=hs` 流程随后请求独立音乐评审。

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
| `=hs` | 根据 `melody.json` 与 `note.txt` 编写和声计划，展开并校验 `bv_raw.json` |

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
| `INPUT_MIDI` | 与角色参数二选一 | 单个 MIDI，必须包含唯一非空 `melody`、`chord` 与最多一个 `control` |
| `--melody MIDI` | 角色模式必填 | melody 来源，忽略空轨后单轨直接使用，多轨选择唯一 `melody` 轨 |
| `--chord MIDI` | 角色模式必填 | chord 来源，忽略空轨后单轨直接使用，多轨选择唯一 `chord` 轨 |
| `--control MIDI` | 可选 | control 来源，忽略空轨后单轨直接使用，多轨选择唯一 `control` 轨 |
| `--work-name NAME` | 输入或 melody 文件名 | 安全的单目录作品名 |
| `--lyric-encoding ENCODING` | `utf-8` | MIDI lyric payload 编码，可选 `utf-8`、`gb18030`、`big5`、`latin-1` |
| `--output-root DIR` | `output` | 输出根目录，实际输出目录为 `DIR/<作品名>/` |
| `--tolerance BEATS` | `0.01` | 量化、重叠与和弦齐尾检查的误差宽容度，单位为 beats，范围为 `0..0.05` |

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

脚本会补齐所有小节和 `N.C.`，并标记 BPM 与拍号变化。BPM 变化须位于小节起点，小节中途变化会触发错误。

> 频繁变拍、变速的工程建议先导出固定拍号与速度的 MIDI，完成流程后再从原 DAW 工程恢复拍号和速度自动化。

### `script/validate_bv_music.py`

```bash
python3 script/validate_bv_music.py WORK_DIR_OR_BV_RAW_JSON [--min-score SCORE] [--evaluation-version v1|v2]
```

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `WORK_DIR_OR_BV_RAW_JSON` | 必填 | 作品目录，或具体的 `bv_raw.json` 路径 |
| `--min-score SCORE` | `80` | v1 评分的最低通过分 |
| `--evaluation-version VERSION` | `v1` | v1 使用数值评分，v2 使用技术校验与音乐诊断 |

示例：

```bash
python3 script/validate_bv_music.py output/krz-demo
```

校验包括 JSON 结构、音域、量化、旋律对齐、声部方向与静音区间。同目录有 plan 时，还会核对来源与展开结果。默认使用 v1 评分，选择 v2 时输出音乐诊断。

### `script/bv_raw_to_midi.py`

展开和声计划：

```bash
python3 script/bv_raw_to_midi.py WORK_DIR_OR_BV_PLAN_JSON --render-plan [-o PATH] [--force]
```

默认输出同目录 `bv_raw.json`，`--force` 替换已有结果。v1 与 v2 的计划格式见[和声技能说明](skill/background-vocals/SKILL.md#plan-format)。

导出 MIDI：

```bash
python3 script/bv_raw_to_midi.py BV_RAW_JSON [-o PATH] \
  [--ticks-per-beat NUMBER] [--velocity NUMBER] [--tolerance BEATS] [--allow-standalone]
```

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `BV_RAW_JSON` | 必填 | 待导出的 `bv_raw.json` |
| `-o PATH` / `--output PATH` | 同目录 `bv.mid` | 指定输出 MIDI 路径 |
| `--ticks-per-beat NUMBER` | `480` | 导出 MIDI 的时间分辨率，即每个四分音符包含的 tick 数，取值为 `1..32767` 的整数 |
| `--velocity NUMBER` | `80` | 写入所有 MIDI note 的 velocity，必须为 `1..127` 的整数 |
| `--tolerance BEATS` | `0.01` | 导出前量化与旋律对齐检查的误差宽容度，单位为 beats，范围为 `0..0.05` |
| `--allow-standalone` | 关闭 | 用于缺少 source melody 且无歌词的旧项目，跳过 melody 对齐与歌词继承检查 |

示例：

```bash
python3 script/bv_raw_to_midi.py output/krz-demo/bv_raw.json \
  --output output/krz-demo/bv_960.mid \
  --ticks-per-beat 960 \
  --velocity 72 \
  --tolerance 0.005
```

如果输出 MIDI 已存在，脚本会询问是否覆盖，只有输入 `y` 才会继续。默认导出必须在同目录找到包含 melody 的 `melody.json` 或 `canonical.json`，以校验旋律对齐与歌词一致性。只有完全无歌词的旧项目可以显式使用 `--allow-standalone`。

`--ticks-per-beat` 决定 JSON 中的 beat 时间在 MIDI 文件内使用的整数刻度，速度、音符时值和演奏结果保持原样。默认值 `480` 可以准确表示本项目常用的 `1/32_bar` 与三连音网格。以下情况可以手动设置：

- 目标 DAW、硬件或后续处理工具要求特定的 PPQ / ticks-per-beat。
- 需要与已有 MIDI 工程使用相同的时间分辨率。
- 后续还要在 DAW 中进行更细的人工 timing 编辑，可使用 `960` 等更高值。
- 为兼容只接受较低 PPQ 的旧设备或软件，需要降低该值。

### `script/project_common.py`

该文件保存项目限制与时间线计算逻辑，由其他脚本调用。

## 输出目录

标准作品目录结构如下：

```text
output/<作品名>/
  canonical.json
  melody.json
  chord.json
  note.txt
  bv_plan.json
  bv_raw.json
  bv.mid
```

| 文件 | 作用 |
| --- | --- |
| `canonical.json` | 完整 MIDI 解析结果 |
| `melody.json` | 主旋律、header 与可选 control 数据 |
| `chord.json` | 和弦音数据 |
| `note.txt` | 按小节整理的文本和弦谱 |
| `bv_plan.json` | 可修订的和声计划 |
| `bv_raw.json` | 展开后的背景人声数据 |
| `bv.mid` | 可导入 DAW 的最终 MIDI |

## 边界

- 本项目生成和声设计初稿，供作者继续试听、编辑和制作。
- 和弦谱脚本按支持的 pitch-class 集合识别和弦。复杂高叠和弦与复合和弦需简化输入，特殊命名与歧义由 Agent 或作者复核。
- Agent 负责和声创作，脚本负责展开与校验。
- Python 校验器检查结构与常见音乐风险。歌词语义、音色、演唱难度和情绪由作者结合试听判断。
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

Python 脚本在本地处理输入 MIDI、歌词、中间文件和生成结果。

和弦复核与背景和声编写由用户选用的 Agent 完成。使用云端 Agent 时，作品内容按所选服务、账户设置和部署方式传输与保存。有保密要求的作品应选择符合要求的部署与隐私配置。

使用 Agent 前，请确认对提交的作曲、旋律、和弦、歌词及相关素材拥有必要权利或已取得授权，并核对后续使用的授权范围。

## 许可证

本项目使用 GNU General Public License v3.0，详见 `LICENSE`。
