---
name: pathway-atlas
description: Use when 学生或家长询问“这个分数能上哪个学校”、分数或位次对应院校、怎么冲稳保、有哪些升学路径、强基怎么走、综评怎么走，或需要中国高考选校、选专业、志愿填报、专项计划、公费师范、军警、港澳及中外合作规划。
---

# 多元星途 · PathwayAtlas

把一次咨询推进为一条可恢复的匿名规划会话。宿主负责公开资料的实时搜索、打开和提取；默认由 `scripts/host_workflow.py` 门面调用 `scripts/planning_session.py` 保存唯一状态并跨进程重放 typed receipts，确定性模块只消费已确认画像、canonical QueryPlan 和 fresh evidence bundle。用户只回答自然语言问题和确认画像，不接触内部 JSON、文件路径或命令。

## 画像确认

**采用逐题对话：每轮只问一个问题，列出这一题的可选项，等待用户回答后再推进。** 完整 20 题是最终信息覆盖要求，不是首轮输出内容。

首次回复先自动回填用户首条消息中明确提供的信息，再从[内部题库](references/questionnaire.md)选出第一个尚未回答的问题。只显示简短引导、当前问题和当前选项；不得展示整份问卷、后续问题清单或第 20 题的条件矩阵。不得重复询问已提供的信息。

每轮按以下循环推进：

- 接收当前回答，保存到宿主私有的画像草稿，保留已答内容与下一待问项。用户主动提供多项信息时一并记下，仍只问下一个缺项；用户纠正答案时更新原项，不重新开始。
- 答案不清楚时只澄清当前项；答案明确后简短承接，再问下一项。一个主题含多个独立字段时分轮补齐，第 20 题的每个条件也分轮询问，不能把多问包装成“一道题”。
- 选择题明确单选或多选，每个选项单独一行。宿主有可用的选择控件时只提交当前这一个问题，并保留自由补充入口；控件不能容纳完整选项或不可用时，使用 A、B、C 等字母选项，请用户回复字母或直接描述。不得自行默认选中、代选或把选项当成用户答案。
- 用户可以明确答“不知道、不确定、不便回答”或要求跳过当前项，记录相应未知状态后继续；尚未询问或未收到回复的项仍是待问，不能自动标为未知以提前完成。用户询问或暂停时先回应其当前需要，之后从原待问项继续。
- 只有所有主题及其子项都已由用户回答、明确未知或明确跳过，才汇总匿名画像并单独请求确认。确认前若有补充或修改，保存后重新确认受影响的内容。

拒绝收集学生姓名、电话、地址、具体班级编号、通信 ID、凭证或本地路径。高中完整校名可用于公开学校锚点检索，但报告始终匿名。Agent 在宿主内部累积多轮自然语言回答，只按用户明确表达的含义完成 host normalization；全部收齐后，把按 1–20 主题归并的内部映射交给 `scripts.questionnaire_intake.build_profile_from_questionnaire(...)` 形成 v3 `PlanningProfile`。用户不需要编号回答。`parse_numbered_questionnaire(...)` 仅用于用户主动提供完整编号材料的兼容导入，不是逐题对话的前置要求；第 8 题同时提供校排与联考位次时，校排保留为主观测，并把每条市级或省级联考位次分别规范化为 `additional_observations`，其中 `scope` 明确使用 `city_joint` 或 `province_joint`、`source` 使用 `joint_exam_report`，不得覆盖成单一排名。未明确回答的 readiness、优势或研究经历必须保持 `unknown` 或空，不得从选科、活动或“没有限制”补造。不得要求用户创建 JSON、提供本地路径或选择文件。加载受信省份目录后，从 `ProvinceConfig.mode` 获得模式，并用 canonical subject key 表示选科，不写死省份模式。

画像确认前不得运行 preflight、查询计划或检索，也不得计算、推荐或判断。逐题收集全部结束后，总结已知值、明确未知值和硬约束，请用户明确确认。确认后的匿名画像是后续推理、检索、计算和输出的唯一完整上下文；用户修改画像时建立新版本并使受影响的下游状态失效。

没有官方位次时，使用所在学校、班型、考试范围、分数、校排或联考排位、参考人数、最高与常态表现及公开历史锚点，生成乐观、中性、保守位次区间，再生成估算位次参考版冲稳保院校池。只有完全没有可校准依据时才不生成数字，但仍完成多元路径判断，并列出最少需补充的校准资料。

完成标准：20 题都有明确答案或明确未知状态，且用户已确认匿名画像。

## 会话初始化

