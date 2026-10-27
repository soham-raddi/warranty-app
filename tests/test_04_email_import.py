"""Order emails forwarded to a family's private address (Resend is never called)."""
from helpers import *  # noqa: F401,F403  (sets up the test app first)


class EmailImportTests(unittest.TestCase):
    """Order emails forwarded to a workspace's private address (Resend is never called)."""

    SECRET = 'whsec_' + base64.b64encode(b'test-webhook-secret-0123456789').decode()

    @classmethod
    def setUpClass(cls):
        from digital_twin.services import inbound
        cls.inbound = inbound
        cls.saved = (inbound.INBOUND_DOMAIN, inbound.WEBHOOK_SECRET, inbound.fetch_received_email, inbound.parse_order_email)
        inbound.INBOUND_DOMAIN = 'abc123.resend.app'
        inbound.WEBHOOK_SECRET = cls.SECRET
        cls.email = {'subject': 'Your Amazon order of Philips Air Fryer', 'from': 'auto-confirm@amazon.in',
                     'date': '2026-10-20T10:00:00Z', 'text': 'Order #402-1 Philips Air Fryer Rs 7,999', 'invoice': None}
        inbound.fetch_received_email = lambda _id: dict(cls.email)
        cls.reading = {'receipt': {'store_name': 'Amazon', 'invoice_number': '402-1', 'date_of_purchase': '2026-10-20'},
                       'items': [{'item_name': 'Philips Air Fryer', 'amount': '7999', 'warranty_months': 24}]}
        inbound.parse_order_email = lambda *_args: json.loads(json.dumps(cls.reading))

    @classmethod
    def tearDownClass(cls):
        (cls.inbound.INBOUND_DOMAIN, cls.inbound.WEBHOOK_SECRET, cls.inbound.fetch_received_email,
         cls.inbound.parse_order_email) = cls.saved

    def post_event(self, event, secret=None, timestamp=None):
        import hashlib
        import hmac
        import time
        body = json.dumps(event).encode()
        msg_id, ts = 'msg_' + str(time.time_ns()), str(timestamp or int(time.time()))
        key = base64.b64decode((secret or self.SECRET).split('_', 1)[1])
        signature = base64.b64encode(hmac.new(key, f'{msg_id}.{ts}.'.encode() + body, hashlib.sha256).digest()).decode()
        return app_module.app.test_client().post('/api/inbound/email', data=body, content_type='application/json',
                                                 headers={'svix-id': msg_id, 'svix-timestamp': ts,
                                                          'svix-signature': f'v1,{signature}'})

    def received(self, to, sender, email_id, received_for=()):
        return {'type': 'email.received', 'data': {'email_id': email_id, 'from': sender, 'to': [to],
                                                   'received_for': list(received_for), 'subject': 'x'}}

    def family_with_address(self, email, name):
        client = new_family(email, name)
        address = client.get('/api/inbound/address').json['address']
        self.assertTrue(address.startswith('receipts-') and address.endswith('@abc123.resend.app'))
        return client, address

    def test_forwarded_email_waits_for_review_then_saves(self):
        owner, address = self.family_with_address('mailer@example.com', 'Mailer Family')
        response = self.post_event(self.received(address, 'Mailer <mailer@example.com>', 'em-1'))
        self.assertEqual(response.json['status'], 'stored')
        self.assertEqual(owner.get('/api/inventory').json['items'], [], 'nothing is added without review')
        self.assertEqual(owner.get('/api/inventory').json['pending_imports'], 1)

        pending = owner.get('/api/pending').json['pending']
        self.assertEqual(pending[0]['items'][0]['item_name'], 'Philips Air Fryer')
        file = owner.get(f"/files/pending/{pending[0]['id']}")
        self.assertEqual(file.status_code, 200)
        self.assertTrue(file.data.startswith(b'%PDF'), 'the email is kept as a PDF receipt')

        saved = owner.post(f"/api/pending/{pending[0]['id']}/save", json={'receipt': pending[0]['receipt'],
                                                                         'items': pending[0]['items']})
        self.assertEqual(saved.status_code, 201)
        self.assertEqual(owner.get('/api/pending').json['pending'], [])
        item = owner.get('/api/inventory').json['items'][0]
        self.assertEqual((item['item_name'], item['store_name'], item['warranty_months']), ('Philips Air Fryer', 'Amazon', 24))
        self.assertEqual(owner.get(f"/files/receipt/{item['id']}").status_code, 200, 'the email PDF became the receipt')

        again = self.post_event(self.received(address, 'mailer@example.com', 'em-1'))
        self.assertEqual(again.json['status'], 'ignored: already imported')

    def test_only_family_members_can_forward(self):
        owner, address = self.family_with_address('guarded@example.com', 'Guarded Family')
        self.assertEqual(self.post_event(self.received(address, 'spammer@example.com', 'em-spam')).json['status'],
                         'ignored: not from a member')
        # Auto-forwarding keeps the shop as sender but says who it was forwarded for.
        auto = self.post_event(self.received(f'Receipts <{address}>', 'orders@flipkart.com', 'em-auto', ['guarded@example.com']))
        self.assertEqual(auto.json['status'], 'stored')
        self.assertEqual(self.post_event(self.received('receipts-0000000000@abc123.resend.app', 'guarded@example.com',
                                                       'em-unknown')).json['status'], 'ignored: unknown address')
        self.assertEqual(len(owner.get('/api/pending').json['pending']), 1)

    def test_unsigned_or_old_requests_are_rejected(self):
        import time
        _, address = self.family_with_address('signed@example.com', 'Signed Family')
        event = self.received(address, 'signed@example.com', 'em-sig')
        wrong = 'whsec_' + base64.b64encode(b'someone-else').decode()
        self.assertEqual(self.post_event(event, secret=wrong).status_code, 401)
        self.assertEqual(self.post_event(event, timestamp=int(time.time()) - 3600).status_code, 401)
        unsigned = app_module.app.test_client().post('/api/inbound/email', json=event)
        self.assertEqual(unsigned.status_code, 401)

    def test_families_only_see_their_own_imports(self):
        owner, address = self.family_with_address('mine@example.com', 'Mine Family')
        self.post_event(self.received(address, 'mine@example.com', 'em-mine'))
        pending_id = owner.get('/api/pending').json['pending'][0]['id']
        other = new_family('theirs@example.com', 'Theirs Family')
        self.assertEqual(other.get('/api/pending').json['pending'], [])
        self.assertEqual(other.get(f'/files/pending/{pending_id}').status_code, 404)
        self.assertEqual(other.post(f'/api/pending/{pending_id}/save', json=self.reading).status_code, 404)
        self.assertEqual(other.delete(f'/api/pending/{pending_id}').status_code, 404)
        self.assertEqual(owner.delete(f'/api/pending/{pending_id}').status_code, 200)
        self.assertEqual(owner.get('/api/pending').json['pending'], [])

    def test_unreadable_email_is_kept_for_manual_details(self):
        owner, address = self.family_with_address('vague@example.com', 'Vague Family')
        self.inbound.parse_order_email = lambda *_args: {'error': "This email doesn't look like a purchase."}
        try:
            self.post_event(self.received(address, 'vague@example.com', 'em-vague'))
        finally:
            self.inbound.parse_order_email = lambda *_args: json.loads(json.dumps(self.reading))
        pending = owner.get('/api/pending').json['pending'][0]
        self.assertIn("doesn't look like a purchase", pending['note'])
        self.assertEqual(pending['items'][0]['item_name'], self.email['subject'])

    def test_replacing_the_address_and_gmail_confirmation(self):
        owner, address = self.family_with_address('rotate@example.com', 'Rotate Family')
        self.post_event(self.received(address, 'forwarding-noreply@google.com', 'em-gmail'))
        self.assertIn('Air Fryer', owner.get('/api/inbound/address').json['gmail_confirmation'])

        new_address = owner.post('/api/inbound/address').json['address']
        self.assertNotEqual(new_address, address)
        self.assertEqual(self.post_event(self.received(address, 'rotate@example.com', 'em-old')).json['status'],
                         'ignored: unknown address')
        self.assertEqual(self.post_event(self.received(new_address, 'rotate@example.com', 'em-new')).json['status'], 'stored')

        owner.post('/api/workspace/invites', json={'email': 'kid@example.com'})
        kid = sign_in('kid@example.com')
        self.assertEqual(kid.post('/api/inbound/address').status_code, 403, 'only owners and admins replace it')

    def test_email_helpers(self):
        self.assertEqual(self.inbound.html_to_text('<style>x{}</style><p>Order <b>#1</b></p><table><tr><td>Fryer</td>'
                                                   '<td>Rs 7,999</td></tr></table>'), 'Order #1\n\nFryer | Rs 7,999')
        pdf = self.inbound.email_as_pdf('Subject', 'shop@example.com', 'today', 'line ' * 400)
        self.assertTrue(pdf.startswith(b'%PDF'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
