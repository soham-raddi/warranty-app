"""Service records keep their own currency."""
from helpers import *  # noqa: F401,F403  (sets up the test app first)


class ServiceCurrencyTests(unittest.TestCase):

    def test_service_records_keep_their_own_currency(self):
        owner = new_family('repairs@example.com', 'Repairs Family')  # INR
        save(owner, {'receipt': {'invoice_number': 'RP-1', 'currency': 'MYR'}, 'items': [{'item_name': 'Servo', 'amount': '6.5'}]})
        item_id = owner.get('/api/inventory').json['items'][0]['id']
        url = f'/api/inventory/{item_id}/service-history'
        default = owner.post(url, json={'service_date': '2026-10-01', 'service_type': 'Repair', 'cost': '20'}).json['entries'][0]
        self.assertEqual(default['currency'], 'MYR', "defaults to the item's currency")
        chosen = owner.post(url, json={'service_date': '2026-10-02', 'service_type': 'Check', 'cost': '499',
                                       'currency': 'inr'}).json['entries'][0]
        self.assertEqual(chosen['currency'], 'INR')
        self.assertEqual(owner.post(url, json={'service_date': '2026-10-03', 'service_type': 'X', 'currency': 'rupees'}).status_code, 400)
        history = read_workbook(owner.get('/api/workspace/export.xlsx'))['Service history']
        self.assertIn('Currency', [c.value for c in history[1]])
        self.assertEqual({c.value for c in history['F'][1:]}, {'MYR', 'INR'})


if __name__ == '__main__':
    unittest.main(verbosity=2)
