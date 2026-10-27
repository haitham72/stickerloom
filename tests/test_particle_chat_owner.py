"""Owner-first chat selection and scoped offers, entirely on FakeTools."""
import unittest
from tests.test_agent import Base


class ParticleChatOwner(Base):
    def setUp(self):
        super().setUp()
        self.seed()
        self.tools.pack_list = [{'id':'a','name':'Princess','count':9}]
        self.tools.owner_rows = [{'sticker_id':'s3','pack_id':'a','generation':'G012','index':3}]
        self.tools.sets = [
            {'id':'P001','name':'Old stars','created':1,'owner':[self.tools.owner_rows[0]],'elements':['stars']},
            {'id':'P002','name':'New hearts','created':2,'owner':[self.tools.owner_rows[0]],'elements':['hearts']},
            {'id':'P003','name':'Other bats','created':3,'owner':[],'elements':['bats']}]

    def test_burst_uses_stickers_newest_owned_set_before_unrelated_focus(self):
        s=self.sess();s['particles']={'set':'P003'};self.store.save(s)
        self.say('burst for sticker 3')
        self.assertIn(('particles_burst','P002','a'),self.tools.calls)

    def test_explicit_name_overrides_owned_set(self):
        self.say('render Other bats particles for sticker 3')
        self.assertIn(('particles_burst','P003','a'),self.tools.calls)

    def test_none_owned_asks_with_set_chips(self):
        self.tools.owner_rows=[]
        m=self.say('burst for sticker 3')
        self.assertEqual({c['label'] for c in m['chips'] if c['label'] in {'Old stars','New hearts','Other bats'}},{'Old stars','New hearts','Other bats'})
        self.assertFalse([c for c in self.tools.calls if c[0]=='particles_burst'])

    def test_unowned_batch_offers_approval(self):
        self.tools.owner_rows=[]
        m=self.say('make particles for G012')
        self.assertEqual(m['cards'][0],{'type':'particles_approve','generation':'G012'})
        self.assertFalse([c for c in self.tools.calls if c[0]=='particles_start'])

    def test_repeat_creation_reuses_newest_owned_set(self):
        m=self.say('make particles for sticker 3')
        self.assertEqual(m['cards'][0]['set'],'P002')
        self.say('yes')
        self.assertEqual([c[1] for c in self.tools.calls if c[0]=='particles_start'],['P002'])

    def test_link_to_next_sticker_is_free(self):
        s=self.sess();s['particles']={'set':'P003'};s['focus']['stickers']=['G012/S3'];self.store.save(s)
        self.say('use them for the next one')
        self.assertIn(('particles_link','P003',['s3']),self.tools.calls)
        self.assertFalse([c for c in self.tools.calls if c[0]=='particles_start'])

    def test_add_uses_same_selection_and_affirms(self):
        self.say('add particles for sticker 3 to the pack')
        self.assertIn(('particles_add','P002','a'),self.tools.calls)


if __name__=='__main__': unittest.main()


class ParticlesOnTheirOwn(Base):
    """"create particles for lipsticks and ribbons" asks which pack, or on their own; on their own makes a set no sticker owns."""

    def setUp(self):
        super().setUp()
        self.tools.pack_list = [{'id': 'a', 'name': 'Princess', 'count': 9}, {'id': 'b', 'name': 'Barbie', 'count': 4}]
        self.tools.owner_rows, self.tools.sets = [], []

    def spent(self):
        return [c for c in self.tools.calls if c[0] in ('particles_start', 'particles_alone')]

    def test_a_request_naming_particles_but_no_pack_asks_which_pack_or_on_their_own(self):
        m = self.say('create particles for lipsticks and ribbons')
        self.assertIn('which pack', m['text'].lower())
        labels = [c['label'] for c in m['chips']]
        self.assertIn('The last pack, Barbie', labels)
        self.assertIn('On their own', labels)
        self.assertEqual(self.spent(), [], 'a question spends nothing')

    def test_on_their_own_shows_the_plan_then_the_go_ahead_starts_it(self):
        m = self.say('particles on their own for lipsticks and ribbons')
        card = m['cards'][0]
        self.assertEqual((card['type'], card['op'], card['elements']), ('particles_plan', 'alone', ['lipstick', 'ribbon']))
        self.assertEqual(self.spent(), [], 'no sheet before the go-ahead')
        m = self.say('yes')
        (c,) = self.spent()
        self.assertEqual(c[0], 'particles_alone')
        self.assertEqual((m['cards'][0]['type'], m['cards'][0]['pack_id']), ('particles', None))

