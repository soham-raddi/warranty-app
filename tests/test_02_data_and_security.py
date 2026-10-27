"""Editing items, exports, ownership and deletion, and production hardening (headers, CSRF, rate limits)."""
from helpers import *  # noqa: F401,F403  (sets up the test app first)


class DataAndSecurityTests(unittest.TestCase):
    """Production hardening, item editing and data controls (independent of the workspace tests above)."""

    # --- editing items and per-item warranty length -------------------------------------------------

    def test_edit_item_and_per_item_warranty(self):
        owner = new_family('editor@example.com', 'Edit Family')
        upload(owner)
        item = owner.get('/api/inventory').json['items'][0]
        self.assertEqual(item['warranty_expires_on'], '2027-05-01', 'default: purchase date + 365 days')

        changed = owner.patch(f"/api/inventory/{item['id']}", json={
            'item_name': 'Big Fridge', 'warranty_months': 24, 'date_of_purchase': '2026-01-31'})
        self.assertEqual(changed.status_code, 200)
        item = next(i for i in owner.get('/api/inventory').json['items'] if i['id'] == item['id'])
        self.assertEqual(item['item_name'], 'Big Fridge')
        self.assertEqual(item['warranty_months'], 24)
        self.assertEqual(item['warranty_expires_on'], '2028-01-31')

        # Month arithmetic clamps to the end of shorter months.
        owner.patch(f"/api/inventory/{item['id']}", json={'warranty_months': 1})
        item = next(i for i in owner.get('/api/inventory').json['items'] if i['id'] == item['id'])
        self.assertEqual(item['warranty_expires_on'], '2026-02-28')

        # Clearing the length falls back to the workspace default.
        owner.patch(f"/api/inventory/{item['id']}", json={'warranty_months': ''})
        item = next(i for i in owner.get('/api/inventory').json['items'] if i['id'] == item['id'])
        self.assertIsNone(item['warranty_months'])

        for bad in ({'warranty_months': 0}, {'warranty_months': 'two'}, {'date_of_purchase': '31/01/2026'},
                    {'item_name': '   '}):
            self.assertEqual(owner.patch(f"/api/inventory/{item['id']}", json=bad).status_code, 400, bad)

    def test_members_can_edit_but_other_families_cannot(self):
        owner = new_family('edit.owner@example.com', 'Edit Owner Family')
        upload(owner)
        item_id = owner.get('/api/inventory').json['items'][0]['id']
        token = token_from(owner.post('/api/workspace/invites', json={'email': 'edit.member@example.com'}).json['link'])
        member = sign_in('edit.member@example.com')
        member.post(f'/join/{token}')
        self.assertEqual(member.patch(f'/api/inventory/{item_id}', json={'brand': 'LG'}).status_code, 200)

        outsider = new_family('edit.outsider@example.com', 'Outsider Family')
        self.assertEqual(outsider.patch(f'/api/inventory/{item_id}', json={'brand': 'Hacked'}).status_code, 404)
        self.assertEqual(outsider.get(f'/api/inventory/{item_id}').status_code, 404)
        self.assertEqual(owner.get(f'/api/inventory/{item_id}').json['item']['brand'], 'LG')

    # --- export, transfer, deleting -----------------------------------------------------------------

    def test_csv_export_is_scoped_and_neutralises_formulas(self):
        owner = new_family('export@example.com', 'Export Family')
        upload(owner)
        item_id = owner.get('/api/inventory').json['items'][0]['id']
        owner.patch(f'/api/inventory/{item_id}', json={'item_name': '=HYPERLINK("http://evil")'})
        response = owner.get('/api/workspace/export.xlsx')
        self.assertEqual(response.status_code, 200)
        self.assertIn('.xlsx', response.headers['Content-Disposition'])
        sheet = read_workbook(response)['Inventory']
        name = sheet.cell(row=2, column=1)
        self.assertEqual((name.value, name.data_type), ('=HYPERLINK("http://evil")', 's'),
                         'formulas from receipts are stored as text and never run')
        self.assertNotIn('Kettle', [c.value for c in sheet['A']], 'only this workspace is exported')

    def test_transfer_ownership(self):
        owner = new_family('transfer.owner@example.com', 'Transfer Family')
        token = token_from(owner.post('/api/workspace/invites', json={'email': 'transfer.new@example.com'}).json['link'])
        heir = sign_in('transfer.new@example.com')
        heir.post(f'/join/{token}')
        heir_id = heir.get('/api/auth/me').json['user']['id']

        self.assertEqual(heir.post('/api/workspace/transfer', json={'user_id': heir_id}).status_code, 403)
        self.assertEqual(owner.post('/api/workspace/transfer', json={'user_id': 999999}).status_code, 400)
        self.assertEqual(owner.post('/api/workspace/transfer', json={'user_id': heir_id}).status_code, 200)
        self.assertEqual(heir.get('/api/auth/me').json['role'], 'owner')
        self.assertEqual(owner.get('/api/auth/me').json['role'], 'admin')

    def test_delete_workspace_removes_everything(self):
        owner = new_family('delete.ws@example.com', 'Doomed Family')
        upload(owner)
        item = owner.get('/api/inventory').json['items'][0]
        stored_file = os.path.join(TEST_DIR, item['file_path'])
        self.assertTrue(os.path.isfile(stored_file))
        workspace_id = owner.get('/api/auth/me').json['workspace']['id']

        self.assertEqual(owner.delete('/api/workspace', json={'confirm_name': 'wrong'}).status_code, 400)
        response = owner.delete('/api/workspace', json={'confirm_name': 'Doomed Family'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(os.path.exists(stored_file), 'receipt files are deleted too')
        database.cursor.execute('SELECT COUNT(*) FROM appliances_v5 WHERE workspace_id = ?', (workspace_id,))
        self.assertEqual(database.cursor.fetchone()[0], 0)
        self.assertIn('/welcome', owner.get('/').headers['Location'])

    def test_delete_account_rules(self):
        owner = new_family('leaving@example.com', 'Shared Family')
        token = token_from(owner.post('/api/workspace/invites', json={'email': 'stays@example.com'}).json['link'])
        sign_in('stays@example.com').post(f'/join/{token}')

        self.assertEqual(owner.delete('/api/account', json={'confirm_email': 'nope@example.com'}).status_code, 400)
        blocked = owner.delete('/api/account', json={'confirm_email': 'leaving@example.com'})
        self.assertEqual(blocked.status_code, 400)
        self.assertIn('Shared Family', blocked.json['error'], 'must transfer shared workspaces first')

        solo = new_family('solo@example.com', 'Solo Family')
        upload(solo)
        self.assertEqual(solo.delete('/api/account', json={'confirm_email': 'SOLO@example.com'}).status_code, 200)
        self.assertEqual(solo.get('/api/inventory').status_code, 401, 'signed out')
        database.cursor.execute('SELECT COUNT(*) FROM users WHERE email = ?', ('solo@example.com',))
        self.assertEqual(database.cursor.fetchone()[0], 0)

    # --- security ---------------------------------------------------------------------------------

    def test_cross_site_requests_are_blocked(self):
        owner = new_family('csrf@example.com', 'CSRF Family')
        evil = owner.post('/api/workspace/invites', json={'email': 'x1@example.com'},
                          headers={'Origin': 'https://evil.example'})
        self.assertEqual(evil.status_code, 403)
        null_origin = owner.patch('/api/workspace', json={'name': 'Hacked'}, headers={'Origin': 'null'})
        self.assertEqual(null_origin.status_code, 403)
        same_site = owner.post('/api/workspace/invites', json={'email': 'x2@example.com'},
                               headers={'Origin': 'http://localhost'})
        self.assertEqual(same_site.status_code, 201)
        # Reading is never blocked.
        self.assertEqual(owner.get('/api/inventory', headers={'Origin': 'https://evil.example'}).status_code, 200)

    def test_security_headers(self):
        owner = new_family('headers@example.com', 'Header Family')
        page = owner.get('/settings')
        self.assertIn("frame-ancestors 'none'", page.headers['Content-Security-Policy'])
        self.assertEqual(page.headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(page.headers['X-Frame-Options'], 'DENY')
        self.assertIn('no-store', owner.get('/api/inventory').headers['Cache-Control'])

    def test_dev_login_is_impossible_in_production(self):
        from digital_twin.routes import auth as auth_routes
        auth_routes.IS_PRODUCTION = True
        try:
            response = app_module.app.test_client().get('/auth/dev-login?email=intruder@example.com')
            self.assertIn('error=dev_disabled', response.headers['Location'])
        finally:
            auth_routes.IS_PRODUCTION = False

    def test_cron_requires_the_secret(self):
        client = app_module.app.test_client()
        self.assertEqual(client.get('/api/cron/daily').status_code, 401)
        self.assertEqual(client.get('/api/cron/daily', headers={'Authorization': 'Bearer wrong'}).status_code, 401)
        ok = client.get('/api/cron/daily', headers={'Authorization': 'Bearer test-cron-secret'})
        self.assertEqual(ok.status_code, 200)
        self.assertIn('old_invites_removed', ok.json)

    def test_rate_limits(self):
        owner = new_family('busy@example.com', 'Busy Family')
        os.environ['RATE_LIMITS_ENABLED'] = 'true'
        try:
            statuses = [owner.post('/api/chat', json={'message': f'hi {n}', 'conversation_id': 'rl'}).status_code
                        for n in range(12)]
        finally:
            os.environ['RATE_LIMITS_ENABLED'] = 'false'
        self.assertEqual(statuses[:10], [200] * 10)
        self.assertEqual(statuses[10:], [429, 429], 'the 11th message in a minute is refused')

    def test_files_cannot_escape_the_uploads_folder(self):
        from digital_twin.services import storage
        with self.assertRaises(ValueError):
            storage._safe_local_path('uploads/../app.py')

    def test_public_pages(self):
        client = app_module.app.test_client()
        for path in ('/privacy', '/terms', '/login', '/manifest.webmanifest', '/static/icons/icon-192.png'):
            self.assertEqual(client.get(path).status_code, 200, path)
        self.assertIn('Groq', client.get('/privacy').get_data(as_text=True))
        self.assertEqual(client.get('/inventory').status_code, 302, 'app pages still need sign-in')


    def test_hosted_site_never_asks_families_for_mail_server_details(self):
        owner = new_family('mailless@example.com', 'Mailless Family')
        emails.EMAIL_ENABLED = False
        try:
            page = owner.get('/settings').get_data(as_text=True)
            self.assertNotIn('id="settingsReminderRecipients"', page)
            self.assertIn("Email reminders aren't available yet", page)
            self.assertEqual(owner.post('/api/reminders/send').status_code, 404)
        finally:
            emails.EMAIL_ENABLED = True
        page = owner.get('/settings').get_data(as_text=True)
        self.assertNotIn('SMTP', page, 'families never configure a mail server')
        self.assertIn('id="settingsReminderRecipients"', page)

    # --- warranty cards -----------------------------------------------------------------------------

    def test_warranty_card_sets_the_real_warranty_length(self):
        owner = new_family('cards@example.com', 'Card Family')
        upload(owner)
        item = owner.get('/api/inventory').json['items'][0]
        self.assertEqual(item['warranty_expires_on'], '2027-05-01', 'default period before the card')

        receipts_routes.parse_warranty_card = lambda _d, _f: {
            'warranty_months': 24, 'warranty_start_date': '2020-01-01', 'coverage_notes': '2 years, 10 on compressor'}
        try:
            response = attach_card(owner, item['id'])
        finally:
            receipts_routes.parse_warranty_card = _unreadable_card
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json['card_read'])
        self.assertIn('2-year', response.json['message'])
        item = next(i for i in owner.get('/api/inventory').json['items'] if i['id'] == item['id'])
        self.assertEqual(item['warranty_months'], 24)
        self.assertEqual(item['warranty_expires_on'], '2028-05-01', 'card length counts from the purchase date')
        self.assertEqual(item['date_of_purchase'], '2026-05-01', 'the purchase date from the receipt is kept')
        self.assertIn('compressor', item['warranty_info'])

    def test_unreadable_warranty_card_keeps_the_default(self):
        owner = new_family('blurry@example.com', 'Blurry Family')
        upload(owner)
        item_id = owner.get('/api/inventory').json['items'][0]['id']
        results = ({'error': 'nope'}, {'warranty_months': None, 'warranty_start_date': None, 'coverage_notes': None})
        try:
            for result in results:
                receipts_routes.parse_warranty_card = lambda _d, _f, r=result: r
                response = attach_card(owner, item_id)
                self.assertEqual(response.status_code, 200, 'the card is still attached')
                self.assertFalse(response.json['card_read'])
        finally:
            receipts_routes.parse_warranty_card = _unreadable_card
        item = owner.get('/api/inventory').json['items'][0]
        self.assertTrue(item['has_warranty_card'])
        self.assertIsNone(item['warranty_months'])

    # --- email (Resend is never called) --------------------------------------------------------------

    def test_invite_can_be_emailed(self):
        owner = new_family('inviter@example.com', 'Mail Family')
        sent.clear()
        created = owner.post('/api/workspace/invites', json={'email': 'cousin@example.com', 'send_email': True})
        self.assertEqual(created.status_code, 201)
        self.assertTrue(created.json['email_sent'])
        self.assertEqual(sent[-1]['to'], 'cousin@example.com')
        self.assertIn(created.json['link'], sent[-1]['text'])
        self.assertIn('Mail Family', sent[-1]['subject'])

        again = owner.post(f"/api/workspace/invites/{created.json['invite']['id']}/email")
        self.assertEqual(again.status_code, 200)
        self.assertNotEqual(again.json['link'], created.json['link'], 'emailing again issues a fresh link')
        self.assertIn(again.json['link'], sent[-1]['text'])

        count = len(sent)
        quiet = owner.post('/api/workspace/invites', json={'email': 'aunt@example.com', 'send_email': False})
        self.assertFalse(quiet.json['email_sent'])
        self.assertEqual(len(sent), count, 'no email unless asked')

    def test_failed_invite_email_still_gives_a_link(self):
        owner = new_family('unlucky@example.com', 'Unlucky Family')

        def refuse(*_args, **_kwargs):
            raise emails.EmailError('The email service refused this address.')
        emails.send_email = refuse
        try:
            created = owner.post('/api/workspace/invites', json={'email': 'x@example.com', 'send_email': True})
        finally:
            emails.send_email = fake_send_email
        self.assertEqual(created.status_code, 201)
        self.assertFalse(created.json['email_sent'])
        self.assertIn('/join/', created.json['link'])
        self.assertIn('refused', created.json['message'])

    def test_reminders_are_sent_once_per_stage(self):
        from datetime import date, timedelta
        owner = new_family('remind@example.com', 'Remind Family')
        upload(owner)
        item_id = owner.get('/api/inventory').json['items'][0]['id']
        # Make the warranty end in 20 days: inside the 30-day window but not yet at the 7-day stage.
        start = (date.today() + timedelta(days=20) - timedelta(days=365)).isoformat()
        owner.patch(f'/api/inventory/{item_id}', json={'date_of_purchase': start})
        settings = owner.get('/api/settings').json['settings']
        settings.update({'reminders_enabled': '1', 'reminder_days_before': 30, 'reminder_recipients': ''})
        self.assertEqual(owner.put('/api/settings', json=settings).status_code, 200)

        cron = app_module.app.test_client()
        auth = {'Authorization': 'Bearer test-cron-secret'}
        mine = lambda: [m for m in sent if m['to'] == ['remind@example.com']]  # noqa: E731
        sent.clear()
        cron.get('/api/cron/daily', headers=auth)
        self.assertEqual(len(mine()), 1, 'members get the reminder when no recipients are set')
        self.assertIn('Fridge', mine()[0]['subject'])
        cron.get('/api/cron/daily', headers=auth)
        self.assertEqual(len(mine()), 1, 'not repeated the next day')

        # Reaching the 7-day stage sends one more.
        start = (date.today() + timedelta(days=5) - timedelta(days=365)).isoformat()
        owner.patch(f'/api/inventory/{item_id}', json={'date_of_purchase': start})
        cron.get('/api/cron/daily', headers=auth)
        self.assertEqual(len(mine()), 2)

        # "Send now" always sends what is in the window.
        response = owner.post('/api/reminders/send')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['items'], 1)

    def test_reminder_recipients_are_validated(self):
        owner = new_family('validate@example.com', 'Validate Family')
        settings = owner.get('/api/settings').json['settings']
        settings['reminder_recipients'] = 'good@example.com, not-an-email'
        self.assertEqual(owner.put('/api/settings', json=settings).status_code, 400)
        settings['reminder_recipients'] = 'Good@Example.com; dad@example.com'
        saved = owner.put('/api/settings', json=settings).json['settings']
        self.assertEqual(saved['reminder_recipients'], 'good@example.com, dad@example.com')


if __name__ == '__main__':
    unittest.main(verbosity=2)
