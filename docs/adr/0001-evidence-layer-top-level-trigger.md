# 证据层独立于编排：顶层研究者自动触发，子研究者抑制

- 状态：accepted
- 日期：2026-09-29

## 决策

证据层实现为独立模块，接口是"聚合后的来源列表 → 证据产物"。它由顶层研究者（Top-level researcher）在 `conduct_research` 末尾自动触发，配置开关 `EVIDENCE_EXTRACTION_ENABLED` 控制；为子查询/子主题创建的嵌套实例（子研究者）显式传入 `is_sub_researcher=True` 以抑制抽取。

## 原因

deep 与 detailed 两条流程后续会合并，编排层接口不稳定；证据层只依赖"来源列表"这一稳定输入，挂在顶层研究者上可在合并后原样保留。自动触发保证 WebSocket / HTTP / CLI 全入口覆盖，避免编排器"忘记调用"。

## 考虑过的方案

- **编排器显式调用**（BasicReport/DetailedReport/CLI 各调一次）：存在漏调风险，且 detailed 编排在合并时还要返工。
- **每个研究者实例都自动触发**：子研究者会产生 N 次重复抽取，浪费成本且产物碎片化。

## 后果

- DetailedReport 在合并前只覆盖主研究的来源（子主题来源不聚合），合并后由合并编排器统一。
- 新增 `is_sub_researcher` 构造参数，需要 deep/detailed 两处创建点传参。
