# 一次项目怎样跨会话继续

下面是一个虚构示例，用来说明控制文件如何保存项目状态。它不是 Y.EAA 的实际项目，也不证明任何工具兼容性。示例使用默认文件 `PROJECT-CONTROL.md`；可从[模板](../skills/project-working-loop/assets/project-control.template.md)复制一份开始。

## 第一个会话：确认项目

用户先明确采用流程：

```text
Use $project-working-loop for this session. I explicitly adopt the workflow.
```

代理提出一个两步项目，用户确认后才写入控制文件并开始 Step 1。此时的 `PROJECT-CONTROL.md` 可以是：

```markdown
# 整理研究笔记

- Outcome: 把现有研究笔记整理成一份可审阅的提纲和一份简短术语说明。
- Current: Step 1 of 2 — 确定提纲

## Steps

### [In Progress] Step 1: 确定提纲

- Target: 形成可供审阅的两级提纲。
- Done when: 用户接受提纲结构。
- Current state: 已收集现有笔记的主题，正在归并重复内容。
- Next: 写出提纲草案并请用户审阅。

### [Not Started] Step 2: 编写术语说明

- Target: 根据已接受的提纲写出简短术语说明。
- Done when: 用户接受术语说明。
```

代理在回复里同时给出文件路径和当前状态，而不是只贴一个链接。

## 仍在 Step 1 的修订

用户看完草案后说：“在提纲里加入术语说明的读者范围。” 这没有改变 Step 1 的验收点。代理读回 `PROJECT-CONTROL.md`，保留 Step 1 的编号，并替换该步骤的当前状态：

```markdown
### [In Progress] Step 1: 确定提纲

- Target: 形成可供审阅的两级提纲。
- Done when: 用户接受提纲结构。
- Current state: 提纲已补入术语说明的读者范围，等待用户确认结构。
- Next: 用户接受提纲结构。
```

Step 2 还没有开始。控制文件不保留上一版状态，也不会把这次修订单列成一个步骤。

## 接受并开始下一步

用户说：“Step 1 已接受，开始 Step 2。” 这句话同时接受 Step 1 并授权开始 Step 2。代理把 Step 1 压缩为只含 `Result` 的完成块，再读回文件。更新后的完整状态如下：

```markdown
# 整理研究笔记

- Outcome: 把现有研究笔记整理成一份可审阅的提纲和一份简短术语说明。
- Current: Step 2 of 2 — 编写术语说明

## Steps

### [Done] Step 1: 确定提纲

- Result: 两级提纲已获用户接受；术语说明面向提纲确定的读者范围。

### [In Progress] Step 2: 编写术语说明

- Target: 根据已接受的提纲写出简短术语说明。
- Done when: 用户接受术语说明。
- Current state: 已读回接受后的提纲，准备起草术语说明。
- Next: 写出术语说明草案并请用户审阅。
```

如果用户只说“Step 1 已接受”，代理完成 Step 1 的压缩后停在 Step 2 的未开始状态，等待另一条明确授权。

## 新会话：从同一文件恢复

新会话仍需明确采用流程。用户可以这样说：

```text
Use $project-working-loop for this session. I explicitly adopt the workflow.
Continue the project recorded in PROJECT-CONTROL.md.
```

代理先读 `PROJECT-CONTROL.md`，发现 Step 2 正在进行，再报告当前草案和下一步。它不从聊天记忆重建项目，也不会新建第二份控制文件。
