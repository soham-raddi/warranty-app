# Digital Twin | Smart Inventory & Warranty Manager

An AI-powered, local-first inventory management system that acts as a "Digital Twin" for your physical assets. 

This application allows you to digitize receipts and invoices (both images and multi-page PDFs). It automatically extracts structured product data using state-of-the-art Large Language Models (LLMs) and allows you to query your inventory using an intelligent, multimodal chatbot that remembers your context and can "see" your uploaded documents.

---

## Features

* **Smart Digitization:** Drag-and-drop receipt images (JPG/PNG) or multi-page PDF invoices.
* **Universal Data Extraction:** Powered by Groq's lightning-fast `meta-llama/llama-4-scout-17b-16e-instruct` vision model. It intelligently ignores shipping fees or platform charges and extracts exact pricing, warranty info, and invoice numbers for your primary purchases.
* **Conversational AI Assistant:** A chatbot (powered by `llama-3.3-70b-versatile`) that remembers your chat history. It uses fuzzy matching to automatically pull up the relevant receipt image from your hard drive to answer highly specific questions about the fine print.
* **Auto-Compression Engine:** Built-in image compression and PDF slicing (via Pillow and PyMuPDF) ensures massive smartphone photos and long documents are processed in milliseconds without crashing the API.
* **Local Privacy First:** Fast, local SQLite storage (`inventory.db`) ensures your personal financial data stays securely on your machine.
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
Create a file named .env in the root directory of the project. Add your Groq API key to this file:

```
Code snippet
GROQ_API_KEY=your_actual_api_key_here
```

### 5. Run the Application
Start the Flask development server:

```
python app.py
```
Open your web browser and navigate to http://127.0.0.1:5000/.
(Note: The application will automatically generate the inventory.db database and a static/uploads/ folder upon its first run.)

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
