"""Private host intake coverage, separate from unknown-valued planning profiles."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


# One entry per independently answerable field, covering the original 20 topics.
_FIELDS = (
    ("province", 2, "孩子在哪个省份参加高考？"),
    ("grade", 4, "孩子目前读几年级？"),
    ("exam_year", 4, "孩子预计哪一年参加高考？"),
    ("city", 3, "孩子目前在哪个城市读高中？"),
    ("high_school", 3, "孩子就读哪所高中？请告诉我学校的完整名称。"),
    ("gender", 1, "孩子的性别是？也可以选择不便回答。"),
    ("class_level", 5, "孩子所在的是普通班、重点班、竞赛班，还是其他班型？"),
    ("subject_selection", 6, "孩子的完整选科组合是什么？"),
    ("score_basis", 6, "这次成绩中的选考科目采用原始分还是赋分？不清楚也可以。"),
    ("exam_score", 7, "孩子最近一次重要考试的总分是多少？"),
    ("exam_name", 7, "这次考试叫什么名称？"),
    ("exam_date", 7, "这次考试是什么时候进行的？"),
    ("exam_scope", 7, "这是校内考试，还是市级或多校联考？"),
    ("exam_max_score", 7, "这次考试总分满分是多少？"),
    ("exam_cutoffs", 7, "学校或联考公布过这次考试的划线吗？有的话可以发图片，也可以一条条告诉我。"),
    ("school_rank", 8, "孩子这次大约排年级第几名？不清楚具体名次可以直接说。"),
    ("rank_scope", 8, "这个排名是全校年级排名，还是班级或某一科类的排名？"),
    ("rank_cohort", 8, "这个排名对应的参考人数大约有多少？"),
    ("other_rank", 8, "这次还有市级或省级联考排名吗？没有或不清楚也可以。"),
    ("best_rank", 9, "孩子以往最好的一次年级排名大约是多少？"),
    ("best_rank_exam", 9, "这个最好排名对应哪一次考试？不清楚可跳过。"),
    ("usual_rank", 9, "正常发挥时，孩子的年级排名大概在什么范围？"),
    ("awards", 10, "孩子有哪些竞赛或比赛获奖经历？没有可答无。"),
    ("activities", 11, "孩子参加过哪些研学、夏令营、科创或社会实践活动？没有可答无。"),
    ("target_schools", 12, "孩子有特别想去的大学吗？没有明确目标也可以。"),
    ("target_school_reasons", 13, "选择这些大学主要看重什么？没有目标学校可答不适用。"),
    ("target_majors", 14, "孩子对哪些专业或大类感兴趣？暂时没方向也可以。"),
    ("target_major_reasons", 15, "想选这些专业主要是什么原因？还没选专业可答不适用。"),
    ("target_regions", 16, "希望孩子在哪些城市或地区读大学？"),
    ("excluded_regions", 16, "有没有不能接受的就读地区？"),
    ("future_plan", 17, "对孩子大学毕业后的方向，目前有什么想法？"),
    ("concerns", 18, "目前最担心孩子升学中的什么问题？"),
    ("desired_outcomes", 19, "这次最希望规划帮您解决什么问题？"),
    ("national_special", 20, "国家专项的资格，您了解孩子是否符合吗？不了解可以答待核验。"),
    ("local_special", 20, "地方专项的资格，您了解孩子是否符合吗？不了解可以答待核验。"),
    ("university_special", 20, "高校专项的资格，您了解孩子是否符合吗？不了解可以答待核验。"),
    ("strong_foundation", 20, "是否考虑强基计划？不了解可以选择之后帮您判断。"),
    ("comprehensive_evaluation", 20, "是否考虑综合评价招生？不了解可以选择之后帮您判断。"),
    ("free_teacher", 20, "是否考虑公费师范生？"),
    ("excellent_teacher", 20, "是否考虑优师计划？"),
    ("targeted_medical", 20, "是否考虑定向医学生？"),
    ("military", 20, "是否考虑军队院校？"),
    ("police", 20, "是否考虑公安院校？"),
    ("judicial", 20, "是否考虑司法类方向？"),
    ("fire_service", 20, "是否考虑消防类方向？"),
    ("navigation", 20, "是否考虑航海类方向？"),
    ("hong_kong_macao", 20, "是否考虑香港或澳门高校？"),
    ("joint_programs", 20, "是否考虑中外合作办学？"),
    ("overseas", 20, "是否考虑境外升学？"),
    ("arts", 20, "孩子有没有艺术方面的经历或升学方向？"),
    ("sports", 20, "孩子有没有体育方面的经历或升学方向？"),
    ("special_talent", 20, "孩子有没有竞赛或学科特长方向？"),
    ("household_region", 20, "孩子户籍大致在哪个省市？不用提供详细地址。"),
    ("school_registration", 20, "孩子学籍在哪个省市？"),
    ("urban_rural", 20, "户籍或生源的城乡属性，您了解大致情况吗？"),
    ("foreign_language", 20, "孩子高考外语选的是什么语种？"),
    ("language_level", 20, "这门外语目前大致是什么水平？"),
    ("medical_limits", 20, "有没有已知、可能影响报考的体检限制？不用提供病历。"),
    ("vision", 20, "视力方面有没有已知限制？"),
    ("color_vision", 20, "色觉方面有没有已知限制？"),
    ("political_review", 20, "政审方面有没有已知限制？"),
    ("physical_fitness", 20, "体能方面有没有已知限制？"),
    ("service_period", 20, "是否能接受毕业后约定的服务年限？"),
    ("targeted_employment", 20, "是否能接受定向就业？"),
    ("employment_region", 20, "是否能接受毕业后到指定地区工作？"),
    ("tuition_budget", 20, "可接受的学费大致是多少？请注明每年还是总预算。"),
    ("living_budget", 20, "可接受的生活费大致是多少？请注明每年还是总预算。"),
    ("private_colleges", 20, "是否接受民办院校？"),
    ("high_fee_majors", 20, "是否接受高收费专业？"),
    ("school_vs_major", 20, "学校和专业之间，更看重哪一个，还是希望平衡？"),
    ("adjustment", 20, "是否接受专业调剂？"),
    ("risk", 20, "选校更偏冲刺、均衡，还是稳妥？"),
)
REQUIRED_FIELDS = {key: {"topic": topic, "question": question} for key, topic, question in _FIELDS}


class IntakeGateError(ValueError):
    def __init__(self, progress):
        super().__init__("intake coverage and confirmation are required")
        self.progress = progress


def assess_intake(state, *, profile_digest=None):
    """Check declared coverage; the host must retain genuine user quotations."""
    if not isinstance(state, dict) or state.get("schema_version") != "1.0":
        raise ValueError("intake state must use schema_version 1.0")
    if set(state) - {"schema_version", "responses", "confirmation"}:
        raise ValueError("unexpected intake state fields")
    responses = state.get("responses")
    if not isinstance(responses, dict) or set(responses) - REQUIRED_FIELDS.keys():
        raise ValueError("invalid intake response fields")
    for response in responses.values():
        if (not isinstance(response, dict) or set(response) != {"status", "quote"}
                or response["status"] not in {"answered", "unknown", "skipped"}
                or not isinstance(response["quote"], str) or not response["quote"].strip()
                or len(response["quote"]) > 4000):
            raise ValueError("resolved intake fields require an explicit user response quote")
    missing = [key for key in REQUIRED_FIELDS if key not in responses]
    digest = hashlib.sha256(json.dumps(responses, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")).encode("utf-8")).hexdigest()
    confirmation = state.get("confirmation")
    confirmed = (not missing and isinstance(profile_digest, str) and bool(profile_digest)
                 and isinstance(confirmation, dict)
                 and set(confirmation) == {"profile_digest", "progress_digest", "quote"}
                 and confirmation["profile_digest"] == profile_digest
                 and confirmation["progress_digest"] == digest
                 and isinstance(confirmation["quote"], str) and bool(confirmation["quote"].strip()))
    key = missing[0] if missing else None
    return {"phase": "collecting" if missing else "ready" if confirmed else "confirming",
            "ready_for_planning": bool(confirmed), "progress_digest": digest,
            "missing_fields": missing, "next_field": key,
            "next_question": REQUIRED_FIELDS[key]["question"] if key else
                None if confirmed else "请确认以上孩子的情况是否准确，有需要修改的地方吗？"}


def require_confirmed_intake(state, profile_digest):
    progress = assess_intake(state, profile_digest=profile_digest)
    if not progress["ready_for_planning"]:
        raise IntakeGateError(progress)
    return progress


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, help="Existing private intake state; omit for an empty intake")
    parser.add_argument("--profile-digest")
    parser.add_argument("--fields", action="store_true", help="Read the internal field catalogue")
    args = parser.parse_args()
    if args.fields:
        print(json.dumps(REQUIRED_FIELDS, ensure_ascii=True))
        return
    state = json.loads(args.state.read_text(encoding="utf-8-sig")) if args.state else {"schema_version": "1.0", "responses": {}}
    print(json.dumps(assess_intake(state, profile_digest=args.profile_digest), ensure_ascii=True))


if __name__ == "__main__":
    main()
