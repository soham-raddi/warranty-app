"""Reading a stored receipt again without saving."""
from helpers import *  # noqa: F401,F403  (sets up the test app first)


class RereadReceiptTests(unittest.TestCase):

    def test_receipt_can_be_read_again_without_saving(self):
        owner = new_family('reread.receipt@example.com', 'Reread Receipt Family')
        save(owner, {'receipt': {'invoice_number': 'RR-1', 'store_name': 'Croma'},
                     'items': [{'item_name': 'Fridge', 'amount': '50000'}, {'item_name': 'Mixer Grinder', 'amount': '3299'}]})
        mixer = next(i for i in owner.get('/api/inventory').json['items'] if i['item_name'] == 'Mixer Grinder')
        original = receipts_routes.parse_receipt_image_to_json
        receipts_routes.parse_receipt_image_to_json = lambda _d, _f: {
            'receipt': {'store_name': 'Croma Retail', 'date_of_purchase': '2026-10-01', 'currency': 'INR'},
            'items': [{'item_name': 'Samsung Fridge 253L', 'amount': '50000'},
                      {'item_name': 'Philips Mixer Grinder 750W', 'amount': '3299', 'warranty_months': 24}]}
        try:
            response = owner.post(f"/api/inventory/{mixer['id']}/read-receipt")
        finally:
            receipts_routes.parse_receipt_image_to_json = original
        self.assertEqual(response.status_code, 200)
        fields = response.json['fields']
        self.assertEqual(fields['item_name'], 'Philips Mixer Grinder 750W', 'the matching product on the receipt')
        self.assertEqual((fields['warranty_months'], fields['store_name']), (24, 'Croma Retail'))
        self.assertEqual(response.json['products_on_receipt'], 2)
        unchanged = next(i for i in owner.get('/api/inventory').json['items'] if i['id'] == mixer['id'])
        self.assertEqual(unchanged['item_name'], 'Mixer Grinder', 'nothing is saved until the person confirms')

        outsider = new_family('reread.receipt.outsider@example.com', 'Other Reread Family')
        self.assertEqual(outsider.post(f"/api/inventory/{mixer['id']}/read-receipt").status_code, 404)


if __name__ == '__main__':
    unittest.main(verbosity=2)
