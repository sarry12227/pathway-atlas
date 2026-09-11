# 默认快速参考规划

画像确认后读取本页。已有session直接续用；已有导出的v3画像使用 `start --profile`，有1–20归一化答案使用 `start --answers`。默认目标是先交付可用的升学范围和典型路径，不等待完整研究矩阵。`brief` 与深度 `finish` 使用同一画像，前者输出规划参考，后者保留原有完整证据认证。`start`只初始化；只有 `brief` 返回 `report_generated=true` 与非空 `report_text` 才是报告已生成，之后还须在对话发出完整正文。

## 一轮研究，先形成结论

1. 沿用确认的学校、年级、校排、人数、最好/常态表现、选科、外语、预算和专业。用户已有回答不重问。
2. **主路线是网上喜报**：[喜报估算](school-report-estimation.md)涵盖学校发布、第三方文章和抖音分享的分数段人数、上线率、层次人数。可读一份即可作注明出处的参考，不套深度三来源门槛，不要求现成“校排—省排”表。本校不足按往年、明确可比的同城学校回退；同时读本省最近完整一分一段表，将喜报分数/人数转成校位/省位边界。
3. 用估算位次筛本省、已选专业的代表校，目标冲3/稳4/保5。学校名称去重，专业组/专业优先于整校最低线；已知选科不符的专业剔除。地域、语种、费用等限制分别比较；未核实的条件可保留为条件候选，已明确不符的剔除。
4. 对强基、综评、港澳各找冲1/稳1/保1的典型项目：先解释是什么、怎样培养，再给适配建议。强基若学科兴趣或成绩明显不匹配，明确暂不优先并说明要达到什么条件；不编出低门槛“保底强基”。其他路径只展开与画像相关的可行或有条件可行方向。
5. 构造本页的来源绑定输入，运行 `brief`，按返回 `report_text` 在对话中交付。数字和学校排序由程序生成，不由Agent随意改写。

默认首轮以**最多24次原文读取、约8分钟研究**作为交付预算；同一失败动作最多重试一次，某个工具确定不可用后本轮换路线，不在每个任务上重复失败。来源选择、搜索回退、喜报粗估和继续生成是Agent已被委托的工作，不发“是否继续／是否先找官方／请提供校排换算表”的选择题。达到预算或定位与代表校足够时，立即执行 `brief` 并交付已有结果；候选缺额写出数量和原因，不为填满名单无限补洞。未研究的深度任务保持pending，不批量伪报unavailable。用户明确要求更深核验时，再按原研究流程继续。

读页预算是首轮服务节奏，不是事实充分性的认证。主要数字、校名、专业、校级培养及门槛必须来自本次实际打开的正文或保留的可读历史材料；搜索摘要只用于发现。一个真实来源可以同时支持多个字段，不另造重复证据链。技术日志、哈希和矩阵放在内部。

## 分数和位次怎么给

- 有正式高考位次：直接使用用户确认的当年位次，按一分一段查对应分数。
- 有校排与喜报：优先用 `school_report` 将累计人数、上线率、层次人数转换为锚点；支持单锚点粗估与有界外推，见喜报估算。若已有直接校排—省排映射也可用 `school_rank`。得到省排中心后，用最好/常态校排与明确的规划浮动形成范围，再按一分一段反查高考分数。先写“本次XX分，按YYYY年口径对应高考大致XX分、省排约XX位”，再用一两句交代依据和范围。高一、高二也做这一步，不以“还没高考”为由放弃。
- `school_rank` 的默认位次浮动为20%，可在10%–50%内明确调整。`school_report` 可在10%–80%内选择规划浮动，单锚点至少50%，代理、人数不全或有限外推至少40%；这是敏感性假设，**不是经回测的置信区间**，不制造自动提分。原始人数不改写，模型推导与外推必须披露。
- 当前无可比锚点时，保留“本次分数”，将个人折算标为暂缺校准；继续用真实学校/项目门槛给**目标梯度**，明确条件，不把目标梯度冒充个人录取把握。只说明这一个定位缺口，不再用“谁给学校名单谁在瞎说”等措辞否定规划价值。也不把校考600分直接等同高考600分。
- 当前研究年未公布的分数表/录取数据可逐年回查至前三年。逐条保留基准年，未来高考年不填成已有官方数据。

## Agent内部输入

运行：

```text
python -m scripts.host_workflow brief --workspace <private-workspace> --session <session-id> --submission <private-workspace>/brief.json
```

所有JSON、目录、原文由Agent准备；用户不填写。输入结构为：

