# pip install easyocr opencv-python
import easyocr
import cv2

# Initialize the reader once (it will download weights on the first run)
reader = easyocr.Reader(['en'], gpu=False) # Set gpu=True if you have a compatible NVIDIA GPU

def extract_text_from_image(image_path):
    """Reads an image and returns the raw text."""
    # Read the image using OpenCV
    img = cv2.imread(image_path)
    
    # Optional: Convert to grayscale to help the OCR
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    
    # Extract text
    results = reader.readtext(gray, detail=0) # detail=0 returns just the text, no bounding boxes
    
    # Join the list of strings into one big block of text
    raw_text = "\n".join(results)
    return raw_text

# --- Test it locally ---
if __name__ == "__main__":
    # Put a test receipt image in your folder and rename it to 'test_receipt.jpg'
    # text = extract_text_from_image("test_receipt.jpg")
    # print(text)
    pass