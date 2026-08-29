# Decisions

> 只追加，不删不改。每条记录决策内容、日期、原因。

- **Decision:** 项目记录体系采用 4 个 Markdown 文件 + kanban.db，与 ar-review / lipo-degradation / nsfc-egfr 项目保持一致
  - **Date:** 2026-07-27
  - **Reason:** Q 已有 3 个项目（ar-review / lipo-degradation / nsfc-egfr）使用 `README + progress + decisions + issues` 四件套，kanban 走未完成任务流；为保持风格统一、便于 agent 跨项目理解，沿用

- **Decision:** 文献调研结果归档在项目本地 `docs/literature-survey/`，不写入 wiki-confirm
  - **Date:** 2026-07-27
  - **Reason:** Q 强调"先罗列给我看，我要看完后再考虑同步哪些"；调研产物先归项目本地，等 L/Q 审阅完决定哪些 ingest 到 wiki-confirm
  - **Files:** `docs/literature-survey/{README, pubmed_2025-07-27.json, corpus_existing_2025-07-27.md, github_resources_2025-07-27.md}`

- **Decision:** LLM Wiki 走默认路径 `~/wiki/`，不与 wiki-confirm 混用
  - **Date:** 2026-07-27
  - **Reason:** Q 区分"wiki-confirm = 人工 review 精选"与"LLM Wiki = agent 自动生成"；LLM Wiki 由 agent 在后台自动 ingest，Q 不手动翻阅，过几天 review 整体质量再决定

- **Decision:** "震荡磁场/磁性脂质体/旋转磁场" 实际是 3 个独立研究方向，调研按 3 条独立路径展开
  - **Date:** 2026-07-27
  - **Reason:** PubMed 检索结果显示：磁性脂质体主要配对磁热疗（高频产热），OMF 主要用于食品/物理效应，RMF 主要用于磁驱动——3 者在物理机制、参数范围、应用领域均不重叠
  - **Note:** 后续研究定位需明确聚焦路径（推荐 A: 磁热疗 + 脂质体；B: 旋转磁场驱动；C: 震荡磁场应用）

- **Decision:** GitHub 调研结论：磁性脂质体方向代码 0 公开（`magnetic liposome` 搜不到）
  - **Date:** 2026-07-27
  - **Reason:** GitHub API 13 个查询覆盖 6 个主方向 + 7 个补充方向，磁性脂质体子领域代码/数据可能在论文 supplementary 或实验室内部，未公开
  - **Implication:** 项目代码实现必须自己写，无法 fork 现成方案

- **Decision:** 串口协议使用 ASCII 文本格式（`CMD:`, `OK:`, `ERR:`），115200 8N1
  - **Date:** 2026-05-18
  - **Reason:** 调试友好，串口监视器可直接阅读；心跳 5 秒周期
  - **Protocol doc:** `docs/protocol.md`

- **Decision:** 软停止用 100 步渐减算法，防止电流突变导致线圈震荡
  - **Date:** 2026-05-18
  - **Reason:** 线圈是感性负载，硬切断会产生反电动势损坏器件；ALL_STOP 仅在紧急情况用

- **Decision:** Python 上位机采用 PySide6（Qt）+ matplotlib 实时波形
  - **Date:** 2026-05-14
  - **Reason:** Qt 跨平台 + matplotlib 学术圈标准；便于二次开发