| 字段 | 内容 |
| --- | --- |
| `schema_version` | `1.0` |
| `profile_digest` | 当前已确认画像的digest，不能复制旧画像的值 |
| `province`, `subject_mode`, `research_year` | 同一会话的省份、模式、查询年 |
| `selection` | 当前画像的三门实际选科数组，浙江技术保持原样 |
| `sources` | 每项含`source_id, url, title, year, text`；text为实际读取并保留的公开正文，年份为该项材料适用年 |
| `calibration` | 喜报用`school_report`及喜报估算中的结构；已有直接锚点、正式成绩及无校准输入见下方；不直接填一个自编的省排中心 |
| `score_table` | `{year, subject_group, rows: [{score, rank, citation}, ...]}`，3+3的group为`综合`，3+1+2为画像首选科目；rank为累计位次。覆盖拟给中心及边界，缺少分数行时只给该段保守分数参照，不声称精确分数 |
| `historical_score_tables` | 喜报适用年份不同于最终参考表时，补入同结构的历史表数组，每年一份；同年喜报分数先在同年表转换 |
| `candidates` | 下方代表校记录数组，不要求每校所有资料完美才能进入候选 |
| `pathway_decisions` | `strong_foundation, comprehensive_evaluation, hong_kong_macao`各一句投入判断，含本人的匹配或限制；政策存在不等于资格已确认 |
| `other_pathways` | `{name, description, year, citation, fit_reason}`数组，描述来源绑定，本人适配理由由画像推理；没有支持项目则空数组 |
| `actions` | 1–5项现阶段具体动作，写时间/触发点、动作和完成标准；优先级从高到低 |

已有直接映射时，校排校准为`{method: "school_rank", school, year, comparability: "same_school", basis, margin_fraction: 0.2, anchors: [{school_rank, province_rank, citation}, ...]}`。这个旧接口需要两个直接映射点，**不代表喜报也必须有两个现成映射点**，常见喜报应走`school_report`。school必须与画像高中一致。同城代理使用`regional_comparable`，补`comparability_reason`说明客观可比依据，正文披露为代理估算。正式高考位次为`{method: "official_rank", basis}`，只从画像读取实际位次；有当年正式高考分数但无位次用`official_score`，要求当年表含该分数的精确行。无锚点为`{method: "unavailable", basis}`；这不妨碍生成其余版块。

`citation`为`{source_id, quote}`；quote是原文中的唯一精确摘录，含对应数字/校名/专业等原值，程序验证。重复摘录须加`start,end`定位。不同字段来自不同页面时用记录的`citations`映射分别提供`school, major, threshold_rank, required_subjects, cultivation, selection`的citation；未单独提供的字段使用公共`citation`。政策年份与分数年份不同的材料应拆成不同年度的候选，不能混成一项当年数据。

代表校记录：

| 字段 | 内容 |
| --- | --- |
| `kind` | `ordinary` / `strong_foundation` / `comprehensive_evaluation` / `hong_kong_macao` |
| `school`, `major` | 来源中的学校和具体专业/项目，不自造简称或专业 |
| `province`, `location_province`, `year` | 招生面向省、学校所在地、门槛基准年；普通批默认学校所在地为用户本省 |
| `matches_preferences` | 对应画像`target_majors`中已选的大类/方向，可语义匹配但列出原偏好名称；无明确偏好才允许空数组 |
| `threshold_rank` | 对应专业录取或项目入围参考位次；无数字则null |
| `threshold_score` | 原投档表只有分数时填写，并提供`citations.threshold_score`；程序用对应年份`score_table`或`historical_score_tables`的精确分数行换算位次，不要求人工另造位次列 |
| `threshold_basis` | `admission`专业录取 / `shortlist`项目入围 / `planning_benchmark`普通批能力参照 / `unavailable` |
| `required_subjects` | 原文明确“均须选考”的科目列表；不限制为空数组，未查到为null。择一要求先由Agent按真实组合核对，不误写成全部必选 |
| `fit` | `matched`条件已比对 / `conditional`仍有条件待复核 / `blocked`已明确不符；缺科目或blocked被剔除 |
| `fit_reason`, `cautions` | 本人匹配理由和影响决策的限制各一句，联系专业、语言、预算和准备；不填保证录取、稳拿满分等承诺 |
| `cultivation`, `selection` | 简短的原文培养/选拔描述；可为null，先展示该类路径一般介绍，具体校级细则待核验 |
| `citation`或`citations` | 支持实际学校/专业/数字/科目和已填写培养选拔字段的原文定位 |
| `benchmark_tier` | 仅在个人位次或项目门槛暂缺时使用`冲/稳/保`描述目标梯度；正文自动标明未判断个人录取把握 |

有个人位次时：门槛在乐观边界以内且好于中心为冲，中心至保守边界为稳，保守边界以外为保；不满足乐观边界的超远目标不挤占推荐数量。按门槛距离选择典型校，每类学校去重。此规则是规划分组，不是录取概率模型。强基/综评的普通批参照必须用`planning_benchmark`，正文明确不能代表该项目入围线；港澳同样不能用普通批线伪装实际独立招生线。

`brief`输出`report_text, positioning, ordinary, pathways, sources, delivery`及附带`report`路径，模式固定`planning_reference`；`authenticated_research=false`明确不是深度认证。来源摘录与结果保存在私有工作区以便复查，原journal与研究任务不变。交付时按[固定正文](conversation-output.md)直接使用文本。
