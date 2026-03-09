# Background Vocal Generator 项目 Rules

## 1. 模式切换

本项目支持三种对话模式：

- `=pt` 普通模式。
- `=hx` 和弦整理模式。
- `=hs` 和声整理模式。

默认模式是 `=pt`。

## 2. `=pt` 普通模式

用途：

- 产品讨论。
- 开发讨论。
- 文档修改。
- Python 脚本实现。
- 调试和测试。
- 普通问答。

行为：

- 不自动加载 `skill/chord-chart/SKILL.md`。
- 不自动加载 `skill/background-vocals/SKILL.md`。
- 按普通 Codex 协作方式处理用户请求。

## 3. `=hx` 和弦整理模式

用途：

- 将 `output/<作品名>/chord.json` 整理成完整和弦谱。
- 输出到 `output/<作品名>/note.txt`。

行为：

- 加载并遵循 `skill/chord-chart/SKILL.md`。
- 如果用户发出对特定作品或文件的执行指令，直接开始和弦整理。
- 如果用户只是讨论规则、文档、提示词或产品设计，则切回 `=pt` 处理实际问题。

触发示例：

```text
=hx 处理 output/demo/chord.json
=hx 给 demo 生成和弦谱
=hx 整理这个作品的 chord.json
```

输出约定：

```text
output/<作品名>/note.txt
```

## 4. `=hs` 和声整理模式

用途：

- 使用 `output/<作品名>/melody.json` 和 `output/<作品名>/note.txt` 生成 BV 轨道 JSON。
- 输出到 `output/<作品名>/bv_raw.json`。

行为：

- 加载并遵循 `skill/background-vocals/SKILL.md`。
- 如果用户发出对特定作品或文件的执行指令，直接开始和声整理。
- 如果用户只是讨论规则、文档、提示词或产品设计，则切回 `=pt` 处理实际问题。

触发示例：

```text
=hs 处理 output/demo
=hs 给 demo 生成 BV
=hs 根据 demo 的 melody.json 和 note.txt 生成 bv_raw.json
```

输出约定：

```text
output/<作品名>/bv_raw.json
```

## 5. 执行倾向判断

在 `=hx` 或 `=hs` 模式下，只要用户表达出对具体作品、文件、目录或输出物的执行意图，就开始执行对应任务。

执行意图包括：

- “处理”
- “生成”
- “整理”
- “跑一下”
- “对这个文件执行”
- “给这个作品做”

如果没有明确执行目标，且用户是在讨论需求、修改规则、询问实现细节或要求解释，则使用普通模式回答。
