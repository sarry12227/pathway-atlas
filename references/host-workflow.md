# Host workflow command guide

This is the default host path after the family confirms the complete anonymous
profile. Every command, JSON file, saved source and returned path stays inside a
host-owned private workspace. The family sees questions, confirmation, progress,
evidence limits and the final report; it never authors or locates these inputs.

## Command loop

Run the Python module commands with the installed Skill root as the command's
working directory. Keep all session files in the separate private workspace.

Create a private workspace and a UTF-8 normalized-answer file, then start once:

```text
python -m scripts.host_workflow start --workspace <private-workspace> --answers <private-workspace>/answers.json --confirmed [--host-capability search] [--host-capability browse] [--host-capability vision]
```

Read `session_id` and `next` from stdout JSON. Keep both internal. Resume and get
the current bounded task list with:

```text
python -m scripts.host_workflow next --workspace <private-workspace> --session <session-id> --limit 3
```

Every non-`finish` command returns the same status shape: `pending` is the total
number of unfinished tasks, while `next` contains only the requested display
slice. The default limit is 3 and the accepted range is 1 through 100. Hidden
pending tasks remain in the journal. The slice is ordered by newest year first,
then kind and task ID. There is no separate `status` command.

For one returned task, search only within its declared kind, year, province,
subject context and candidate limit. Open each retained public page or attachment,
save it in the private workspace, write one submission file, then run:

```text
python -m scripts.host_workflow ingest --workspace <private-workspace> --session <session-id> --task <task-id> --submission <private-workspace>/submission.json
```

When the bounded search cannot produce an admissible extraction, record the real
reason and continue:

```text
python -m scripts.host_workflow unavailable --workspace <private-workspace> --session <session-id> --task <task-id> [--task <task-id>] --reason <stable-reason>
```

After a newer comparable task in the same family has completed, close an older
pending task only with the completed task's receipt-backed ID:

```text
python -m scripts.host_workflow unavailable --workspace <private-workspace> --session <session-id> --task <older-task-id> --reason newer_comparable_year_accepted --newer-task <completed-task-id>
```

The facade loads that typed receipt from the journal and lets the state machine
verify its family and year. Do not pass `--newer-task` for another reason. The
facade never skips older tasks automatically.

Repeat `next` after every `ingest` or `unavailable`. When `pending` is zero,
publish through the same facade:

```text
python -m scripts.host_workflow finish --workspace <private-workspace> --session <session-id> --format markdown
python -m scripts.host_workflow finish --workspace <private-workspace> --session <session-id> --format docx
```

Exit `0` returns JSON status or the report path. Exit `2` keeps the last checkpoint
after invalid input, extraction, evidence or filesystem state. Exit `3` means an
optional format or adapter capability is unavailable; retain the Markdown path or
record the affected task unavailable. Re-run any command with the same workspace
and session after process loss; the facade replays its journal and typed receipts.

`finish` also returns `sources`: the original public URL, publisher, tier,
publication date and retrieval date for each authenticated `source_id`. Use
those links in the family's final explanation alongside the matching reported
claims. The report's source IDs are identifiers, not sufficient citations by
themselves; do not ask the family to open the internal evidence bundle.

## Complete normalized twenty-answer file

The host collects answers through the one-question-at-a-time conversation in
SKILL.md, saving the draft and next pending item privately after each user reply.
Only after every topic and subitem is answered or explicitly skipped does the
host normalize the accumulated explicit meanings under keys `1` through `20`.
A complete numbered document volunteered by the user can optionally be segmented
with `parse_numbered_questionnaire`; conversational replies need no numbering.
Explicitly unknown or skipped values stay `null`, `unknown` or empty, while
unasked items remain pending. Confirm the resulting profile before `start`.
This valid sample shows the complete metadata shape, not values to copy into a
different student's profile:

