---
name: pathway-atlas
description: Use when 学生、家长或老师询问“这个分数能上哪个学校”、怎样看位次和冲稳保、有哪些升学路径、强基怎么走、综评怎么走，或需要中国高考选校、选专业、志愿填报及专项、公费师范、军警、港澳和中外合作规划；结合成绩、选科、兴趣和家庭条件，弄清值得准备的选择与下一步行动。
---

# 多元星途 · PathwayAtlas

![多元星途 PathwayAtlas：陪你看清选择](https://raw.githubusercontent.com/sarry12227/pathway-atlas/main/assets/brand/pathway-atlas-logo.png)

看清有哪些升学选择，知道下一步怎么走。面向学生和家长，通过一题一问了解个人情况，查证公开资料，在对话中讲清学校、专业、路径选择的理由、限制和准备行动；资料不足时明确缺口，给出有依据的部分结论或准备建议。

无需先懂招生术语。安装后说“请使用多元星途帮我做升学规划”，即可逐步开始；完整介绍与可复制安装提示词见[README：了解价值与开始使用](https://github.com/sarry12227/pathway-atlas#readme)。

默认先交付快速升学参考：本次分数、高考参考分数与省排、本省冲3稳4保5、强基/综评/港澳各冲1稳1保1、其他可行路径、现阶段行动。按[快速规划](references/quick-planning.md)进行一轮有界研究后使用 `brief`，由固定模板生成正文。深度核验才使用下面六阶段的完整研究流程；`scripts/host_workflow.py` 调用 `scripts/planning_session.py` 保存唯一状态并跨进程重放 typed receipts，只消费已确认画像、canonical QueryPlan 和 fresh evidence bundle。用户只回答自然语言问题和确认画像，不接触内部 JSON、文件路径或命令。

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

没有官方位次时，主动使用本校历史出口与校排、往年或明确可比学校锚点生成参考定位，并反查相应年份一分一段得到高考参考分数。高一、高二也应得到典型学校和准备路径，不以“还没高考，不能推荐大学”结束服务。若确实没有校准资料，继续输出来源支持的学校目标梯度，并明确个人折算尚缺的具体依据。校内月考、期中或校模考的 `scope` 为 `school`；`province_official` 只用于明确的正式省级高考成绩，不能把同满分的校考裸分直接当高考分。

完成标准：20 题都有明确答案或明确未知状态，且用户已确认匿名画像。

## 会话初始化

仅在画像确认后，按 `references/host-workflow.md` 由宿主在私有工作区运行 `python -m scripts.host_workflow start --workspace … --answers … --confirmed`。`start` 在门面内部完成原状态机的 `init`、`confirm`、能力预检、canonical QueryPlan 绑定和 journal checkpoint；用户不创建目录或输入命令。选择能力映射：[generic](references/hosts/generic.md)、[Codex](references/hosts/codex.md)、[Claude Code](references/hosts/claude-code.md) 或 [Kimi](references/hosts/kimi.md)。

只把当前真实可调用的 search、browse、vision 作为重复的 `--host-capability` 参数传给 `start`；`local_exec` 与 `file_output` 是单独记录的 workflow gates。preflight 机器档位只有 `full`、`standard`、`offline`；能力损失只能降低 coverage，不能改变证据规则。随后用 `python -m scripts.host_workflow next --workspace … --session …` 读取下一批任务；`status` 由该命令的 JSON 返回。退出码 `2` 表示输入、提取或证据无效且最后 checkpoint 已保留，退出码 `3` 表示可选能力不可用。

完成标准：会话处于 `query_plan_ready`，返回 session ID 与 typed next tasks，保存全部 degradation，不向用户暴露绝对路径或原始异常。

预检后按[研究恢复与准备版交付](references/research-recovery.md)实测已有取证能力。`browse` 包括可读原文的宿主工具与安全HTTP读取，不等于Chrome；只有 `browse` 也可从受信官方入口开展 `standard` 研究。没有某个搜索API或PDF库时先换已有工具，不把用户修环境当作继续条件。

## 研究循环

**默认使用快速规划的一轮研究与 `brief`。** 首轮达到约8分钟、24次原文读取预算或已有足够定位与代表校时就形成正文；不等待72个任务全部处理，不为同一缺口循环自证。已确认的兴趣、选科、语种与预算可用于规划判断，推估明确标为参考。下面的 `next → ingest → finish` 是用户要求深入核验时的流程，未做的深度任务保持pending，不能伪报unavailable。

### 可选深度核验

用 `next` 返回的 typed `QueryTask` 按[检索流程](references/retrieval-playbook.md)逐项搜索、打开并保存公开材料。宿主写入 `references/host-workflow.md` 定义的 submission 后，运行 `python -m scripts.host_workflow ingest --workspace … --session … --task … --submission …`，即调用 `ingest`；无法完成则运行同一门面的 `unavailable` 并给出真实 reason。每次之后反复调用 `next` 循环，直到没有 pending task。门面拥有 journal、evidence bundle、completed outcomes 和恢复上下文；Agent 只准备公开材料及其提取配置，不手工拼接 receipt、digest 或 journal JSON。

每个任务保持 `ProvinceConfig.mode`、规范化 `subject_group`、`required_extraction_fields`、`availability`、`freshness` 和有界 `max_candidates`，不得另设固定 Top-N。搜索仅发现候选；必须打开原网页或附件，不能把搜索摘要当事实。按实际格式选择 HTML、XLS/XLSX、PDF、OCR 或 QR adapter 提取，下载只走 secure downloader，并保存 year、method、locator、source provenance、coverage 与 warnings。

所有年度数据按 `Y → Y-1 → Y-2 → Y-3` 查询。最新年度没有、缺失或未公布时依次逐年回查，最多向前查三年；每种数据类型独立选择最近可比年份，不能因一项缺失停止整份规划。至少覆盖一分一段表、投档位次、招生计划、招生章程、学费、选科要求、多元路径政策、服务期与违约条款。当前年度只有第三方资料而上一年度有官方资料时同时保留：前者标当年参考，后者标历史基线；制度或口径变化导致不可比时停止数值聚合并说明原因。

普通批投档只经 `scripts.adapters.admission_bridge` 组合 exact adapter row、对应 `QueryTask`、`ValidatedAdmissionRow` 和 extraction coverage，整行绑定委托 `admission_row_hash`，`coverage_status` 与 evidence status 分开。位次与路径分别使用 typed rank/pathway bridge；public prose 使用 quote/span 绑定的 `public_text` adapter，省略字段保持 missing。不得手工拼事实或虚构 `province.json`。

门面的 `ingest` 在同一宿主进程内执行 bridge→receipt→ingest：它用 exact adapter 产生的 typed bridge 调用 `scripts.planning_session.build_task_evidence_outcome(profile, query_plan, task, bridges)`，再以 `session.ingest_task(..., evidence_outcome=task_evidence_outcome)` 和内部 checkpoint 完成该任务。这里的 `evidence_outcome=`、factory-only receipt、完整 origin replay 和裸 digest 无授权能力是实现不变量；Agent 不调用这些底层步骤。

offline 仅消费用户提供或本会话先前保存且已认证的本地材料，不声称当前或实时验证；没有静默联网回退。每个 task 最终必须 `ingest` 为 completed，或用受控 unavailable reason 结束。

查询矩阵不是“所有年份全部抓取成功”的承诺。先核验各族最新年份；读取 `research_summary` 与 `older_year_resolution`，对已获合格较新证据的同族历史任务，按门面提示显式记录 `newer_comparable_year_accepted`。浏览器分支失败不影响其他读取方式，某格式失败不影响其他任务；同一确定故障在本轮只做初次尝试和至多一次重试，不能为每个任务重复启动失效工具。

完成标准：`next` 不再返回查询任务，每个 task 恰有一个可重放 outcome。

## 证据最终化

本节仅用于深度 `finish`。默认 `brief` 使用实际读取的公共原文与精确引用检查，输出 `planning_reference`，不冒称完成本节认证；公开原文足够支持参考学校或路径时不等待全部字段齐全。

按[信源规范](references/source-policy.md)执行发布者分级、独立性、去重、采纳和冲突处理；冲突不得取平均或挑选方便值。A 级原始来源可形成 `official`；没有 A 时，两个独立 B 一致可形成 `corroborated`，三个独立 C 一致可形成 `reference`。官方来源缺失、不可得或未找到时仍继续检索 B/C；未达到门槛的单源第三方只能作为发现线索或“观察”理由。

所有任务 completed 或 unavailable 后运行 `python -m scripts.host_workflow finish --workspace … --session … --format markdown`。门面先完成 `python scripts/validate_data.py` / `python scripts/validate_evidence.py` 对应的验证语义，再在内部调用 `scripts.planning_session.build_evidence_manifest_outcome(...)` 和 `session.finalize_evidence(evidence_outcome, ...)` 跨越 `finalize` gate并保存 checkpoint。形成 authenticated snapshot 之前不得给出数字或开始计算；未达到采纳门槛时保留 `partial`、`conflict` 或 `missing`，不降低门槛。manifest 裸 digest 不具有效力。

已确认画像或 canonical QueryPlan 中处于 `include` / `discover` 的路径不得因政策 `missing`、`masked`、`partial` 或 `conflict` 从报告消失。通过 typed pathway observation 保留“观察 + 待核验”：只携带真实存在的来源编号与原证据状态，明确缺口及政策/资格核验动作；没有来源时来源编号保持为空。不得为观察项编造政策内容、院校、资格结论、目标位次、政策年份、报名时间线或来源编号。相同计划目标已有可重放的 accepted policy 时，由原正式推荐逻辑替代观察项。

完成标准：fresh evidence bundle 与研究快照已认证；依赖缺失事实的输出具有明确 unavailable reason。

所有任务均真实标为 unavailable、没有公开事实时，也正常执行 `finish`；空证据包的认证仅证明记录和缺口一致，不意味着学生已有认证的位次或资格结论。不得绕过门面凭经验补数。

## 计算发布

**默认执行 `brief`，最终交付是对话正文。** 按[固定对话结论](references/conversation-output.md)展示 `report_text`，保持下面七部分的顺序、数量及数值，不自行替换成工作日志、泛泛建议或技术审计。报告文件作为末尾附带下载，用户无需打开文件才看到结论。

1. **成绩定位**：本次考试XX/满分XX分 → 按YYYY年口径对应高考大致XX分 → 省排约XX位。中心值先给，紧接区间与一两句估算依据；暂缺校准时一次说明该缺口，然后继续学校与路径。
2. **本省普通批**：冲3所、稳4所、保5所，按用户已选专业方向匹配。每所写学校＋专业、匹配理由、历史门槛和主要限制。已知选科不符的剔除；可比材料不足的条件候选明确标记。只有数量真的不足时列明缺额，不拿重复或不符学校凑数。
3. **强基计划**：是什么、培养模式、本次投入判断；分数与兴趣相符时列冲1所、稳1所、保1所。不能把普通批线当强基入围线，没有适合的保档直接说明，不包装成保录。
4. **综合评价**：是什么、培养模式、本次投入判断；冲1所、稳1所、保1所，说明是历史入围参考还是普通批能力参照，以及个人需满足的条件。
5. **港澳升学**：是什么、培养模式、本次投入判断；冲1所、稳1所、保1所。分别考虑香港、澳门和具体项目的高考语种、校测、授课语言、费用，不因英语薄弱或选日语一刀切排除。
6. **其他路径**：结合画像说明专项、公费/定向、军警、艺体、中外合作等可行或有条件可行选择，解释培养、出口及限制；不把政策存在写成学生必然有资格。
7. **现阶段行动**：1–5项当前最需要做的事，按时间与价值排序，写动作、时间/触发点和完成标准。最后写“AI生成，仅供升学规划参考；不构成录取承诺，正式报考以当年官方要求为准。”

正文采用短段落、加粗校名、编号列表。每个学校和项目先给结论再给依据，来源链接紧贴数字或政策；只保留影响决定的限制。内部 source_id、digest、逐字段coverage、任务百分比留在附件，不让用户读自证过程。对于冲、稳、保、观察和主攻、重点准备、备选、观察、不建议，按实际来源、证据状态、覆盖范围和不确定性解释，不能随意升级。

`brief` 的固定模板为默认正文约束，主持Agent可作简短过渡，不改数值、数量、排序和资格状态，不再追加同样长的重复解释。无法运行代码时也严格照这七部分交付，并如实说明是宿主整理的参考，不能声称运行了引擎。

用户要求深度报告时，`finish` 继续内部 `compute` gate：调用 `scripts.planning_session.build_calculation_outcome(...)` 和 `scripts.planning_session.build_report_publication_outcome(...)`，保存校验结果并原子发布；调用方提供的 calculation/report 裸 digest 不具有效力。Markdown/DOCX及完整证据审计作为附件，主对话仍按上面七部分先呈现已有结论。深度报告保留原披露：“本结果由 AI 基于公开数据整理，仅供升学规划参考，不构成录取承诺或正式升学建议。政策、招生计划和录取结果请以当年主管部门及招生高校最终公布内容为准。”

完成标准：用户直接读到成绩定位、本省典型学校、三类重点路径和其他可行选择、现阶段行动。没有校准数字时仍给来源支持的目标梯度，不能退化为只讲不能判断；没有事实来源时明确相应缺额，也不编学校或资格。匿名输出；用户明确授权前，不发布、上传或push产物。

## 恢复与降级

选科以用户确认的真实组合为准：浙江 `3+3` 支持包含“技术”的七选三，不能替换为化学或删去科目来通过校验。选科合法与专业可报是两个判断；逐校逐专业核对当年认证要求，不得由“有物理”推断理工专业普遍可报，也不得由“没有化学/生物”笼统排除全部医学或生化方向。引擎报错时保留画像与已认证进度，继续可独立完成的资料核验；依赖失败步骤的数值和资格结论保持待核验，不用常识绕过引擎补算或承诺。

工具调用未杀死进程时用门面的 `next` 查看 stage；进程死亡或宿主重启后，用原 `--workspace` 与 `--session` 再次运行 `next`、`ingest`、`unavailable` 或 `finish`。门面的 `PlanningWorkflow.resume` 内部调用 `journal.load(session_id)`，以 `PlanningSessionReplayContext` 作为唯一恢复上下文，并由 context 方法完成 `finalize_evidence`、`calculate` 和 `publish`。`status` 快照、session digest、manifest digest 或调用方重建的 JSON 都不能恢复 completed receipt，也不能替代 factory replay。

宿主在发布完成前保留同一私有 journal 与 validated evidence bundle；journal 加载失败时停止依赖该 receipt 的推进并给出受控 degradation，不从头重跑已认证步骤，不重新向用户索取信息，不得让用户提供内部 JSON、本地路径或文件路径。

联网不可用时仍基于保留的可读材料给出参考或 partial 版本：位次区间使用可比历史锚点，典型学校和路径可作为来源支持的条件目标，标记年份、参考状态和缺口。普通批数据可用而路径政策不足时先给普通批；反之亦然。零证据时保留所有画像和待核验路径，合并重复缺口，在正文交付具体准备建议及复盘节点。任何失败都转为受控 degradation 或 unavailable reason，不把内部路径、堆栈和学生身份写入回复。

估算与个人适配判断应当主动给出，并用历史依据或明确假设说明；事实与资格不能靠措辞升级：没有竞赛不等于不具备强基/综评报名资格，日语或英语薄弱不等于所有港澳/中外合作项目不可行。分别核验项目类别、选科、语种、校测与授课要求；历史政策不能承诺未来申请年的要求或日期。

完成标准：会话可从最后一个有效快照继续；所有未完成内容、缺口与降级对用户可见。
