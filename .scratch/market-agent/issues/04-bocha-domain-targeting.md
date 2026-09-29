# 04: 博查域名定向

**What to build:** 博查检索器支持定向域名搜索，行为与其他检索器（如 Tavily）一致：调用方能通过域名列表把搜索限制在权威站点上，查询中的 site: 记号也能被正确处理。

**Blocked by:** None (can start immediately)

**Status:** done

- [x] query_domains 规范化为 host（去 scheme/路径/端口）后以 include 参数传给博查 API；多个域名用 `|` 连接，最多 100 个
- [x] 查询文本中的 site: 记号被提取并入 include，并从查询中剥离（不让 web-search 把 site: 当字面词搜索）
- [x] 规范化后域名列表为空时不传 include 字段（请求体与现状相同）
- [x] mock HTTP 单测覆盖以上行为；不影响未传域名的现有调用
