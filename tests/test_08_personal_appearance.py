"""Each person chooses their own theme."""
from helpers import *  # noqa: F401,F403  (sets up the test app first)


class PersonalAppearanceTests(unittest.TestCase):

    def test_each_person_chooses_their_own_look(self):
        owner = new_family('looks.owner@example.com', 'Looks Family')
        owner.post('/api/workspace/invites', json={'email': 'looks.kid@example.com'})
        kid = sign_in('looks.kid@example.com')

        # A member can change their own theme, even though they can't change family settings.
        self.assertEqual(kid.put('/api/me/appearance', json={'theme_mode': 'dark', 'accent_color': 'rose'}).status_code, 200)
        self.assertEqual(kid.get('/api/settings').json['settings']['theme_mode'], 'dark')
        self.assertIn('data-theme-mode="dark"', kid.get('/inventory').get_data(as_text=True))
        self.assertIn('data-accent="rose"', kid.get('/inventory').get_data(as_text=True))

        # ...and it doesn't change anyone else's.
        self.assertEqual(owner.get('/api/settings').json['settings']['theme_mode'], 'system')
        self.assertIn('data-theme-mode="system"', owner.get('/inventory').get_data(as_text=True))

        self.assertEqual(kid.put('/api/me/appearance', json={'theme_mode': 'purple'}).status_code, 400)
        self.assertEqual(kid.put('/api/me/appearance', json={'reduced_motion': True}).json['appearance']['reduced_motion'], '1')

    def test_people_who_never_chose_keep_the_familys_old_look(self):
        owner = new_family('looks.legacy@example.com', 'Legacy Looks Family')
        workspace_id = owner.get('/api/auth/me').json['workspace']['id']
        database.set_setting_value(workspace_id, 'theme_mode', 'dark')  # shared setting from before
        self.assertEqual(owner.get('/api/settings').json['settings']['theme_mode'], 'dark')
        owner.put('/api/me/appearance', json={'theme_mode': 'light'})
        self.assertEqual(owner.get('/api/settings').json['settings']['theme_mode'], 'light')


if __name__ == '__main__':
    unittest.main(verbosity=2)
