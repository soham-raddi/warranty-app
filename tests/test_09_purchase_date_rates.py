"""Items convert at the exchange rate on their purchase date."""
from helpers import *  # noqa: F401,F403  (sets up the test app first)


class PurchaseDateRateTests(unittest.TestCase):

    def test_items_convert_at_the_rate_on_their_purchase_date(self):
        owner = new_family('history@example.com', 'History Family')  # INR
        database.save_exchange_rates({'EUR': 1.0, 'INR': 100.0, 'MYR': 5.0, 'USD': 1.0}, '2026-10-26')   # today: RM1 = Rs 20
        database.save_historical_rates('2026-03-11', {'EUR': 1.0, 'INR': 96.0, 'MYR': 4.0, 'USD': 1.2})  # then: RM1 = Rs 24
        save(owner, {'receipt': {'invoice_number': 'H-1', 'currency': 'MYR', 'date_of_purchase': '2026-03-11'},
                     'items': [{'item_name': 'Servo', 'amount': '10'}]})
        save(owner, {'receipt': {'invoice_number': 'H-2', 'currency': 'MYR'}, 'items': [{'item_name': 'Motor', 'amount': '10'}]})
        data = owner.get('/api/inventory').json
        servo = next(i for i in data['items'] if i['item_name'] == 'Servo')
        motor = next(i for i in data['items'] if i['item_name'] == 'Motor')
        self.assertEqual((servo['home_amount'], servo['rate_date']), (240.0, '2026-03-11'), 'purchase-date rate')
        self.assertEqual((motor['home_amount'], motor['rate_date']), (200.0, None), "no date: today's rate")
        self.assertEqual(data['total_spent'], 440.0)
        self.assertEqual((data['converted_items'], data['converted_at_purchase_date']), (2, 1))

        # The stored day covers every currency, so switching the family to USD still uses that day.
        settings = owner.get('/api/settings').json['settings']
        settings['currency_code'] = 'USD'
        owner.put('/api/settings', json=settings)
        servo = next(i for i in owner.get('/api/inventory').json['items'] if i['item_name'] == 'Servo')
        self.assertEqual(servo['home_amount'], 3.0, 'RM10 at 4 per EUR and 1.2 USD per EUR')

    def test_missing_purchase_dates_are_fetched_once(self):
        owner = new_family('backfill@example.com', 'Backfill Family')  # INR
        save(owner, {'receipt': {'invoice_number': 'B-1', 'currency': 'SGD', 'date_of_purchase': '2026-02-02'},
                     'items': [{'item_name': 'Cable', 'amount': '5'}]})
        self.assertIn('2026-02-02', database.purchase_dates_missing_rates())

        fetched = []
        original = fx.fetch_day_rates

        def fake_fetch(day):
            fetched.append(day)
            database.save_historical_rates(day, {'EUR': 1.0, 'INR': 100.0, 'SGD': 1.5})
            return True
        fx.fetch_day_rates = fake_fetch
        try:
            fx.refresh_if_needed()
            fx.refresh_if_needed()
            fx.ensure_day_rates('2026-02-02', '2999-01-01')
        finally:
            fx.fetch_day_rates = original
        self.assertEqual(fetched.count('2026-02-02'), 1, 'each day is fetched only once')
        self.assertNotIn('2999-01-01', fetched, 'future dates are never looked up')
        self.assertNotIn('2026-02-02', database.purchase_dates_missing_rates())
        cable = next(i for i in owner.get('/api/inventory').json['items'] if i['item_name'] == 'Cable')
        self.assertEqual((cable['home_amount'], cable['rate_date']), (333.33, '2026-02-02'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
