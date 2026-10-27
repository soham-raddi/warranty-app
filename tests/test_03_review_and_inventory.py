"""Reviewing scans before saving, receipts with several products, re-reading cards, unsubscribing."""
from helpers import *  # noqa: F401,F403  (sets up the test app first)


class ReviewAndInventoryTests(unittest.TestCase):
    """Reviewing scans, receipts with several products, re-reading cards and unsubscribing."""

    def test_nothing_is_saved_until_the_review_is_confirmed(self):
        owner = new_family('reviewer@example.com', 'Review Family')
        scanned = scan(owner)
        self.assertEqual(scanned.status_code, 200)
        self.assertEqual(owner.get('/api/inventory').json['items'], [], 'scanning alone saves nothing')

        details = scanned.json
        details['items'][0].update({'item_name': 'Samsung Fridge', 'warranty_months': '24', 'brand': 'Samsung'})
        details['receipt']['store_name'] = 'Croma'
        saved = save(owner, details)
        self.assertEqual(saved.status_code, 201)
        item = owner.get('/api/inventory').json['items'][0]
        self.assertEqual((item['item_name'], item['brand'], item['store_name']), ('Samsung Fridge', 'Samsung', 'Croma'))
        self.assertEqual(item['warranty_months'], 24)

        again = save(owner, details)
        self.assertEqual(again.status_code, 409, 'saving the same receipt twice is refused')
        self.assertTrue(scan(owner).json['items'][0]['duplicate_item_id'] is None, 'new invoice number, not a duplicate')

    def test_review_rejects_bad_details(self):
        owner = new_family('badreview@example.com', 'Bad Review Family')
        details = scan(owner).json
        for change in ({'item_name': ''}, {'warranty_months': '999'}):
            broken = json.loads(json.dumps(details))
            broken['items'][0].update(change)
            self.assertEqual(save(owner, broken).status_code, 400, change)
        broken = json.loads(json.dumps(details))
        broken['receipt']['date_of_purchase'] = '31/12/2026'
        self.assertEqual(save(owner, broken).status_code, 400)
        self.assertEqual(save(owner, {'receipt': {}, 'items': []}).status_code, 400)
        self.assertEqual(owner.get('/api/inventory').json['items'], [])

    def test_receipt_with_several_products(self):
        owner = new_family('multi@example.com', 'Multi Family')
        details = {
            'receipt': {'store_name': 'Electronics Mart', 'invoice_number': 'EM-77', 'date_of_purchase': '2026-06-01',
                        'total_amount': '91000'},
            'items': [{'item_name': 'Television', 'amount': '60000', 'warranty_months': '12'},
                      {'item_name': 'Soundbar', 'amount': '25000'},
                      {'item_name': 'Wall mount', 'amount': '6000'}],
        }
        saved = save(owner, details)
        self.assertEqual(saved.status_code, 201)
        self.assertEqual(saved.json['saved'], 3)
        data = owner.get('/api/inventory').json
        self.assertEqual(sorted(i['item_name'] for i in data['items']), ['Soundbar', 'Television', 'Wall mount'])
        self.assertEqual(data['total_spent'], 91000, 'each item counts its own amount, not the receipt total')
        self.assertEqual({i['store_name'] for i in data['items']}, {'Electronics Mart'})

        # All three share one stored receipt, which is only deleted with the last of them.
        ids = [i['id'] for i in data['items']]
        self.assertEqual(owner.get(f'/files/receipt/{ids[0]}').status_code, 200)
        owner.delete(f'/api/delete/{ids[0]}')
        self.assertEqual(owner.get(f'/files/receipt/{ids[1]}').status_code, 200, 'still there for the others')
        owner.delete(f'/api/delete/{ids[1]}')
        owner.delete(f'/api/delete/{ids[2]}')
        self.assertEqual(len(os.listdir(os.path.join(TEST_DIR, 'uploads', str(owner.get('/api/auth/me').json['workspace']['id'])))), 0)

        # Saving the receipt again skips what is already there and adds only new items.
        save(owner, {**details, 'items': details['items'][:1]})
        again = save(owner, details)
        self.assertEqual(again.status_code, 201)
        self.assertEqual(again.json['saved'], 2)
        self.assertEqual(again.json['skipped'], ['Television'])

    def test_card_can_be_read_again(self):
        owner = new_family('reread@example.com', 'Reread Family')
        upload(owner)
        item_id = owner.get('/api/inventory').json['items'][0]['id']
        self.assertEqual(owner.post(f'/api/inventory/{item_id}/read-warranty-card').status_code, 404, 'no card yet')

        attach_card(owner, item_id)  # read fails in tests, like cards attached before cards were read
        self.assertIsNone(owner.get('/api/inventory').json['items'][0]['warranty_months'])
        receipts_routes.parse_warranty_card = lambda data, _f: (
            {'warranty_months': 36, 'warranty_start_date': None, 'coverage_notes': None} if data == b'fake card'
            else {'error': 'wrong file'})
        try:
            response = owner.post(f'/api/inventory/{item_id}/read-warranty-card')
        finally:
            receipts_routes.parse_warranty_card = _unreadable_card
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json['card_read'], 'the stored card file is what gets read')
        self.assertEqual(owner.get('/api/inventory').json['items'][0]['warranty_months'], 36)

        outsider = new_family('reread.outsider@example.com', 'Outsider Family')
        self.assertEqual(outsider.post(f'/api/inventory/{item_id}/read-warranty-card').status_code, 404)

    def test_unsubscribe_link_and_personal_setting(self):
        from digital_twin.services import reminders
        from datetime import date, timedelta
        owner = new_family('unsub.owner@example.com', 'Unsub Family')
        upload(owner)
        item_id = owner.get('/api/inventory').json['items'][0]['id']
        start = (date.today() + timedelta(days=10) - timedelta(days=365)).isoformat()
        owner.patch(f'/api/inventory/{item_id}', json={'date_of_purchase': start})
        settings = owner.get('/api/settings').json['settings']
        settings.update({'reminder_recipients': 'unsub.owner@example.com, grandpa@example.com'})
        owner.put('/api/settings', json=settings)

        sent.clear()
        owner.post('/api/reminders/send')
        self.assertEqual(sorted(m['to'][0] for m in sent), ['grandpa@example.com', 'unsub.owner@example.com'],
                         'one email per person')
        grandpa_mail = next(m for m in sent if m['to'] == ['grandpa@example.com'])
        link = grandpa_mail['headers']['List-Unsubscribe'].strip('<>')
        self.assertIn(link, grandpa_mail['text'])
        path = '/unsubscribe/' + link.rsplit('/unsubscribe/', 1)[1]

        visitor = app_module.app.test_client()  # not signed in, like someone clicking in their inbox
        page = visitor.get(path)
        self.assertEqual(page.status_code, 200)
        self.assertIn('grandpa@example.com', page.get_data(as_text=True))
        sent.clear()
        owner.post('/api/reminders/send')
        self.assertEqual(len(sent), 2, 'just opening the link does not unsubscribe')

        self.assertIn("unsubscribed", visitor.post(path).get_data(as_text=True))
        sent.clear()
        owner.post('/api/reminders/send')
        self.assertEqual([m['to'] for m in sent], [['unsub.owner@example.com']])

        tampered = path[:-3] + ('aaa' if not path.endswith('aaa') else 'bbb')
        self.assertEqual(visitor.get(tampered).status_code, 404)
        with app_module.app.app_context():
            self.assertIsNone(reminders.read_unsubscribe_token('nonsense'))

        # Anyone can switch reminders off (and on) for themselves from Settings.
        self.assertTrue(owner.get('/api/me/reminders').json['subscribed'])
        self.assertFalse(owner.put('/api/me/reminders', json={'subscribed': False}).json['subscribed'])
        sent.clear()
        response = owner.post('/api/reminders/send')
        self.assertEqual(response.status_code, 400, 'everyone has unsubscribed')
        self.assertEqual(sent, [])
        visitor.post(path, data={'resubscribe': '1'})
        owner.post('/api/reminders/send')
        self.assertEqual([m['to'] for m in sent], [['grandpa@example.com']])


if __name__ == '__main__':
    unittest.main(verbosity=2)
