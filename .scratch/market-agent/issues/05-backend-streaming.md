# 05: 后端流式接入（WS 事件 + 下载路径）

**What to build:** 证据层的进度与统计通过 WebSocket 实时推给前端；证据产物进入现有下载路径体系，用户可在任务结束后拿到证据文件。

**Blocked by:** 03（顶层接入）

**Status:** ready-for-agent

- [ ] 开启证据层时，WebSocket 收到 evidence 进度/统计事件：开始、完成、按来源等级的计数
- [ ] path 事件包含 evidence 产物下载路径，路径格式与报告文件一致（正斜杠 + URL 编码），不在 Windows 上出现反斜杠坏链
- [ ] 前端处理新增事件类型不报错（可作为日志行展示）
- [ ] 一次完整 WebSocket 研究流程验证：事件顺序与产物可访问
