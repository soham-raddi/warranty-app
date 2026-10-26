# Digital Twin | Smart Inventory & Warranty Manager

An AI-powered, local-first inventory management system that acts as a "Digital Twin" for your physical assets. 

This application allows you to digitize receipts and invoices (both images and multi-page PDFs). It automatically extracts structured product data using state-of-the-art Large Language Models (LLMs) and allows you to query your inventory using an intelligent, multimodal chatbot that remembers your context and can "see" your uploaded documents.

---

## Features

* **Smart Digitization:** Drag-and-drop receipt images (JPG/PNG) or multi-page PDF invoices.
* **Universal Data Extraction:** Powered by a Groq vision model (default `qwen/qwen3.8-27b`, configurable with `GROQ_VISION_MODEL`). It intelligently ignores shipping fees or platform charges and extracts exact pricing, warranty info, and invoice numbers for your primary purchases.
* **Conversational AI Assistant:** A chatbot (default `openai/gpt-oss-120b`, configurable with `GROQ_CHAT_MODEL`) that remembers your chat history. It uses fuzzy matching to automatically pull up the relevant receipt image from your hard drive to answer highly specific questions about the fine print.
* **Auto-Compression Engine:** Built-in image compression and PDF slicing (via Pillow and PyMuPDF) ensures massive smartphone photos and long documents are processed in milliseconds without crashing the API.
* **Local Storage:** Inventory data lives in a local SQLite database (`inventory.db`) and uploaded documents in a private `uploads/` folder that is only served to signed-in users. Note that receipt images are sent to the Groq API for extraction and chat.
* **Analytics Dashboard:** Instantly tracks your total financial investment and counts active warranties.

---

## Tech Stack

* **Backend:** Python, Flask
* **Database:** SQLite (Local)
* **AI & LLM Inference:** Groq API (Llama Vision & Text Models)
* **Document Processing:** PyMuPDF (`fitz`), Pillow (`PIL`)
* **NLP & Search:** `thefuzz`, `python-Levenshtein`, `scikit-learn`
* **Frontend:** HTML5, CSS3, JavaScript, Bootstrap 5

---

## Prerequisites

