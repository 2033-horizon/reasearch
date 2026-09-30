# 11: DOCX 真脚注（Word/WPS 当页脚注）

**What to build:** Word 报告显示真正的当页脚注：正文 [序号] 上标与当页底部"标题 — 发布主体（等级）· 日期 · URL"脚注一一对应，URL 可点击；同一数据多处引用时每处都有内容相同的脚注；Word 与 WPS 打开均可见、Word 另存 PDF 后保留。实现不引入 pandoc 等外部工具。

**Blocked by:** 10（证据驱动写作）

**Status:** ready-for-human

- [x] 含脚注定义的报告 Markdown 转换出的 .docx 打开后，正文上标与当页脚注一一对应
- [x] 脚注内容为"标题 — 发布主体（等级）· 日期 · URL"，URL 为可点击超链接
- [x] 同一证据组多处引用时，每处显示独立编号的脚注、内容一致
- [x] 结构测试解包校验脚注部件、正文引用与脚注文本（离线、不依赖本机安装 Word）
- [x] 无脚注输入时 DOCX 输出与现状一致（回归）
- [ ] Word 与 WPS 人工验收通过并记录；Word 另存 PDF 后脚注保留

## Comments

2026-09-30 实现完成（`gpt_researcher/evidence/docx.py` + `backend/utils.py` 接入）：

- 离线结构测试覆盖脚注部件/正文引用/脚注文本/超链接关系/内容类型，并用 python-docx 回读验证 zip 合法；无脚注输入不产生脚注部件（回归）。
- 真实端到端产出的 `outputs/research_a65d60aa85eb.v2.docx` 经解包校验：72 处正文 `w:footnoteReference` 对应 72 条脚注（同一证据组重复引用 → 每处独立编号、内容一致），脚注文本符合"标题 — 发布主体（等级）· 日期 · URL"，URL 位于 `word/_rels/footnotes.xml.rels` 外链。
- 待人工：用 Word 与 WPS 各打开一次上述文件确认当页脚注呈现与 URL 可点击，并用 Word 另存 PDF 确认脚注保留（本环境未安装 Word/WPS）。
