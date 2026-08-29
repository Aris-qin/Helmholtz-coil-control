# Progress

Updated: 2026-07-27

## Now
- 文献调研完成（2026-07-27）
  - PubMed 10 篇（2019-2025），全部 original research
  - Corpus 已有 6 篇强相关（质量评分 0.90/0.417 最高）
  - GitHub 12+ 仓库（magpylib 362⭐ 是唯一高质量 star 项目）
- 调研档案已建: `docs/literature-survey/`
  - README.md（调研背景、范围、结论）
  - pubmed_2025-07-27.json（10 篇完整元数据 + 摘要）
  - corpus_existing_2025-07-27.md（corpus 提取）
  - github_resources_2025-07-27.md（GitHub 资源）
- 项目记录体系搭建完成（2026-07-27）
  - README.md（项目定义） ✅
  - progress.md（进度） ✅
  - decisions.md（决策） ✅
  - issues.md（问题与教训） ✅

## Blocked
（无）

## Next
- L 审阅 PubMed 10 篇调研结果，决定哪些 ingest 到 wiki-confirm
- L 决定磁线圈项目跟调研方向怎么结合（硬件平台复用 / 控制算法升级 / 闭环验证）
- L 评估 magpylib 跟 ESP32 协议打通做仿真验证的可行性
- 硬件层（线圈/功率放大/DAC 电路）实施

## Waiting
- L 审阅 PubMed 文献

## Done recently
- [07-27] 文献调研：10 PubMed + 6 corpus + 12 GitHub，档案归档在 `docs/literature-survey/`
- [07-27] 项目记录体系：README / progress / decisions / issues 四件套搭建
- [07-17] 项目目录结构整理（命名冲突警告：项目根 main.py ≠ python/main.py）
- [05-20] README_ORIGINAL.md 写完（4852B），软件层封板
- [05-18] coil_serial.py 串口库完成（262 行）
- [05-14] main.py PySide6 GUI 完成（854 行）
- [05-14] coil/main/main.c ESP32 固件完成（809 行）