Before you begin, ensure you have the following installed on your machine:
* **Python 3.8 or higher**
* **Git**
* A free **Groq API Key** (You can get one at [console.groq.com/keys](https://console.groq.com/keys))

---

## Installation & Setup

Follow these steps to get your Digital Twin running locally.

### 1. Clone the Repository
Open your terminal or command prompt and clone this repository to your local machine:

```
git clone [https://github.com/yourusername/digital-twin-inventory.git](https://github.com/yourusername/digital-twin-inventory.git)
cd digital-twin-inventory
```

### 2. Set Up a Virtual Environment (Recommended)
It's best practice to run Python applications in an isolated environment to prevent dependency conflicts.

```
# Create the virtual environment
python -m venv venv

# Activate the virtual environment
# On Windows:
venv\Scripts\activate
# On macOS/Linux:
source venv/bin/activate
```

### 3. Install Dependencies
Install all the required Python packages using the provided requirements.txt file:

```
pip install -r requirements.txt
```

### 4. Configure Environment Variables
Create a file named .env in the root directory of the project. Add your API keys and Google OAuth credentials:

```ini
GROQ_API_KEY=your_actual_groq_api_key_here
SECRET_KEY=generate_a_secure_random_key_here
GOOGLE_CLIENT_ID=your-google-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-google-client-secret
ALLOW_NEW_WORKSPACES=true        # false = invite-only site
OWNER_EMAIL=your-email@gmail.com  # Only used when upgrading an existing install
GROQ_VISION_MODEL=qwen/qwen3.8-27b    # Optional: must accept images
GROQ_CHAT_MODEL=openai/gpt-oss-120b   # Optional
ALLOW_DEV_LOGIN=false             # Set to true to sign in as the owner from 127.0.0.1 without Google
FLASK_DEBUG=false                 # Set to true for auto-reload and the debugger (local only)
```

See `.env.example` for all options. If `SECRET_KEY` is missing, a random key is used and everyone is signed out whenever the server restarts.

*Note: In Google Cloud Console, set your Authorized Redirect URI to:*  
`http://127.0.0.1:5000/auth/callback`

### 5. Run the Application
Start the Flask development server:

```
python app.py
```
Open your web browser and navigate to http://127.0.0.1:5000/.
(Note: The application will automatically generate the inventory.db database and an uploads/ folder upon its first run. Files from older versions stored in static/uploads/ are moved there automatically.)

### Deploying to Vercel
Vercel runs the app as a serverless function whose disk is temporary, so production data lives in **Supabase**
(Postgres for the database, a private Storage bucket for receipts). Locally the app keeps using `inventory.db`
and the `uploads/` folder.

1. **Supabase:** create a project in the **South Asia (Mumbai)** region (it must match `"regions": ["bom1"]` in
   `vercel.json`; if you pick another region, change that line to the nearest Vercel region). In **Storage**, create a bucket named `receipts` and keep it **private**.
   Copy the *Transaction pooler* connection string (port 6543) from **Project Settings -> Database**, and the
   *Project URL* and *service_role* key from **Project Settings -> API**.
2. **Vercel environment variables** (Project Settings -> Environment Variables, Production): `SECRET_KEY`,
   `GROQ_API_KEY`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `DATABASE_URL`, `SUPABASE_URL`,
   `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_BUCKET=receipts`, `CRON_SECRET`, `CONTACT_EMAIL`, and optionally
   `RESEND_API_KEY` and `EMAIL_FROM` for emails, plus `INBOUND_EMAIL_DOMAIN` and `RESEND_WEBHOOK_SECRET` for importing
   order emails (see below). See `.env.example`.
3. **Google Cloud:** add `https://<your-app>.vercel.app/auth/callback` as an authorized redirect URI, then publish
   the OAuth consent screen with `https://<your-app>.vercel.app/` as the homepage and
   `https://<your-app>.vercel.app/privacy` as the privacy policy.
4. Deploy. `vercel.json` sets a 60-second function timeout and a daily job at 03:00 UTC that cleans up and sends
   warranty reminder emails.

On Vercel the app automatically: requires `SECRET_KEY`, uses secure cookies and HTTPS URLs, disables dev login,
limits uploads to 4 MB (large photos are shrunk in the browser first), and serves files through short-lived
signed links.

### Family Workspaces
Anyone with a Google account can sign in. Each family gets its own private **workspace** with its own items, receipts, settings and members:

1. A new person signs in and is offered to **create a workspace** (they become its owner).
2. The owner (or an admin) invites a family member from **Settings → Workspace & Members** by entering their email. This creates a personal **invite link**, which the app emails to them when email is set up (see below); it can also be copied and shared on WhatsApp or text.
3. Opening the link shows who invited them; after signing in with **any** Google account they click **Join**. Links work once and expire after 7 days; "Email again" or "New link" replaces a lost link and "Revoke" cancels it. If they sign in with the exact email that was invited, they also join automatically.
4. Someone can belong to several workspaces and switch between them from the menu in the top-right corner.

Roles: **Owner** (everything, including renaming the workspace and adding admins), **Admin** (delete items, change settings, invite members), **Member** (view, upload receipts, chat). Chat history is private to each person, even inside a shared workspace.

### Emails
Emails are sent through [Resend](https://resend.com), set up once for the whole site, so families never configure anything.
Set `RESEND_API_KEY` (and `EMAIL_FROM`) to turn on:

- **Emailed invites:** "Email the invite to them" when inviting, and "Email again" on pending invites.
- **Warranty reminders:** in **Settings → Alerts & reminders**, an owner or admin turns on reminders and picks the window
  (30 days by default). The daily job emails each workspace's members (or the chosen recipients) when a warranty enters
  the window, then again 7 days and 1 day before it ends. Each reminder is sent once, as a separate email per person with
  an unsubscribe link (people can also turn reminders off for themselves in Settings). "Send reminders now" emails the
  current list right away.

Resend only delivers to other people's inboxes from a **domain you have verified** in Resend. Until then, the default
`onboarding@resend.dev` sender can only email the address of your own Resend account.

Without `RESEND_API_KEY`, the email options are hidden and invite links are shared by hand.

### Importing order emails
Each workspace gets a private forwarding address (shown on the **Upload** page). Forward an order confirmation from
any shop to it, or set up a Gmail filter that forwards them automatically, and the purchase appears under
**From your email** to check and save. If the email has a PDF or image invoice attached, that is read and kept as
the receipt; otherwise the email itself is saved as a PDF. Only email sent or forwarded by members of the workspace
is accepted, and an owner or admin can replace the address at any time.

To turn it on, receiving must be set up in Resend (no custom domain needed):
1. Use a Resend API key with **full access** (receiving needs it) as `RESEND_API_KEY`.
2. In Resend, open **Emails > Receiving**, choose **Receiving address** from the menu and copy the domain part
   (`<id>.resend.app`) into `INBOUND_EMAIL_DOMAIN`.
3. Under **Webhooks**, add `https://<your-app>/api/inbound/email` for the `email.received` event and copy its
   signing secret into `RESEND_WEBHOOK_SECRET`.

Purchases nobody reviews are removed after 30 days by the daily job.

Upgrading from a version without workspaces is automatic: existing data becomes the first workspace, owned by `OWNER_EMAIL` (or the previous owner).

### Running the Tests
```
python -m unittest discover -s tests -v
```
The tests use a throwaway database, never read `.env` and never call Groq. They check that families cannot see or
change each other's data, plus security (cross-site requests, headers, rate limits, dev login in production),
item editing, exports and account deletion. To run them against Postgres, set `TEST_DATABASE_URL` to an **empty,
throwaway** database (it is wiped first).

### Project Architecture
* **app.py:** The main Flask server. It routes web traffic, handles API requests from the frontend, and manages file uploads.

* **llm_extractor.py:** The extraction engine. It handles image compression, PDF slicing, and formats the strict multimodal prompts sent to Groq's Vision API to turn messy documents into clean JSON.

* **chatbot.py:** The conversational brain. It manages chat history, formats overall database statistics, and dynamically attaches receipt images to the prompt if the user mentions a specific item.

* **database.py:** Manages the local SQLite database creation (currently v5), data insertions, deletions, and active warranty analytics.

**templates/index.html:** The single-page frontend UI, featuring Bootstrap 5 styling, interactive tabs, and a rich post-upload summary modal.

### How to Use
* **Upload Tab:** Select a receipt image or PDF and click "Scan receipt". The AI reads every product on it (fees like delivery are left out), then shows a review screen to fix anything and untick what you don't want before saving. Items already in your inventory are unticked automatically.

* **Inventory Tab:** Search, filter by category or warranty status, and sort your items. Each item's ⋯ menu lets you edit it, see its service history, open or download the receipt, attach, replace or re-read the warranty card (which sets the real warranty length), or delete it.

* **Assistant Tab:** Chat naturally with your Digital Twin.

* **Ask broad questions:** "What is my total spend?" or "How many items do I have?"

* **Ask specific questions:** "Look at the receipt for my Kettle, what was the exact tax amount?" or "Did my phone come with a return policy?"