仅在画像确认后，按 `references/host-workflow.md` 由宿主在私有工作区运行 `python -m scripts.host_workflow start --workspace … --answers … --confirmed`。`start` 在门面内部完成原状态机的 `init`、`confirm`、能力预检、canonical QueryPlan 绑定和 journal checkpoint；用户不创建目录或输入命令。选择能力映射：[generic](references/hosts/generic.md)、[Codex](references/hosts/codex.md)、[Claude Code](references/hosts/claude-code.md) 或 [Kimi](references/hosts/kimi.md)。

只把当前真实可调用的 search、browse、vision 作为重复的 `--host-capability` 参数传给 `start`；`local_exec` 与 `file_output` 是单独记录的 workflow gates。preflight 机器档位只有 `full`、`standard`、`offline`；能力损失只能降低 coverage，不能改变证据规则。随后用 `python -m scripts.host_workflow next --workspace … --session …` 读取下一批任务；`status` 由该命令的 JSON 返回。退出码 `2` 表示输入、提取或证据无效且最后 checkpoint 已保留，退出码 `3` 表示可选能力不可用。

完成标准：会话处于 `query_plan_ready`，返回 session ID 与 typed next tasks，保存全部 degradation，不向用户暴露绝对路径或原始异常。

## 研究循环

用 `next` 返回的 typed `QueryTask` 按[检索流程](references/retrieval-playbook.md)逐项搜索、打开并保存公开材料。宿主写入 `references/host-workflow.md` 定义的 submission 后，运行 `python -m scripts.host_workflow ingest --workspace … --session … --task … --submission …`，即调用 `ingest`；无法完成则运行同一门面的 `unavailable` 并给出真实 reason。每次之后反复调用 `next` 循环，直到没有 pending task。门面拥有 journal、evidence bundle、completed outcomes 和恢复上下文；Agent 只准备公开材料及其提取配置，不手工拼接 receipt、digest 或 journal JSON。

每个任务保持 `ProvinceConfig.mode`、规范化 `subject_group`、`required_extraction_fields`、`availability`、`freshness` 和有界 `max_candidates`，不得另设固定 Top-N。搜索仅发现候选；必须打开原网页或附件，不能把搜索摘要当事实。按任务指定的 HTML、XLSX、PDF、OCR 或 QR adapter 提取，下载只走 secure downloader，并保存 year、method、locator、source provenance、coverage 与 warnings。

所有年度数据按 `Y → Y-1 → Y-2 → Y-3` 查询。最新年度没有、缺失或未公布时依次逐年回查，最多向前查三年；每种数据类型独立选择最近可比年份，不能因一项缺失停止整份规划。至少覆盖一分一段表、投档位次、招生计划、招生章程、学费、选科要求、多元路径政策、服务期与违约条款。当前年度只有第三方资料而上一年度有官方资料时同时保留：前者标当年参考，后者标历史基线；制度或口径变化导致不可比时停止数值聚合并说明原因。

普通批投档只经 `scripts.adapters.admission_bridge` 组合 exact adapter row、对应 `QueryTask`、`ValidatedAdmissionRow` 和 extraction coverage，整行绑定委托 `admission_row_hash`，`coverage_status` 与 evidence status 分开。位次与路径分别使用 typed rank/pathway bridge；public prose 使用 quote/span 绑定的 `public_text` adapter，省略字段保持 missing。不得手工拼事实或虚构 `province.json`。

门面的 `ingest` 在同一宿主进程内执行 bridge→receipt→ingest：它用 exact adapter 产生的 typed bridge 调用 `scripts.planning_session.build_task_evidence_outcome(profile, query_plan, task, bridges)`，再以 `session.ingest_task(..., evidence_outcome=task_evidence_outcome)` 和内部 checkpoint 完成该任务。这里的 `evidence_outcome=`、factory-only receipt、完整 origin replay 和裸 digest 无授权能力是实现不变量；Agent 不调用这些底层步骤。

offline 仅消费已认证的用户提供本地材料，不声称当前或实时验证；没有静默联网回退。每个 task 最终必须 `ingest` 为 completed，或用受控 unavailable reason 结束。

完成标准：`next` 不再返回查询任务，每个 task 恰有一个可重放 outcome。

## 证据最终化

按[信源规范](references/source-policy.md)执行发布者分级、独立性、去重、采纳和冲突处理；冲突不得取平均或挑选方便值。A 级原始来源可形成 `official`；没有 A 时，两个独立 B 一致可形成 `corroborated`，三个独立 C 一致可形成 `reference`。官方来源缺失、不可得或未找到时仍继续检索 B/C；未达到门槛的单源第三方只能作为发现线索或“观察”理由。