```json
{
  "1": "不便回答",
  "2": "湖北",
  "3": {"city": "武汉", "high_school": "武汉市示例中学"},
  "4": {"grade": "高二", "exam_year": 2028},
  "5": "重点班",
  "6": {"mode": "3+1+2", "primary": "历史", "secondary": ["政治", "地理"], "score_basis": "原始分"},
  "7": {"date": "2026-06-01", "scope": "school", "score": 610, "max_score": 750, "source": "school_report"},
  "8": {
    "rank": 120,
    "cohort_size": 1000,
    "additional_observations": [
      {"scope": "province_joint", "rank": 18200, "cohort_size": 210000, "source": "joint_exam_report"}
    ]
  },
  "9": {"best_rank": 80, "usual_rank": 140},
  "10": {"subject_strengths": [], "awards": ["示例学科奖项"]},
  "11": {"research_experiences": [], "activities": ["示例志愿活动"]},
  "12": ["示例甲大学"],
  "13": ["专业实力强"],
  "14": ["历史学"],
  "15": ["自己感兴趣"],
  "16": {"targets": ["武汉"], "excluded": []},
  "17": "postgraduate",
  "18": ["院校定位", "多元路径"],
  "19": ["院校范围", "多元路径", "当前行动"],
  "20": {
    "budget": "moderate",
    "institution_types": ["public"],
    "service": "reject",
    "adjustment": "consider",
    "risk": "balanced",
    "health": [],
    "school_vs_major": "balanced",
    "pathways": {
      "strong_foundation": "interested",
      "comprehensive_evaluation": "interested",
      "special_program": "unknown",
      "service_oriented": "unknown",
      "uniformed_service": "not_applicable",
      "cross_border": "unknown",
      "arts_sports": "not_applicable"
    },
    "eligibility": ["完成高考报名"],
    "readiness": {"english_readiness": "unknown", "interview_readiness": "unknown", "physical_readiness": "unknown"}
  }
}
```

If question 8 has no explicit joint-exam observation, omit
`additional_observations`. Do not derive readiness, strengths or research from
subjects and activities. Unknown score/rank values may be `null`; the four
question-7/8 numeric values are omitted from `rank_observations` only when all are
null.

## Submission envelope and source metadata

Every submission has `sources`. Non-pathway tasks also have `records`; each
`rows` list selects one zero-based extracted row per source. Pathway tasks instead
use each source's `options.field_map`. The facade calculates `content_hash` from
the saved bytes, so omit it from host metadata unless independently computed and
exactly equal.

Each `candidate` contains all other `SourceCandidate` metadata:

```json
{
  "source_id": "stable-public-id",
  "url": "https://publisher.example.edu.cn/public-page",
  "publisher": "公开发布机构",
  "tier": "A",
  "published_at": "2026-06-25",
  "retrieved_at": "2026-09-06T08:00:00Z",
  "citation_root": "https://publisher.example.edu.cn/",
  "summary": "公开材料的简短匿名说明"
}
```

Use the source-policy tier derived from the actual publisher. `published_at` may
be `null`; `retrieved_at`, canonical URL, publisher and citation root remain
required provenance. One source ID always refers to the same saved material.

## HTML submission

This complete score-table submission selects the first data row. `columns` maps
canonical extraction fields to exact page headers; `roles` and `score_scale`
activate numeric validation.

```json
{
  "sources": [
    {
      "path": "<private-workspace>/sources/score-2026.html",
      "adapter": "html",
      "candidate": {
        "source_id": "score-table-2026",
        "url": "https://eea.example.gov.cn/score/2026.html",
        "publisher": "示例教育考试院",
        "tier": "A",
        "published_at": "2026-06-25",
        "retrieved_at": "2026-09-06T08:00:00Z",
        "citation_root": "https://eea.example.gov.cn/",
        "summary": "2026 一分一段公开表"
      },
      "options": {
        "table_index": 1,
        "caption": null,
        "columns": {"score": "分数", "cumulative_count": "累计人数"},
        "roles": {"score": "score", "cumulative_count": "rank"},
        "score_scale": [0, 750]
      }
    }
  ],
  "records": [{"rows": [0], "coverage_status": "official"}]
}
```

## XLSX submission

This complete admission-row submission maps exact worksheet headers. The facade
adds the task's canonical year, province, subject group and empty remarks before
building the validated admission row.

```json
{
  "sources": [
    {
      "path": "<private-workspace>/sources/admission-2026.xlsx",
      "adapter": "xlsx",
      "candidate": {
        "source_id": "admission-2026",
        "url": "https://eea.example.gov.cn/admission/2026.xlsx",
        "publisher": "示例教育考试院",
        "tier": "A",
        "published_at": "2026-07-20",
        "retrieved_at": "2026-09-06T08:00:00Z",
        "citation_root": "https://eea.example.gov.cn/",
        "summary": "2026 普通批投档公开表"
      },
      "options": {
        "sheet": "普通批",
        "columns": {
          "school_code": "院校代码",
          "school_name": "院校名称",
          "program_group": "专业组",
          "min_score": "最低分",
          "min_rank": "最低位次"
        },
        "roles": {"min_score": "score", "min_rank": "rank"},
        "score_scale": [0, 750]
      }
    }
  ],
  "records": [{"rows": [0], "coverage_status": "official"}]
}
```

