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

### Family Workspaces
Anyone with a Google account can sign in. Each family gets its own private **workspace** with its own items, receipts, settings and members:

1. A new person signs in and is offered to **create a workspace** (they become its owner).
2. The owner (or an admin) invites family members by Google email from **Settings → Workspace & Members**.
3. Invited people join automatically the next time they sign in. Someone can belong to several workspaces and switch between them from the menu in the top-right corner.

Roles: **Owner** (everything, including renaming the workspace and adding admins), **Admin** (delete items, change settings, invite members), **Member** (view, upload receipts, chat). Chat history is private to each person, even inside a shared workspace.

Upgrading from a version without workspaces is automatic: existing data becomes the first workspace, owned by `OWNER_EMAIL` (or the previous owner).

### Running the Tests
```
python -m unittest discover -s tests -v
```
The tests use a throwaway database and never call Groq. They check that families cannot see or change each other's data.

### Project Architecture
* **app.py:** The main Flask server. It routes web traffic, handles API requests from the frontend, and manages file uploads.

* **llm_extractor.py:** The extraction engine. It handles image compression, PDF slicing, and formats the strict multimodal prompts sent to Groq's Vision API to turn messy documents into clean JSON.

* **chatbot.py:** The conversational brain. It manages chat history, formats overall database statistics, and dynamically attaches receipt images to the prompt if the user mentions a specific item.

* **database.py:** Manages the local SQLite database creation (currently v5), data insertions, deletions, and active warranty analytics.

**templates/index.html:** The single-page frontend UI, featuring Bootstrap 5 styling, interactive tabs, and a rich post-upload summary modal.

### How to Use
* **Upload Tab:** Select a receipt image or PDF and click "Scan & Save". The AI will process the document in seconds and display a rich summary popup containing the exact invoice number, store name, and warranty duration.

* **Inventory Tab:** View all your digitized assets in a clean table. Check warranty statuses (Active/Expired), download original documents, or delete records.

* **Assistant Tab:** Chat naturally with your Digital Twin.

* **Ask broad questions:** "What is my total spend?" or "How many items do I have?"

* **Ask specific questions:** "Look at the receipt for my Kettle, what was the exact tax amount?" or "Did my phone come with a return policy?"