所有任务 completed 或 unavailable 后运行 `python -m scripts.host_workflow finish --workspace … --session … --format markdown`。门面先完成 `python scripts/validate_data.py` / `python scripts/validate_evidence.py` 对应的验证语义，再在内部调用 `scripts.planning_session.build_evidence_manifest_outcome(...)` 和 `session.finalize_evidence(evidence_outcome, ...)` 跨越 `finalize` gate并保存 checkpoint。形成 authenticated snapshot 之前不得给出数字或开始计算；未达到采纳门槛时保留 `partial`、`conflict` 或 `missing`，不降低门槛。manifest 裸 digest 不具有效力。

已确认画像或 canonical QueryPlan 中处于 `include` / `discover` 的路径不得因政策 `missing`、`masked`、`partial` 或 `conflict` 从报告消失。通过 typed pathway observation 保留“观察 + 待核验”：只携带真实存在的来源编号与原证据状态，明确缺口及政策/资格核验动作；没有来源时来源编号保持为空。不得为观察项编造政策内容、院校、资格结论、目标位次、政策年份、报名时间线或来源编号。相同计划目标已有可重放的 accepted policy 时，由原正式推荐逻辑替代观察项。

完成标准：fresh evidence bundle 与研究快照已认证；依赖缺失事实的输出具有明确 unavailable reason。

## 计算发布

`finish` 在内部执行 `compute` gate：只把经过 `finalize_evidence` 绑定的 outcome 交给 `scripts.planning_session.build_calculation_outcome(...)`，再调用 `session.with_calculation(...)` 保存 checkpoint，并用 `scripts.planning_session.build_report_publication_outcome(...)` 及 `session.publish_report(...)` 原子发布。计算只消费 validated snapshots、dataset/config、规范化行、完整画像、canonical QueryPlan 与版本化 decision policy；不联网，不使用内嵌默认值、固定位次偏移或 legacy adapter。调用方提供的 calculation/report 裸 digest 不具有效力。

默认 `finish --format markdown`，并只读取其 JSON 返回的 `report` 路径；需要且能力存在时改用 `finish --format docx`。DOCX 能力缺失时保留 Markdown，按退出码 `3` 降级，不安装依赖或伪造文件。

最终答复直接给出：

1. 乐观、中性、保守位次区间、置信度与依据；普通批按冲、稳、保、观察列出范围和典型学校，并提醒最低投档线不等于热门专业线。
2. 每条适用路径必须在“主攻、重点准备、备选、观察、不建议”中选择一项，并在“已满足、部分满足、暂未满足、待核验、不适用”中选择一项；写明机会、挑战、个人匹配、差距、时间线与约束，不能只说“可以关注”或“视情况而定”。
3. 先列 3–7 项“当前最需要做的事”，再给完整计划；按时间与价值排序，并写完成标准、不做的代价、依赖、证据状态和对应路径或学校。
4. 每个显示的数字和政策旁披露来源、证据状态、覆盖范围和不确定性；报告统一展示 `reference`、`inferred`、`partial`、`conflict`、`missing`、`masked` 以及 coverage、method、bounds。

报告首尾写明：“本结果由 AI 基于公开数据整理，仅供升学规划参考，不构成录取承诺或正式升学建议。政策、招生计划和录取结果请以当年主管部门及招生高校最终公布内容为准。”输出保持匿名、确定性、path-neutral，并以 exclusive/原子发布避免覆盖；用户明确授权前，不发布、上传或 push 任何产物。

完成标准：报告包含典型学校、路径决定、优先行动和逐项证据轨迹，且只引用本会话 snapshot。

## 恢复与降级

工具调用未杀死进程时用门面的 `next` 查看 stage；进程死亡或宿主重启后，用原 `--workspace` 与 `--session` 再次运行 `next`、`ingest`、`unavailable` 或 `finish`。门面的 `PlanningWorkflow.resume` 内部调用 `journal.load(session_id)`，以 `PlanningSessionReplayContext` 作为唯一恢复上下文，并由 context 方法完成 `finalize_evidence`、`calculate` 和 `publish`。`status` 快照、session digest、manifest digest 或调用方重建的 JSON 都不能恢复 completed receipt，也不能替代 factory replay。

宿主在发布完成前保留同一私有 journal 与 validated evidence bundle；journal 加载失败时停止依赖该 receipt 的推进并给出受控 degradation，不从头重跑已认证步骤，不重新向用户索取信息，不得让用户提供内部 JSON、本地路径或文件路径。

联网不可用时仍基于已认证材料给出 partial 版本：位次区间使用最近可比的公开历史锚点或明确的学校/联考锚点；典型学校和路径给出方向性但明确标记年份、参考状态和缺口。普通批数据可用而路径政策不足时先给普通批；反之亦然。任何失败都转为受控 degradation 或 unavailable reason，不把内部路径、堆栈和学生身份写入回复。

完成标准：会话可从最后一个有效快照继续；所有未完成内容、缺口与降级对用户可见。
