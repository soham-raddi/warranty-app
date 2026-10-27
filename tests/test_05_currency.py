"""Items bought in another currency keep it; totals convert to the family currency."""
from helpers import *  # noqa: F401,F403  (sets up the test app first)


class CurrencyTests(unittest.TestCase):
    """Items bought in another currency keep it; totals convert to the workspace currency."""

    def test_items_keep_their_currency_and_totals_convert(self):
        owner = new_family('traveller@example.com', 'Traveller Family')  # workspace currency is INR
        details = {'receipt': {'store_name': 'Cytron', 'invoice_number': '548720', 'currency': 'myr'},
                   'items': [{'item_name': 'SG90 Micro Servo', 'amount': '6.50'},
                             {'item_name': 'Vibrating Motor', 'amount': '9.00'}]}
        self.assertEqual(save(owner, details).status_code, 201)
        save(owner, {'receipt': {'invoice_number': 'IN-1'}, 'items': [{'item_name': 'Kettle', 'amount': '700'}]})

        data = owner.get('/api/inventory').json
        servo = next(i for i in data['items'] if i['item_name'] == 'SG90 Micro Servo')
        self.assertEqual(servo['currency'], 'MYR')
        self.assertIsNone(servo['home_amount'], 'no exchange rate yet')
        self.assertEqual(data['total_spent'], 700, 'ringgit items are left out until a rate is known')
        self.assertEqual(data['unconverted_items'], 2)

        database.save_exchange_rates({'EUR': 1.0, 'INR': 100.0, 'MYR': 5.0}, '2026-10-25')
        data = owner.get('/api/inventory').json
        servo = next(i for i in data['items'] if i['item_name'] == 'SG90 Micro Servo')
        self.assertEqual(servo['home_amount'], 130.0, 'RM6.50 at 20 rupees per ringgit')
        self.assertEqual(data['total_spent'], 700 + 130 + 180)
        self.assertEqual((data['converted_items'], data['unconverted_items']), (2, 0))
        kettle = next(i for i in data['items'] if i['item_name'] == 'Kettle')
        self.assertEqual((kettle['currency'], kettle['home_amount']), ('INR', 700), 'no currency means the workspace one')

    def test_currency_can_be_corrected(self):
        owner = new_family('fixer@example.com', 'Fixer Family')
        save(owner, {'receipt': {'invoice_number': 'FX-1'}, 'items': [{'item_name': 'Servo', 'amount': '6.5'}]})
        item_id = owner.get('/api/inventory').json['items'][0]['id']
        self.assertEqual(owner.patch(f'/api/inventory/{item_id}', json={'currency': 'ringgit'}).status_code, 400)
        self.assertEqual(owner.patch(f'/api/inventory/{item_id}', json={'currency': 'myr'}).status_code, 200)
        self.assertEqual(owner.get('/api/inventory').json['items'][0]['currency'], 'MYR')
        owner.patch(f'/api/inventory/{item_id}', json={'currency': ''})
        self.assertEqual(owner.get('/api/inventory').json['items'][0]['currency'], 'MYR', 'blank keeps the currency')

    def test_changing_the_workspace_currency_converts_instead_of_relabelling(self):
        owner = new_family('switcher@example.com', 'Switcher Family')  # INR
        save(owner, {'receipt': {'invoice_number': 'SW-1'}, 'items': [{'item_name': 'Phone', 'amount': '33999'}]})
        # An item from before currencies were tracked has none stored.
        workspace_id = owner.get('/api/auth/me').json['workspace']['id']
        database.cursor.execute("UPDATE appliances_v5 SET currency = NULL WHERE workspace_id = ?", (workspace_id,))
        database.conn.commit()
        database.save_exchange_rates({'EUR': 1.0, 'INR': 100.0, 'USD': 1.25}, '2026-10-25')

        settings = owner.get('/api/settings').json['settings']
        settings['currency_code'] = 'USD'
        self.assertEqual(owner.put('/api/settings', json=settings).status_code, 200)
        data = owner.get('/api/inventory').json
        self.assertEqual(data['items'][0]['currency'], 'INR', 'the phone was bought in rupees')
        self.assertEqual(data['total_spent'], 424.99, 'Rs 33,999 is about $425, not $33,999')

    def test_receipt_currency_is_read(self):
        from digital_twin.services import ai as llm_extractor
        cleaned = llm_extractor._clean_receipt_data({'currency': 'myr', 'items': [{'item_name': 'Servo'}]})
        self.assertEqual(cleaned['receipt']['currency'], 'MYR')
        self.assertIsNone(llm_extractor._clean_receipt_data({'currency': 'RM', 'items': []})['receipt']['currency'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
