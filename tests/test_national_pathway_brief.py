"""Synthetic behavior tests for nationwide pathway comparison and output order."""
from copy import deepcopy
import unittest

from scripts.planning_brief import build_planning_brief
from tests.test_planning_brief import brief_fixture


class NationalPathwayBriefTest(unittest.TestCase):
    def test_pathway_soft_major_mismatch_keeps_national_choices(self):
        profile, payload = brief_fixture()
        for item in payload['candidates']:
            if item['kind'] != 'ordinary':
                item['location_province'] = '陕西'
                item['matches_preferences'] = []
        result = build_planning_brief(profile, payload, research_year=2026)
        for tiers in result['pathways'].values():
            self.assertEqual([len(tiers[t]) for t in ('冲', '稳', '保')], [1, 1, 1])
            self.assertTrue(all(item['fit'] == 'conditional' for items in tiers.values() for item in items))
        payload['candidates'][0]['matches_preferences'] = []
        result = build_planning_brief(profile, payload, research_year=2026)
        self.assertEqual(len(result['ordinary']['冲']), 2)

    def test_all_pathway_choices_precede_score_assessment_and_cautions(self):
        profile, payload = brief_fixture()
        result = build_planning_brief(profile, payload, research_year=2026)
        section = result['report_text'].split('## 三、')[1].split('## 四、')[0]
        choices_end = section.index(result['pathways']['strong_foundation']['保'][0]['school'])
        self.assertLess(choices_end, section.index('分数判断'))
        self.assertLess(section.index('分数判断'), section.index('是什么'))
        self.assertLess(section.index('分数判断'), section.index('语种、费用、体检'))

    def test_hong_kong_priority_and_same_tier_alternative(self):
        profile, payload = brief_fixture()
        original = next(c for c in payload['candidates'] if c['kind'] == 'hong_kong_macao')
        original['location_province'] = '澳门'
        hk = deepcopy(original)
        hk['school'] = '合成香港备选大学'
        hk['location_province'] = '香港'
        quote = original['citation']['quote'].replace(original['school'], hk['school'])
        hk['citation'] = {'source_id': 'synthetic', 'quote': quote}
        payload['sources'][0]['text'] += '\n' + quote
        payload['candidates'].append(hk)
        result = build_planning_brief(profile, payload, research_year=2026)
        self.assertEqual(result['pathways']['hong_kong_macao']['冲'][0]['school'], hk['school'])
        self.assertEqual(result['pathway_alternatives']['hong_kong_macao']['冲'][0]['school'], original['school'])

    def test_ordinary_benchmark_uses_reasoned_pathway_tier(self):
        profile, payload = brief_fixture()
        item = next(c for c in payload['candidates'] if c['kind'] == 'strong_foundation')
        item.update(threshold_basis='planning_benchmark', benchmark_tier='稳',
                    tier_reason='普通批仅作能力参照；综合项目难度与当前准备条件列为稳档目标')
        result = build_planning_brief(profile, payload, research_year=2026)
        self.assertFalse(result['pathways']['strong_foundation']['冲'])
        self.assertIn(item['school'], [c['school'] for c in result['pathways']['strong_foundation']['稳']
                                     + result['pathway_alternatives']['strong_foundation']['稳']])
        self.assertIn(item['tier_reason'], result['report_text'])

    def test_confirmed_subject_or_eligibility_barriers_still_exclude(self):
        profile, payload = brief_fixture()
        candidates = [c for c in payload['candidates'] if c['kind'] == 'strong_foundation']
        candidates[0]['required_subjects'] = ['物理', '化学']
        candidates[1]['fit'] = 'blocked'
        result = build_planning_brief(profile, payload, research_year=2026)
        self.assertEqual([len(result['pathways']['strong_foundation'][t]) for t in ('冲', '稳', '保')], [0, 0, 1])

    def test_four_comprehensive_schools_are_all_visible_without_duplicate_primaries(self):
        profile, payload = brief_fixture()
        item = deepcopy(next(c for c in payload['candidates'] if c['kind'] == 'comprehensive_evaluation'))
        previous = item['school']
        item['school'] = '合成第四所综评大学'
        quote = item['citation']['quote'].replace(previous, item['school'])
        item['citation'] = {'source_id': 'synthetic', 'quote': quote}
        payload['sources'][0]['text'] += '\n' + quote
        payload['candidates'] += [item, deepcopy(item)]
        result = build_planning_brief(profile, payload, research_year=2026)
        primary = [c['school'] for group in result['pathways']['comprehensive_evaluation'].values() for c in group]
        backups = [c['school'] for group in result['pathway_alternatives']['comprehensive_evaluation'].values() for c in group]
        self.assertEqual(len(primary), 3)
        self.assertEqual(len(backups), 1)
        self.assertEqual(len(set(primary + backups)), 4)
        self.assertTrue(all(name in result['report_text'] for name in primary + backups))

    def test_source_bound_admissions_provinces_exclude_wrong_origin(self):
        profile, payload = brief_fixture()
        item = next(c for c in payload['candidates'] if c['kind'] == 'strong_foundation')
        item['admissions_provinces'] = ['广东', '福建']
        quote = '合成招生范围记录：广东、福建。'
        item['citations'] = {'admissions_provinces': {'source_id': 'synthetic', 'quote': quote}}
        payload['sources'][0]['text'] += '\n' + quote
        result = build_planning_brief(profile, payload, research_year=2026)
        self.assertFalse(result['pathways']['strong_foundation']['冲'])
        item['admissions_provinces'].append('浙江')
        with self.assertRaises(ValueError):
            build_planning_brief(profile, payload, research_year=2026)


if __name__ == '__main__':
    unittest.main()
