import os
import json
import sys
import subprocess
import urllib.request
import urllib.error

def get_fresh_access_token():
    try:
        decrypt_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'decrypt_token.js')
        res = subprocess.run(["node", decrypt_script], capture_output=True, text=True, check=True)
        tokens = json.loads(res.stdout.strip())
        return tokens.get("main-account", {}).get("token", {}).get("accessToken")
    except Exception as e:
        print(f"Error fetching decrypted token: {e}")
        return None

def append_text_to_doc(doc_id, text_to_append):
    access_token = get_fresh_access_token()
    if not access_token:
        print("Failed to get token.")
        return False
        
    # 1. Get current doc metadata to find end index
    get_url = f"https://docs.googleapis.com/v1/documents/{doc_id}"
    req = urllib.request.Request(get_url, headers={"Authorization": f"Bearer {access_token}"})
    
    try:
        with urllib.request.urlopen(req) as res:
            doc_data = json.loads(res.read().decode('utf-8'))
            body = doc_data.get("body", {})
            end_index = body.get("content", [])[-1].get("endIndex", 1)
    except Exception as e:
        print(f"Error fetching doc {doc_id}: {e}")
        return False
        
    # 2. Prepare append update request
    # Google Doc body ends with an implicit newline. 
    # To append to the end, we insert at end_index - 1.
    target_index = max(1, end_index - 1)
    
    requests = [{
        "insertText": {
            "location": {
                "index": target_index
            },
            "text": text_to_append
        }
    }]
    
    update_url = f"https://docs.googleapis.com/v1/documents/{doc_id}:batchUpdate"
    body_data = json.dumps({"requests": requests}).encode('utf-8')
    
    update_req = urllib.request.Request(
        update_url, 
        data=body_data, 
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
    )
    
    try:
        with urllib.request.urlopen(update_req) as res:
            print(f"Successfully appended to document {doc_id}")
            return True
    except urllib.error.HTTPError as he:
        print(f"HTTP Error updating {doc_id}: {he.code} - {he.read().decode('utf-8', errors='ignore')}")
        return False
    except Exception as e:
        print(f"Error updating doc {doc_id}: {e}")
        return False

if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Usage: python append_doc.py <doc_id> <text_file_path>")
        sys.exit(1)
        
    doc_id = sys.argv[1]
    file_path = sys.argv[2]
    
    with open(file_path, 'r', encoding='utf-8') as f:
        text_content = f.read()
        
    append_text_to_doc(doc_id, text_content)
