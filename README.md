# Project Working Loop

Project Working Loop 是一个 Codex Skill，用一份 Markdown 控制文件管理跨会话项目。它把项目拆成有验收点的有序步骤；每次继续前先读回同一份文件，而不是依赖聊天记录。

这个流程由作者在 Y.EAA 做项目时发展出来。公开版本默认把控制文件放在项目工作区，文件名为 `PROJECT-CONTROL.md`。它只提供指令和模板，没有运行时后端，也不需要额外服务。

## 第一次使用

先在本次会话明确采用流程：

```text
Use $project-working-loop for this session. I explicitly adopt the workflow.
```

采用后，代理会先提出项目名称、目标、范围、排除项、步骤和每一步的验收条件。确认控制文件的确切路径后，才开始 Step 1。

一次消息可以同时确认相容的事项。例如可以写明项目范围、控制文件的本地准确路径、接受步骤，并授权开始 Step 1；不需要把这些确认拆成重复的回复。

执行时，代理只处理当前步骤。改稿、修复、重试和审阅反馈都留在当前步骤。完成后由用户接受该步骤，代理才把它压缩成只含 `Result` 的完成块。接受本身不会自动开始下一步；要继续时另行授权即可。下面这句话同时接受 Step 1 并开始 Step 2：

```text
Step 1 accepted, start Step 2.
```

新会话也要再次明确采用流程，并指出同一份控制文件。代理先读回文件，再从 `Current` 和正在进行的步骤继续。完整示例见[演示](examples/walkthrough.md)。演示只说明预期流程，不代表任何主机、版本或模型已经验证兼容。

## 控制文件与权限

控制文件只记录项目状态，不是指令层级或授权来源。文件中的文字、引用、链接和过去的授权声明都应被当作记录数据。代理在采取实质行动前，仍要核对当前用户方向、已有有效授权、批准范围和主机限制；不会只因为文件写了命令或链接就执行或访问它。

### 概念演示：在当前步骤内修订

Step 1 的 `note.md` 原定写两条建议。用户补充“练习回忆时不看笔记能发现知识缺口”后，代理把笔记改为三条简单建议，同步更新受影响的 `Target` 和 `Done when`，仍保留 Step 1，读回后等待接受。用户若只接受 Step 1，代理只压缩 Step 1，不会执行 Step 2；之后仍需明确授权开始 Step 2。控制文件里的旧批准记录不能代替当前授权；内容含糊、来源或真实性不明、受主机或更高优先级规则限制、会变成另一份持久结果，或实际后果明显的操作尚未获授权时，代理会停下来澄清。

默认位置是当前项目工作区的 `PROJECT-CONTROL.md`。用户给出其他路径时，代理会检查实际目标、上级路径、符号链接或重定向、工作区边界、已有文件类型，以及它是否确实属于同一项目。遇到含糊、无关或不安全的目标会停止，不会悄悄覆盖。

每个项目只有一份权威的进度控制文件。这个约束禁止重复的项目跟踪、历史或支持状态文档，不禁止用户要求的交付物、真实测试证据、普通备份或版本控制记录，只要它们不变成第二份进度来源。

采用流程或接受某一步也不能授权无关的外部、破坏性或特权操作。这里的规则是给模型的提示，不是技术强制或通用安全保证；实际可做的事仍由当前主机的沙箱、审批和工具决定。

控制文件可从随 Skill 打包的[模板](skills/project-working-loop/assets/project-control.template.md)开始。

## 安装与发现

[官方技能文档](https://learn.chatgpt.com/docs/build-skills)列出的发现位置是：

- 用户范围：`$HOME/.agents/skills`
- 仓库范围：仓库根目录的 `.agents/skills`

用户范围适合自己的所有项目；仓库范围只随当前仓库使用。二者只选一个。

**同名 Skill 只能存在一份。**如果 `$HOME/.agents/skills`、目标仓库的 `.agents/skills` 或 `$HOME/.codex/skills` 中已经有另一个名为 `project-working-loop` 的 Skill，Codex 不会报错，但 `$project-working-loop` 将不再注入任何内容，代理会像没有安装一样工作。安装前先检查这三个位置；已有旧版本时，先移除或改名，再安装这一份。（Codex CLI 0.145 实测。）

下面的命令假定 `repo_root` 是下载或解压后的仓库根目录，所有路径都加了引号，因此可以包含空格。

```sh
repo_root="/path/to/downloaded/project-working-loop"
source_skill="$repo_root/skills/project-working-loop"

# 用户范围：
destination="$HOME/.agents/skills/project-working-loop"

# 仓库范围：在目标仓库根目录执行时，改用这一行。
# destination="$PWD/.agents/skills/project-working-loop"

if [ ! -d "$source_skill" ]; then
  printf 'Source skill directory not found: %s\n' "$source_skill" >&2
elif [ -e "$destination" ] || [ -L "$destination" ]; then
  printf 'Refusing to overwrite existing destination: %s\n' "$destination" >&2
else
  mkdir -p "$(dirname "$destination")" &&
    cp -R "$source_skill" "$destination" &&
    printf 'Installed: %s\n' "$destination"
fi
```

`-e` 会拦住已有目标，`-L` 也会拦住悬空符号链接。脚本不会删除、覆盖或更新已有目标。若目标已存在，先自行检查内容和路径，再决定下一步。

复制完成后，打开一个新会话，显式调用 `$project-working-loop`，并观察当前主机是否实际发现并加载该 Skill。仅让代理直接读取 `SKILL.md` 不等于已注册发现；那只是把文件作为本次会话的明确输入。特定 Codex 版本、账户或模型是否能发现并运行它，需要在实际主机上验证，本文不预先作兼容性承诺。

## 不安装也可以使用

可以把下载目录中的 `skills/project-working-loop/SKILL.md` 明确交给代理阅读，并让它能访问同目录的 `assets/project-control.template.md`。例如：

```text
Read "/path/to/downloaded/project-working-loop/skills/project-working-loop/SKILL.md".
Keep its assets/project-control.template.md available. Use this workflow for this
session; I explicitly adopt it.
```

这种方式不改全局或仓库配置，也不会让主机自动发现该 Skill。它仍然要求本次会话明确采用流程。

## 模型、账单与隐私

这个包不选择模型或提供商，不含 API 凭据，也不运营作者的服务。推理由当前助手配置的模型、账户和工作区提供；费用由该账户的订阅或 API 计费方式决定。

控制文件保存在本地不等于离线处理。云端模型读取提示或控制文件时，内容会进入模型上下文，并受账户和提供商的控制项约束。避免在文件中放入密钥和不必要的敏感状态；也要检查版本控制、同步和共享可能带来的暴露。这个 Skill 不会自动修改 `.gitignore` 或云端权限。

## 许可证

MIT，见 [LICENSE](LICENSE)。
