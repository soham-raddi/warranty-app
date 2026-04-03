from google import genai
from dotenv import load_dotenv
from database import get_all_appliances
import datetime
import os
load_dotenv()
API_KEY = os.getenv("GEMINI_API_KEY") 

# Initialize the new Client
client = genai.Client(api_key=API_KEY)

def ask_digital_twin(user_message, chat_history):
    columns, rows = get_all_appliances()
    
    # NEW: Get the exact current date so the AI can do warranty math!
    today = datetime.date.today().strftime("%B %d, %Y")
    
    db_context = "User's current inventory:\n"
    if not rows:
        db_context += "Empty.\n"
    else:
        for r in rows:
            # Indices match our V4 database table columns
            db_context += f"- Item: {r[1]} (Brand: {r[2]}), Bought: {r[6]}, Price paid: {r[9]}, Store: {r[11]}, Warranty text: {r[13]}\n"
            
    system_prompt = f"""
    You are the 'Digital Twin Assistant', an AI that helps the user manage their purchased items.
    Today's exact date is: {today}. 
    
    Here is the exact database of what the user owns:
    {db_context}
    
    Your Extended Capabilities:
    1. WARRANTY MATH: Compare the 'Bought' date to today's date ({today}) to tell the user if a warranty is expired. Assume standard 1-year warranty unless the 'Warranty text' says otherwise.
    2. FINANCIALS: You can add up the prices to tell the user their total spending if they ask.
    3. SEARCH: Tell the user exactly where they bought an item.
    4. MISSING ITEMS: If they ask about something not in the list, tell them you don't have a record of it yet.
    
    Keep responses friendly, highly accurate, and concise. Do not use Markdown tables.
    """
    
    full_prompt = system_prompt + "\n\nChat History:\n"
    for msg in chat_history:
        full_prompt += f"{msg['role'].capitalize()}: {msg['content']}\n"
        
    full_prompt += f"\nUser: {user_message}\nAI:"
    
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=full_prompt
        )
        return response.text
    except Exception as e:
        # This will show up in the chat window if something crashes!
        return f"System Alert: I encountered a connection error. ({str(e)})"