## OCR-row submission

Use `ocr_rows` when the public source is an image or image-only PDF and the host
can produce the normalized OCR JSON accepted by `normalize_ocr_rows`. `path`
always names the downloaded original public image/PDF whose bytes bind the
candidate `content_hash`; `options.ocr_path` names the separate absolute,
host-owned normalized JSON. The facade fixes `min_exact_confidence` at 0.95 and
validates page/image/bbox cells plus spatial verification anchors. Low confidence,
cropped, partial-page, or invalid-anchor material cannot become exact evidence.

```json
{
  "sources": [
    {
      "path": "<private-workspace>/sources/score-2026.png",
      "adapter": "ocr_rows",
      "candidate": {
        "source_id": "score-image-2026",
        "url": "https://eea.example.gov.cn/score/2026.png",
        "publisher": "示例教育考试院",
        "tier": "A",
        "published_at": "2026-06-25",
        "retrieved_at": "2026-09-06T08:00:00Z",
        "citation_root": "https://eea.example.gov.cn/",
        "summary": "2026 一分一段公开图片"
      },
      "options": {
        "ocr_path": "<private-workspace>/normalized/score-2026.ocr.json",
        "columns": {"score": "分数", "cumulative_count": "累计人数"},
        "roles": {"score": "score", "cumulative_count": "rank"},
        "score_scale": [0, 750]
      }
    }
  ],
  "records": [{"rows": [0], "coverage_status": "official"}]
}
```

## Public-text submission

Use `public_text` for saved UTF-8 prose such as an HTML page's visible text.
Every non-null typed value has an exact supporting `quote`. A unique quote needs
no offsets; repeated prose requires zero-based `start` and exclusive `end`, and
the slice must equal the quote. Numeric values and list items must occur in the
quote. The facade reads with UTF-8-sig and normalizes CRLF and CR to LF before
binding fields, so compute every span against that normalized text. The candidate
hash still covers the original saved bytes. `field_map` maps pathway canonical
names to arbitrary observed names.

```json
{
  "sources": [
    {
      "path": "<private-workspace>/sources/pathway-2026.txt",
      "adapter": "public_text",
      "candidate": {
        "source_id": "pathway-policy-2026",
        "url": "https://university.example.edu.cn/admission/pathway-2026.html",
        "publisher": "示例大学招生办公室",
        "tier": "A",
        "published_at": "2026-04-10",
        "retrieved_at": "2026-09-06T08:00:00Z",
        "citation_root": "https://university.example.edu.cn/admission/",
        "summary": "2026 强基计划公开简章正文"
      },
      "options": {
        "fields": {
          "school": {"value": "示例大学", "quote": "招生学校：示例大学"},
          "region": {"value": "湖北", "quote": "面向湖北考生"},
          "mode": {"value": "3+1+2", "quote": "适用3+1+2模式"},
          "policy_year": {"value": 2026, "quote": "2026年强基计划"},
          "eligibility": {"value": ["完成高考报名"], "quote": "申请人须完成高考报名"},
          "majors": {"value": ["历史学", "哲学"], "quote": "招生专业为历史学、哲学"}
        },
        "field_map": {
          "institution": "school",
          "province": "region",
          "subject_mode": "mode",
          "year": "policy_year",
          "eligibility_requirements": "eligibility",
          "professional_options": "majors"
        }
      }
    }
  ]
}
```

Omit fields that the page does not state and omit their `field_map` entries. The
adapter writes them as source-bound missing values, so the pathway policy remains
partial. Use `{"value": null, "status": "uncertain", "quote": "..."}` only
when prose exists but cannot support an exact semantic value. Never write a quote
for absent material or turn a missing field into affirmative prose.

## Year fallback

Treat the year on each returned task as authoritative. For every data family,
try `Y`, then `Y-1`, `Y-2`, and `Y-3` only through the separate tasks returned by
`next`. If `Y` is unavailable, record that task with a reason such as
`current_year_not_published`, call `next`, and ingest the next comparable year
with its real `year` field or row. Once a newer comparable task is completed,
close each older pending task in that family with
`--reason newer_comparable_year_accepted --newer-task <completed-task-id>`.
The receipt proves the newer task; there is no caller-authored or automatic skip.

Each family falls back independently. Retain a current-year third-party source as
current reference and the previous-year official source as historical baseline
when both are useful. Stop numeric aggregation when policy or measurement changes
make years incomparable, and record the unavailable reason. The facade preserves
the selected task year, evidence status, source provenance and coverage in every
checkpoint.
