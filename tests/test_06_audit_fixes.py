"""Fixes from the full app audit."""
from helpers import *  # noqa: F401,F403  (sets up the test app first)


class AuditFixTests(unittest.TestCase):
    """Fixes from the full app audit."""

    def test_service_records_are_checked_and_can_be_edited_or_deleted(self):
        owner = new_family('servicer@example.com', 'Service Family')
        upload(owner)
        item_id = owner.get('/api/inventory').json['items'][0]['id']
        url = f'/api/inventory/{item_id}/service-history'
        for bad in ({'service_date': '2026-13-45', 'service_type': 'Repair'},
                    {'service_date': '2026-10-01', 'service_type': ''},
                    {'service_date': '2026-10-01', 'service_type': 'Repair', 'cost': 'lots'},
                    {'service_date': '2026-10-01', 'service_type': 'Repair', 'next_service_date': 'whenever'},
                    {'service_date': '2026-10-01', 'service_type': 'Repair', 'next_service_date': '2026-09-01'},
                    {'service_date': '2099-01-01', 'service_type': 'Repair'}):
            response = owner.post(url, json=bad)
            self.assertEqual(response.status_code, 400, bad)
            self.assertTrue(response.json['error'].endswith('.'), 'friendly sentence, not a field name')
        created = owner.post(url, json={'service_date': '2026-10-01', 'service_type': 'Repair', 'cost': 'Rs 1,500.00'})
        self.assertEqual(created.status_code, 200)
        entry = created.json['entries'][0]
        self.assertEqual(entry['cost'], '1500')

        edited = owner.patch(f"{url}/{entry['id']}", json={'service_date': '2026-10-02', 'service_type': 'Gas refill'})
        self.assertEqual(edited.json['entries'][0]['service_type'], 'Gas refill')
        outsider = new_family('service.outsider@example.com', 'Other Service Family')
        self.assertEqual(outsider.delete(f"{url}/{entry['id']}").status_code, 404)
        self.assertEqual(owner.delete(f"{url}/{entry['id']}").json['entries'], [])

    def test_item_amounts_and_dates_are_checked(self):
        owner = new_family('checker@example.com', 'Checker Family')
        upload(owner)
        item_id = owner.get('/api/inventory').json['items'][0]['id']
        self.assertEqual(owner.patch(f'/api/inventory/{item_id}', json={'total_amount': 'about seven hundred'}).status_code, 400)
        self.assertEqual(owner.patch(f'/api/inventory/{item_id}', json={'date_of_purchase': '2099-01-01'}).status_code, 400)
        owner.patch(f'/api/inventory/{item_id}', json={'total_amount': '24990.0'})
        self.assertEqual(owner.get(f'/api/inventory/{item_id}').json['item']['total_amount'], '24990')

    def test_unreadable_file_gives_a_friendly_error(self):
        from digital_twin.services import ai as llm_extractor
        result = llm_extractor.parse_receipt_image_to_json(b'not really a png', 'broken.png')
        self.assertNotIn('BytesIO', result['error'])
        self.assertIn("couldn't be opened", result['error'])

    def test_one_chat_can_be_deleted(self):
        owner = new_family('chatter@example.com', 'Chat Family')
        for conversation in ('keep', 'gone'):
            owner.post('/api/chat', json={'message': f'hello {conversation}', 'conversation_id': conversation})
        self.assertEqual(owner.delete('/api/chat/conversations/gone').status_code, 200)
        left = [c['conversation_id'] for c in owner.get('/api/chat/conversations').json['conversations']]
        self.assertEqual(left, ['keep'])
        other = new_family('chat.outsider@example.com', 'Other Chat Family')
        self.assertEqual(other.delete('/api/chat/conversations/keep').status_code, 404, "can't delete someone else's")

    def test_new_families_start_in_their_own_time_zone(self):
        client = sign_in('kl.family@example.com')
        client.post('/api/workspaces', json={'name': 'KL Family', 'timezone': 'Asia/Kuala_Lumpur'})
        self.assertEqual(client.get('/api/settings').json['settings']['timezone'], 'Asia/Kuala_Lumpur')

        legacy = new_family('utc.family@example.com', 'UTC Family')  # created without a time zone
        settings = legacy.get('/api/settings').json['settings']
        self.assertTrue(settings['timezone_is_default'])
        self.assertEqual(legacy.put('/api/settings/timezone', json={'timezone': 'Mars/Olympus'}).status_code, 400)
        self.assertEqual(legacy.put('/api/settings/timezone', json={'timezone': 'Asia/Kolkata'}).json['timezone'], 'Asia/Kolkata')
        # Once chosen, it isn't overwritten by another device.
        self.assertEqual(legacy.put('/api/settings/timezone', json={'timezone': 'Europe/London'}).json['timezone'], 'Asia/Kolkata')
        settings = legacy.get('/api/settings').json['settings']
        settings['timezone'] = 'Not/AZone'
        self.assertEqual(legacy.put('/api/settings', json=settings).status_code, 400)

    def test_email_again_shows_the_new_link_when_sending_fails(self):
        owner = new_family('resender@example.com', 'Resend Family')
        invite = owner.post('/api/workspace/invites', json={'email': 'aunt@example.com'}).json['invite']

        def refuse(*_args, **_kwargs):
            raise emails.EmailError('The email could not be sent.')
        emails.send_email = refuse
        try:
            response = owner.post(f"/api/workspace/invites/{invite['id']}/email")
        finally:
            emails.send_email = fake_send_email
        self.assertEqual(response.status_code, 502)
        self.assertIn('/join/', response.json['link'], 'the replacement link is still shown')
        self.assertTrue(response.json['expires_at'])

    def test_friendly_error_pages_and_strict_scripts(self):
        owner = new_family('pages@example.com', 'Pages Family')
        page = owner.get('/no-such-page')
        self.assertEqual(page.status_code, 404)
        self.assertIn('Page not found', page.get_data(as_text=True))
        api = owner.get('/api/no-such-thing')
        self.assertEqual(api.status_code, 404)
        self.assertIn('error', api.json)

        settings_page = owner.get('/settings')
        policy = settings_page.headers['Content-Security-Policy']
        self.assertNotIn("'unsafe-inline' https://cdn", policy.split('style-src')[0], 'scripts need the nonce')
        nonce = policy.split("'nonce-")[1].split("'")[0]
        html = settings_page.get_data(as_text=True)
        self.assertIn(f'<script nonce="{nonce}">', html)
        self.assertNotIn('onclick=', html)
        self.assertNotEqual(nonce, owner.get('/settings').headers['Content-Security-Policy'].split("'nonce-")[1].split("'")[0])

    def test_csv_export_includes_currencies(self):
        owner = new_family('csv.currency@example.com', 'CSV Currency Family')
        save(owner, {'receipt': {'invoice_number': 'CSV-1', 'currency': 'MYR'}, 'items': [{'item_name': 'Servo', 'amount': '6.5'}]})
        sheet = read_workbook(owner.get('/api/workspace/export.xlsx'))['Inventory']
        headers = [c.value for c in sheet[1]]
        self.assertIn('Currency', headers)
        self.assertIn('Amount in INR', headers)
        row = [c.value for c in sheet[2]]
        self.assertEqual(row[headers.index('Currency')], 'MYR')
        self.assertEqual(row[headers.index('Amount')], 6.5, 'a real number, not text')


if __name__ == '__main__':
    unittest.main(verbosity=2)
