"""Synthetic regressions for the owner's national opportunity hierarchy."""
from copy import deepcopy
import unittest

from scripts.planning_brief import build_planning_brief
from scripts.opportunity_order import align_qualitative_pathways, reference_position, sort_reference
from tests.test_planning_brief import brief_fixture


class OpportunityOrderTest(unittest.TestCase):
    def comprehensive_fixture(self):
        profile, payload = brief_fixture()
        template = next(c for c in payload['candidates'] if c['kind'] == 'comprehensive_evaluation')
        payload['candidates'] = [c for c in payload['candidates'] if c['kind'] != 'comprehensive_evaluation']
        for school, tier in [('昆山杜克大学','冲'), ('上海纽约大学','冲'), ('北京外国语大学','稳'), ('深圳北理莫斯科大学','保')]:
            c=deepcopy(template)
            quote=template['citation']['quote'].replace(template['school'], school)
            c.update(school=school, threshold_rank=None, threshold_basis='unavailable', benchmark_tier=tier,
                     tier_reason=f'原宿主将此校列为{tier}档', citation={'source_id':'synthetic','quote':quote})
            payload['sources'][0]['text']+='\n'+quote
            payload['candidates'].append(c)
        return profile,payload

    def test_unpositioned_comprehensive_order_is_not_host_insertion_order(self):
        profile,payload=self.comprehensive_fixture()
        result=build_planning_brief(profile,payload,research_year=2026)
        groups=result['pathways']['comprehensive_evaluation']
        self.assertEqual([groups[t][0]['school'] for t in ('冲','保')], ['上海纽约大学','深圳北理莫斯科大学'])
        self.assertEqual({groups['稳'][0]['school'],result['pathway_alternatives']['comprehensive_evaluation']['稳'][0]['school']},{'北京外国语大学','昆山杜克大学'})
        self.assertNotIn('原宿主将此校列为冲档',result['report_text'])

    def test_hubei_score_is_never_a_cross_province_level_converter(self):
        profile,payload=self.comprehensive_fixture()
        result=build_planning_brief(profile,payload,research_year=2026)
        altered=deepcopy(payload)
        altered.pop('score_table',None)
        again=build_planning_brief(profile,altered,research_year=2026)
        self.assertEqual(result['pathways'],again['pathways'])

    def test_same_group_and_campus_identity_are_retained(self):
        self.assertEqual(reference_position('comprehensive_evaluation','北外')[0],reference_position('comprehensive_evaluation','昆杜')[0])
        self.assertNotEqual(reference_position('strong_foundation','哈尔滨工业大学'), reference_position('strong_foundation','哈工大威海'))
        self.assertIsNone(reference_position('strong_foundation','哈尔滨工业大学（深圳）'))
        self.assertIsNone(reference_position('ordinary','合成未收录大学'))
        self.assertEqual(reference_position('ordinary','哈尔滨工业大学','威海校区计算机')[0],6)
        self.assertEqual(reference_position('ordinary','山东大学','威海校区数学')[0],6)
        self.assertEqual(reference_position('ordinary','厦门大学','马来西亚分校计算机')[0],7)
        self.assertIsNone(reference_position('ordinary','哈尔滨工业大学','深圳校区计算机'))

    def test_peers_keep_actual_threshold_proximity(self):
        items=[dict(school=s,major='合成项目',threshold_rank=rank,location_province='北京') for s,rank in [('昆山杜克大学',51000),('北京外国语大学',59000)]]
        sort_reference('comprehensive_evaluation',items,50000)
        self.assertEqual(items[0]['school'],'昆山杜克大学')

    def test_two_groups_keep_peers_together_and_mixed_threshold_does_not_disable_alignment(self):
        def item(s,personal=False):
            return dict(school=s,major='合成项目',personal_tier=personal,tier_reason='合成比较')
        tiers={'冲':[item('北京外国语大学')],'稳':[item('昆山杜克大学')],'保':[item('深圳北理莫斯科大学')]}
        align_qualitative_pathways('comprehensive_evaluation',tiers)
        self.assertEqual({i['school'] for i in tiers['稳']},{'北京外国语大学','昆山杜克大学'})
        fixed=item('合成真实门槛项目',True)
        tiers={'冲':[item('昆山杜克大学')],'稳':[item('上海纽约大学')],'保':[fixed]}
        align_qualitative_pathways('comprehensive_evaluation',tiers)
        self.assertEqual(tiers['冲'][0]['school'],'上海纽约大学')
        self.assertIs(tiers['保'][0],fixed)

    def test_hong_kong_preference_does_not_promote_lower_group_above_macao(self):
        items=[dict(school=s,major='合成项目',threshold_rank=None,location_province=location) for s,location in [('香港都会大学','香港'),('澳门大学','澳门'),('香港浸会大学','香港')]]
        sort_reference('hong_kong_macao',items,None)
        self.assertEqual([c['school'] for c in items],['香港浸会大学','澳门大学','香港都会大学'])

    def test_unlisted_school_keeps_its_slot_in_a_comparable_tier(self):
        items=[dict(school=s,major='合成项目',threshold_rank=None,location_province='北京') for s in ('昆山杜克大学','合成未收录大学','上海纽约大学')]
        sort_reference('comprehensive_evaluation',items,None)
        self.assertEqual([c['school'] for c in items],['上海纽约大学','合成未收录大学','昆山杜克大学'])


if __name__=='__main__': unittest.main()
